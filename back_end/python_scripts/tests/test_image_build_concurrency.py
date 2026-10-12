"""Coordinator regressions with real files and controlled runtime interleavings.

Run with Python 3.14. AST loading avoids importing the application configuration
or starting singleton services; the complete coordinator class remains intact.
Docker/Apptainer commands are replaced at their external-process boundary.
"""
from __future__ import annotations

import ast
import asyncio
import importlib.util
import json
import os
import stat
import subprocess
import sys
import tempfile
import threading
import types
import unittest
from pathlib import Path
from unittest.mock import patch


SERVER = Path(__file__).resolve().parents[2] / "server"


class Progress:
    def set_phase(self, *arguments):
        pass

    def append(self, *arguments):
        pass


class Worker:
    def __init__(self, config):
        self.size = 0

    async def disk_usage(self):
        return self.size

    async def prune(self, target):
        self.size = min(self.size, target)


def load_coordinator():
    module = types.ModuleType("image_build_coordinator_fixture")
    sys.modules[module.__name__] = module
    module.__dict__.update({
        "BuildProgress": Progress,
        "BuildKitClient": Worker,
        "ImageBuildError": type("ImageBuildError", (RuntimeError,), {}),
        "is_local_mode": False,
        "magnus_config": {},
        "write_json": lambda path, data: path.write_text(json.dumps(data), encoding="utf-8"),
    })
    specification = importlib.util.spec_from_file_location(
        "scratch_windows_fixture",
        SERVER / "_resource_manager/_scratch_windows.py",
    )
    windows_security = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(windows_security)
    module._prepare_windows_scratch_root = windows_security.prepare_private_directory
    for filename, selected in (
        (SERVER / "_size_utils.py", {"_parse_size_string"}),
        (SERVER / "_resource_manager/_helpers.py", {"_run_blocking_io"}),
        (SERVER / "_resource_manager/_builds.py", None),
    ):
        tree = ast.parse(filename.read_text(encoding="utf-8"), filename=str(filename))
        tree.body = [
            node for node in tree.body
            if (
                isinstance(node, (ast.Import, ast.ImportFrom))
                and not (isinstance(node, ast.ImportFrom) and node.level)
            ) or (
                not isinstance(node, (ast.Import, ast.ImportFrom))
                and (selected is None or getattr(node, "name", None) in selected)
            )
        ]
        exec(compile(tree, str(filename), "exec"), module.__dict__)
        if selected == {"_run_blocking_io"}:
            module.blocking_io = module._run_blocking_io
    return module


class ImageBuildConcurrencyTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        # ACL tests need a trusted parent. A deliberately shared user-profile
        # ACL must not be weakened just to make these security checks pass.
        security_directory = (
            Path.home().anchor
            if os.name == "nt" and self._testMethodName.startswith("test_private_scratch_")
            else None
        )
        self.temporary = tempfile.TemporaryDirectory(prefix="magnus-regression-", dir=security_directory)
        self.base = Path(self.temporary.name)
        self.module = load_coordinator()
        self.module.magnus_config = {
            "server": {"root": str(self.base / "server"), "ephemeral_root": str(self.base / "ephemeral")},
            "execution": {"image_build": {
                "enabled": True,
                "timeout_seconds": 10,
                "cache_size": "10M",
                "min_free_space": "0",
            }},
        }
        self.manager = self.module.ImageBuildManager()
        self.manager.root.mkdir(parents=True)
        self.manager.temporary_root.mkdir(parents=True)
        self.directory = self.manager.temporary_root / "build-fixture"
        self.manager._prepare_directory(self.directory, "FROM scratch")
        self.archive = self.directory / "image.tar"
        self.archive.write_bytes(b"archive")
        self.operation = self.module._BuildOperation(key="b" * 64, dockerfile="FROM scratch")
        self.operation.waiters["job"] = self.base / "diagnostic"
        self.manager.jobs["job"] = self.operation
        self.pending = f"magnus-build/{self.manager.namespace}:pending-build-fixture"
        self.converted = asyncio.Event()
        self.finish_conversion = asyncio.Event()
        self.module.run_command = self.command
        self.tag_images = {}
        self.docker_digest = "sha256:" + "c" * 64

    def tearDown(self):
        self.temporary.cleanup()
        sys.modules.pop(self.module.__name__, None)

    async def command(self, arguments, **keywords):
        if arguments[:2] == ["apptainer", "build"]:
            self.conversion_arguments = arguments
            self.converted.set()
            await self.finish_conversion.wait()
            Path(arguments[-2]).write_bytes(b"SIF bytes")
        elif arguments[:2] == ["docker", "load"]:
            self.tag_images[self.pending] = self.docker_digest
            self.converted.set()
            await self.finish_conversion.wait()
        elif arguments[:3] == ["docker", "image", "inspect"]:
            digest = self.tag_images[arguments[-1]]
            return json.dumps({"Id": digest, "Size": 64})
        elif arguments[:3] == ["docker", "image", "ls"]:
            tag = arguments[-1].removeprefix("reference=")
            return self.tag_images.get(tag, "")
        elif arguments[:2] == ["docker", "tag"]:
            self.tag_images[arguments[-1]] = self.tag_images[arguments[-2]]
        elif arguments[:3] == ["docker", "image", "rm"]:
            self.tag_images.pop(arguments[-1], None)
        return ""

    async def import_image(self):
        return await self.manager._import_archive(self.operation, self.directory, self.archive, self.pending)

    async def docker_inventory(self):
        inventory = {}
        for tag, digest in self.tag_images.items():
            value = inventory.setdefault(digest, {"size": 64, "tags": []})
            value["tags"].append(tag)
        return inventory

    async def use_docker(self):
        self.module.is_local_mode = True
        self.manager._docker_inventory = self.docker_inventory

    async def test_cache_hit_does_not_wait_for_conversion(self):
        warm = self.manager.root / "warm-source"
        warm.write_bytes(b"warm image")
        digest = self.module._hash_file(warm)
        path = self.manager._sif_path(digest)
        warm.rename(path)
        cached = self.module._BuildOperation(key="a" * 64, dockerfile="warm")
        cached.waiters["warm-job"] = self.base / "warm-diagnostic"
        self.manager.results[cached.key] = {
            "digest": digest, "sif_path": str(path), "size_bytes": path.stat().st_size,
        }
        importing = asyncio.create_task(self.import_image())
        await self.converted.wait()
        try:
            result = await asyncio.wait_for(self.manager._cached_result(cached), 2)
            self.assertEqual(result["digest"], digest)
            self.assertFalse(importing.done())
        finally:
            self.finish_conversion.set()
            await importing
        self.assertIn("-processors 2 -mem 1024M", self.conversion_arguments)

    async def test_monitor_cancels_conversion_before_scratch_cleanup(self):
        importing = asyncio.create_task(self.import_image())
        self.operation.task = importing
        await self.converted.wait()
        (self.directory / "oversized").write_bytes(b"x" * 1024)
        self.manager.scratch_budget = 100

        async def flush(operation):
            pass

        self.manager._flush = flush
        monitor = asyncio.create_task(self.manager._monitor(self.operation))
        with self.assertRaises(self.module._CacheBudgetError):
            await asyncio.wait_for(monitor, 2)
        with self.assertRaises(asyncio.CancelledError):
            await importing
        self.assertFalse(self.finish_conversion.is_set())
        self.assertTrue((self.directory / "oversized").exists())

    async def test_slow_cache_validation_releases_publication_lock_but_keeps_pin(self):
        path = self.manager.root / "source"
        path.write_bytes(b"warm image")
        digest = self.module._hash_file(path)
        target = self.manager._sif_path(digest)
        path.rename(target)
        self.manager.results[self.operation.key] = {
            "digest": digest, "sif_path": str(target), "size_bytes": target.stat().st_size,
        }
        checking = threading.Event()
        release_check = threading.Event()

        def held_validation(result):
            checking.set()
            release_check.wait(5)
            return True

        self.manager.is_available = held_validation
        cached = asyncio.create_task(self.manager._cached_result(self.operation))
        self.assertTrue(await asyncio.to_thread(checking.wait, 2))
        try:
            await asyncio.wait_for(self.manager._collect_cache(), 2)
            self.assertEqual(self.manager.pins["job"], digest)
            self.assertTrue(target.exists())
            self.assertFalse(cached.done())
        finally:
            release_check.set()
            await cached

    async def test_incoming_copy_is_protected_from_gc(self):
        copying = threading.Event()
        release_copy = threading.Event()
        original_copy = self.module.shutil.copyfile

        def held_copy(source, target):
            Path(target).write_bytes(b"partial")
            copying.set()
            release_copy.wait(5)
            return original_copy(source, target)

        self.finish_conversion.set()
        with patch.object(self.module.shutil, "copyfile", held_copy):
            importing = asyncio.create_task(self.import_image())
            self.assertTrue(await asyncio.to_thread(copying.wait, 2))
            incoming = next(iter(self.manager.incoming_paths))
            stale = self.manager.root / ".stale.tmp"
            stale.write_bytes(b"stale")
            try:
                async with self.manager.maintenance_lock:
                    await self.manager._remove_orphans()
                self.assertTrue(incoming.exists())
                self.assertFalse(stale.exists())
            finally:
                release_copy.set()
                result = await importing
        self.assertTrue(Path(result["sif_path"]).exists())
        self.assertEqual(self.manager.incoming_paths, set())

    async def test_cancelled_copy_joins_writer_before_removing_reservation(self):
        copying = threading.Event()
        release_copy = threading.Event()
        original_copy = self.module.shutil.copyfile

        def held_copy(source, target):
            Path(target).write_bytes(b"partial")
            copying.set()
            release_copy.wait(5)
            return original_copy(source, target)

        self.finish_conversion.set()
        with patch.object(self.module.shutil, "copyfile", held_copy):
            importing = asyncio.create_task(self.import_image())
            self.assertTrue(await asyncio.to_thread(copying.wait, 2))
            incoming = next(iter(self.manager.incoming_paths))
            importing.cancel()
            await asyncio.sleep(0)
            importing.cancel()
            await asyncio.sleep(0)
            self.assertFalse(importing.done())
            self.assertTrue(incoming.exists())
            release_copy.set()
            with self.assertRaises(asyncio.CancelledError):
                await importing
        self.assertFalse(incoming.exists())
        self.assertEqual(self.manager.incoming_paths, set())
        self.assertEqual(self.manager.results, {})

    async def test_pending_docker_tag_survives_gc_then_cancellation_removes_it(self):
        await self.use_docker()
        importing = asyncio.create_task(self.import_image())
        await self.converted.wait()
        stale = f"magnus-build/{self.manager.namespace}:pending-abandoned"
        self.tag_images[stale] = self.docker_digest
        async with self.manager.maintenance_lock:
            await self.manager._remove_orphans()
        self.assertIn(self.pending, self.tag_images)
        self.assertNotIn(stale, self.tag_images)
        importing.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await importing
        self.assertEqual(self.tag_images, {})
        self.assertEqual(self.manager.pending_tags, set())

    async def test_failed_index_write_rolls_back_sif_and_waiter_pins(self):
        real_save = self.manager._save_index
        saves = 0

        async def fail_first_save():
            nonlocal saves
            saves += 1
            await real_save()
            if saves == 1:
                raise OSError("simulated failure after atomic index write")

        self.manager._save_index = fail_first_save
        self.finish_conversion.set()
        with self.assertRaisesRegex(OSError, "simulated failure"):
            await self.import_image()
        self.assertEqual(self.manager.results, {})
        self.assertEqual(self.manager.pins, {})
        self.assertEqual(list(self.manager.root.glob("*.sif")), [])
        self.assertEqual(json.loads((self.manager.root / "index.json").read_text())["results"], {})

    async def test_failed_docker_publication_keeps_another_jobs_canonical_image(self):
        await self.use_docker()
        canonical = self.manager._docker_tag(self.docker_digest)
        self.tag_images[canonical] = self.docker_digest
        self.manager.pins["existing-job"] = self.docker_digest
        real_save = self.manager._save_index
        calls = 0

        async def fail_first_save():
            nonlocal calls
            calls += 1
            if calls == 1:
                raise OSError("index unavailable")
            await real_save()

        self.manager._save_index = fail_first_save
        self.finish_conversion.set()
        with self.assertRaises(OSError):
            await self.import_image()
        self.assertEqual(self.tag_images, {canonical: self.docker_digest})
        self.assertEqual(self.manager.pins, {"existing-job": self.docker_digest})

    async def test_peak_scratch_is_checked_before_cleanup(self):
        self.manager.scratch_budget = self.module._directory_size(self.directory) + 1
        self.finish_conversion.set()
        with self.assertRaisesRegex(self.module._CacheBudgetError, "scratch budget"):
            await self.import_image()
        self.assertTrue((self.directory / "image.sif").exists())
        self.assertEqual(self.manager.results, {})

    async def test_cancelled_index_write_restores_previous_result(self):
        previous = {"digest": "sha256:" + "d" * 64}
        self.manager.results[self.operation.key] = previous
        real_save = self.manager._save_index
        saving = asyncio.Event()

        async def held_first_save():
            await real_save()
            if not saving.is_set():
                saving.set()
                await asyncio.Future()

        self.manager._save_index = held_first_save
        self.finish_conversion.set()
        importing = asyncio.create_task(self.import_image())
        await saving.wait()
        importing.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await importing
        self.assertIs(self.manager.results[self.operation.key], previous)
        self.assertEqual(self.manager.pins, {})
        self.assertIsNone(self.operation.result)
        self.assertEqual(list(self.manager.root.glob("*.sif")), [])
        self.assertEqual(
            json.loads((self.manager.root / "index.json").read_text())["results"][self.operation.key],
            previous,
        )

    async def test_verified_sif_detects_replacement_with_same_size(self):
        source = self.manager.root / "source"
        source.write_bytes(b"valid")
        digest = self.module._hash_file(source)
        target = self.manager._sif_path(digest)
        source.rename(target)
        self.assertTrue(self.manager._verify_sif(target, digest, 5))
        replacement = self.manager.root / "replacement"
        replacement.write_bytes(b"other")
        replacement.replace(target)
        self.assertFalse(self.manager._verify_sif(target, digest, 5))

    async def test_budget_counts_legacy_scratch_and_ignores_its_empty_volume(self):
        config = self.module.magnus_config["execution"]["image_build"]
        config["scratch_root"] = str(self.base / "fast-volume")
        manager = self.module.ImageBuildManager()
        manager.temporary_root.mkdir(parents=True)
        (manager.temporary_root / "new-file").write_bytes(b"x" * 10)
        old_size = self.module._directory_size(manager.legacy_temporary_root)
        self.assertEqual(manager._local_usage()[2], old_size + 10)
        self.module.shutil.rmtree(self.directory)
        disk_usage = self.module.shutil.disk_usage
        checked = []

        def record_disk_usage(path):
            checked.append(path)
            return disk_usage(path)

        with patch.object(self.module.shutil, "disk_usage", record_disk_usage):
            self.assertEqual(manager._local_usage()[2], 10)
        self.assertNotIn(manager.legacy_temporary_root, checked)

    async def test_configured_scratch_is_partitioned_and_recovers_legacy_lease(self):
        config = self.module.magnus_config["execution"]["image_build"]
        config["scratch_root"] = str(self.base / "fast-volume")
        manager = self.module.ImageBuildManager()
        self.assertEqual(manager.temporary_root.parent, Path(config["scratch_root"]))
        self.assertEqual(manager.temporary_root.name, f"magnus-build-{manager.namespace}")
        manager.temporary_root.mkdir(parents=True)
        with patch.object(self.module, "_process_exists", return_value=True):
            with self.assertRaisesRegex(self.module.ImageBuildError, "Another backend"):
                manager._recover_scratch()
        with patch.object(self.module, "_process_exists", return_value=False):
            self.assertFalse(manager._recover_scratch())
        self.assertFalse(self.directory.exists())
        self.module.magnus_config["server"]["root"] = str(self.base / "other-station")
        other = self.module.ImageBuildManager()
        self.assertNotEqual(manager.temporary_root, other.temporary_root)

    async def test_recovery_retains_live_child_and_its_incoming_file(self):
        (self.directory / "process.json").write_text(json.dumps({"state": "running", "pid": 42}))
        incoming = self.manager.root / ".unfinished.tmp"
        incoming.write_bytes(b"unfinished")
        with patch.object(self.module, "_process_exists", side_effect=lambda pid: pid == 42):
            self.assertTrue(self.manager._recover_scratch())
        self.assertTrue(self.directory.exists())
        self.assertTrue(incoming.exists())
        with patch.object(self.module, "_process_exists", return_value=False):
            self.assertFalse(self.manager._recover_scratch())
        async with self.manager.maintenance_lock:
            await self.manager._remove_orphans()
        self.assertFalse(self.directory.exists())
        self.assertFalse(incoming.exists())

    @unittest.skipUnless(os.name == "nt", "Windows junction semantics")
    async def test_private_scratch_rejects_precreated_junction(self):
        target = self.base / "junction-target"
        target.mkdir()
        marker = target / "keep"
        marker.write_bytes(b"unrelated content")
        junction = self.base / "scratch-junction"
        subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(junction), str(target)],
            check=True,
            capture_output=True,
        )
        try:
            with self.assertRaisesRegex(self.module.ImageBuildError, "linked directory"):
                self.module._prepare_scratch_root(junction)
            self.assertEqual(marker.read_bytes(), b"unrelated content")
        finally:
            junction.rmdir()

    @unittest.skipUnless(os.name == "nt", "Windows configured parent junction")
    async def test_private_scratch_resolves_configured_parent_junction(self):
        target = self.base / "parent-target"
        target.mkdir()
        junction = self.base / "configured-parent"
        subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(junction), str(target)],
            check=True,
            capture_output=True,
        )
        try:
            prepared = self.module._prepare_scratch_root(junction / "scratch")
            self.assertEqual(prepared, target / "scratch")
            self.assertTrue(prepared.is_dir())
        finally:
            junction.rmdir()

    @unittest.skipUnless(os.name == "nt", "Windows DACL semantics")
    async def test_private_scratch_rejects_ordinary_leaf_with_public_read_grant(self):
        leaf = self.base / "public-leaf"
        leaf.mkdir(mode=0o700)
        marker = leaf / "keep"
        marker.write_bytes(b"unchanged")
        subprocess.run(
            ["icacls", str(leaf), "/grant", "*S-1-1-0:(R)"],
            check=True,
            capture_output=True,
        )
        with self.assertRaisesRegex(self.module.ImageBuildError, "grants access to another account"):
            self.module._prepare_scratch_root(leaf)
        self.assertEqual(marker.read_bytes(), b"unchanged")

    @unittest.skipUnless(os.name == "nt", "Windows DACL semantics")
    async def test_private_scratch_rejects_parent_delete_child_grant(self):
        parent = self.base / "shared-parent"
        parent.mkdir(mode=0o700)
        subprocess.run(
            ["icacls", str(parent), "/grant", "*S-1-1-0:(DC)"],
            check=True,
            capture_output=True,
        )
        with self.assertRaisesRegex(self.module.ImageBuildError, "grants access to another account"):
            self.module._prepare_scratch_root(parent / "scratch")
        self.assertFalse((parent / "scratch").exists())

    @unittest.skipUnless(os.name == "nt", "Windows DACL semantics")
    async def test_private_scratch_allows_parent_inherit_only_grant(self):
        parent = self.base / "inherit-only-parent"
        parent.mkdir(mode=0o700)
        subprocess.run(
            ["icacls", str(parent), "/grant", "*S-1-1-0:(OI)(CI)(IO)(F)"],
            check=True,
            capture_output=True,
        )
        leaf = self.module._prepare_scratch_root(parent / "scratch")
        self.assertTrue(leaf.is_dir())
        # The private leaf created by Python does not inherit that public grant.
        self.assertEqual(self.module._prepare_scratch_root(leaf), leaf)

    @unittest.skipUnless(os.name == "nt", "Windows directory sharing semantics")
    async def test_private_scratch_directory_handle_prevents_replacement(self):
        parent = self.base / "held-parent"
        parent.mkdir(mode=0o700)
        security_type = self.module._prepare_windows_scratch_root.__globals__["_WindowsSecurity"]
        security = security_type()
        handle = security.open_directory(parent)
        try:
            with self.assertRaises(OSError):
                parent.rename(self.base / "replaced-parent")
        finally:
            security.kernel.CloseHandle(handle)
        parent.rename(self.base / "replaced-parent")

    @unittest.skipUnless(os.name == "nt", "Windows owner validation")
    async def test_private_scratch_requires_current_account_as_leaf_owner(self):
        leaf = self.base / "owned-leaf"
        leaf.mkdir(mode=0o700)
        security_type = self.module._prepare_windows_scratch_root.__globals__["_WindowsSecurity"]
        security = security_type()
        handle = security.open_directory(leaf)
        try:
            security.validate_directory(handle, leaf, private=True)
            security.user_sid = "S-1-1-0"
            with self.assertRaisesRegex(PermissionError, "untrusted owner"):
                security.validate_directory(handle, leaf, private=True)
        finally:
            security.kernel.CloseHandle(handle)

    @unittest.skipUnless(os.name == "posix", "POSIX ownership and sticky directory semantics")
    async def test_private_scratch_rejects_precreated_symlink(self):
        target = self.base / "symlink-target"
        target.mkdir(mode=0o755)
        before = target.stat().st_mode
        link = self.base / "scratch-link"
        link.symlink_to(target, target_is_directory=True)
        with self.assertRaises(OSError):
            self.module._prepare_scratch_root(link)
        self.assertEqual(target.stat().st_mode, before)

    @unittest.skipUnless(os.name == "posix", "POSIX ownership and sticky directory semantics")
    async def test_private_scratch_rejects_nonsticky_shared_parent(self):
        parent = self.base / "unsafe-shared"
        parent.mkdir()
        parent.chmod(0o777)
        with self.assertRaisesRegex(self.module.ImageBuildError, "untrusted writable parent"):
            self.module._prepare_scratch_root(parent / "scratch")
        self.assertFalse((parent / "scratch").exists())

    @unittest.skipUnless(os.name == "posix", "POSIX ownership and sticky directory semantics")
    async def test_private_scratch_allows_sticky_parent_and_sets_private_leaf(self):
        parent = self.base / "sticky-shared"
        parent.mkdir()
        parent.chmod(0o1777)
        leaf = self.module._prepare_scratch_root(parent / "scratch")
        self.assertEqual(stat.S_IMODE(leaf.stat().st_mode), 0o700)
        self.assertEqual(leaf.stat().st_uid, os.geteuid())

    @unittest.skipUnless(os.name == "posix", "POSIX ownership")
    async def test_private_scratch_rejects_leaf_owned_by_another_account(self):
        leaf = self.base / "foreign-leaf"
        leaf.mkdir()
        original = os.fstat
        identifier = leaf.stat().st_ino

        def foreign_leaf(descriptor):
            information = original(descriptor)
            if information.st_ino == identifier:
                return types.SimpleNamespace(
                    st_uid=os.geteuid() + 1,
                    st_mode=information.st_mode,
                    st_ino=information.st_ino,
                    st_dev=information.st_dev,
                )
            return information

        with patch.object(self.module.os, "fstat", foreign_leaf):
            with self.assertRaisesRegex(self.module.ImageBuildError, "belongs to another account"):
                self.module._prepare_scratch_root(leaf)


if __name__ == "__main__":
    unittest.main()
