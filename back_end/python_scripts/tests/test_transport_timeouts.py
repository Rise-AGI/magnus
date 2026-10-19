"""Deadline tests without SSH, authentication or any external endpoint."""
from __future__ import annotations

import ast
from pathlib import Path
import subprocess
import sys
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from test_gpu_resource_query import load_queries


SERVER = Path(__file__).resolve().parents[2] / "server"


def load_transport():
    namespace = {"__name__": __name__}
    for name in ("_ssh_auto_connect.py", "_transport.py"):
        path = SERVER / "_slurm_manager" / name
        tree = ast.parse(path.read_text(encoding="utf-8"))
        tree.body = [node for node in tree.body if not isinstance(node, ast.ImportFrom) or not node.level]
        exec(compile(tree, str(path), "exec"), namespace)
    return namespace


class TransportDeadlineTests(unittest.TestCase):
    def test_local_timeout_reaps_actual_child(self):
        namespace = load_transport()
        transport = namespace["_LocalTransport"]()
        children = []
        original = subprocess.Popen
        def capture(*args, **kwargs):
            child = original(*args, **kwargs)
            children.append(child)
            return child
        started = time.monotonic()
        with patch.object(subprocess, "Popen", capture), self.assertRaises(subprocess.TimeoutExpired):
            transport.run([sys.executable, "-c", "import time; time.sleep(30)"], timeout=0.1)
        self.assertLess(time.monotonic() - started, 3)
        self.assertEqual(len(children), 1)
        self.assertIsNotNone(children[0].poll())

    def test_ssh_command_gets_only_budget_left_after_master_check(self):
        namespace = load_transport()
        clock = SimpleNamespace(now=10)
        calls = []
        namespace["time"] = SimpleNamespace(monotonic=lambda: clock.now)
        def ensure(*args, deadline=None):
            self.assertEqual(deadline, 15)
            clock.now += 2
        def run(command, **kwargs):
            calls.append((command, kwargs))
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        namespace["ensure_control_master"] = ensure
        namespace["subprocess"] = SimpleNamespace(run=run)
        transport = namespace["_SshControlMasterTransport"]("socket", "host", "user", auto_connect={})
        transport.run(["squeue"], timeout=5)
        self.assertEqual(calls[0][1]["timeout"], 3)
        self.assertNotIn("-O", calls[0][0])

    def test_expired_auth_budget_never_launches_query(self):
        namespace = load_transport()
        clock = SimpleNamespace(now=0)
        namespace["time"] = SimpleNamespace(monotonic=lambda: clock.now)
        namespace["ensure_control_master"] = lambda *args, **kwargs: setattr(clock, "now", 6)
        namespace["subprocess"] = SimpleNamespace(run=lambda *args, **kwargs: self.fail("query launched after its deadline"))
        transport = namespace["_SshControlMasterTransport"]("socket", "host", "user", auto_connect={})
        with self.assertRaises(TimeoutError):
            transport.run(["squeue"], timeout=5)

    def test_auth_singleflight_lock_is_bounded_and_owned_lock_is_not_released(self):
        namespace = load_transport()
        namespace["master_alive"] = lambda *args, **kwargs: False
        namespace["_establish"] = lambda *args, **kwargs: self.fail("auth started while another owner holds the lock")
        lock = namespace["_establish_lock"]
        lock.acquire()
        try:
            started = time.monotonic()
            with self.assertRaises(TimeoutError):
                namespace["ensure_control_master"]("socket", "host", "user", {}, deadline=started+0.02)
            self.assertLess(time.monotonic() - started, 1)
            self.assertTrue(lock.locked())
        finally:
            lock.release()

    def test_master_probe_uses_remaining_budget(self):
        namespace = load_transport()
        calls = []
        namespace["time"] = SimpleNamespace(monotonic=lambda: 10)
        namespace["subprocess"] = SimpleNamespace(run=lambda *args, **kwargs: calls.append(kwargs) or SimpleNamespace(returncode=0))
        self.assertTrue(namespace["master_alive"]("socket", "host", "user", deadline=12))
        self.assertEqual(calls[0]["timeout"], 2)

    def test_authentication_expect_uses_shared_deadline_and_closes_child(self):
        namespace = load_transport()
        clock = SimpleNamespace(now=10)
        namespace["time"] = SimpleNamespace(monotonic=lambda: clock.now)
        namespace["_read_secret"] = lambda *args: "unused-test-value"
        namespace["os"] = SimpleNamespace(unlink=lambda path: None)
        closed = []
        waits = []
        def expect(patterns, timeout):
            waits.append(timeout)
            clock.now += 6
            return 1
        child = SimpleNamespace(expect=expect, close=lambda **kwargs: closed.append(kwargs), sendline=lambda value: self.fail("answer sent after deadline"))
        fake_pexpect = SimpleNamespace(spawn=lambda *args, **kwargs: child, EOF=object(), TIMEOUT=object())
        fake_pyotp = SimpleNamespace(TOTP=lambda secret: object())
        with patch.dict(sys.modules, {"pexpect": fake_pexpect, "pyotp": fake_pyotp}), self.assertRaises(TimeoutError):
            namespace["_establish"]("socket", "host", "user", {}, deadline=15)
        self.assertEqual(waits, [5])
        self.assertEqual(closed, [{"force": True}])

    def test_totp_window_wait_cannot_exceed_deadline(self):
        namespace = load_transport()
        namespace["time"] = SimpleNamespace(monotonic=lambda: 10, time=lambda: 29, sleep=lambda seconds: self.fail("wait exceeds deadline"))
        with self.assertRaises(TimeoutError):
            namespace["_totp_code_with_margin"](SimpleNamespace(interval=30, now=lambda: "unused"), deadline=11)

    def test_ordinary_command_default_has_no_new_timeout(self):
        namespace = load_transport()
        calls = []
        namespace["subprocess"] = SimpleNamespace(run=lambda *args, **kwargs: calls.append(kwargs) or object())
        namespace["_LocalTransport"]().run(["sbatch"], input="script")
        namespace["_SshControlMasterTransport"]("socket", "host", "user").run(["scancel", "123"])
        self.assertEqual([call["timeout"] for call in calls], [None, None])

    def test_empty_node_scope_never_queries_whole_cluster(self):
        module = load_queries()
        manager = module._ResourceQueryMixin()
        manager._transport = SimpleNamespace(run=lambda *args, **kwargs: self.fail("empty scope reached transport"))
        self.assertEqual(manager.get_all_running_tasks(node_names=[], _raise_on_error=True), [])

    def test_cold_cache_lock_wait_is_bounded_without_releasing_another_owner(self):
        path = SERVER / "routers" / "cluster.py"
        tree = ast.parse(path.read_text(encoding="utf-8"))
        function = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "_cached_slurm_view")
        namespace = load_transport()
        waits = []
        namespace.update(
            _slurm_view_cache={},
            _slurm_view_cache_lock=namespace["threading"].Lock(),
            _slurm_view_refresh_lock=SimpleNamespace(
                acquire=lambda **kwargs: waits.append(kwargs) or False,
                release=lambda: self.fail("released another refresh owner's lock"),
            ),
        )
        exec(compile(ast.Module(body=[function], type_ignores=[]), str(path), "exec"), namespace)
        with self.assertRaises(TimeoutError):
            namespace["_cached_slurm_view"]("cold", 5, lambda: self.fail("lock not acquired"))
        self.assertEqual(waits, [{"timeout": 30}])


if __name__ == "__main__":
    unittest.main()
