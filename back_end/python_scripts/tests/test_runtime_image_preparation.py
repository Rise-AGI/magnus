"""Run generated wrappers through process fixtures without a controller or Slurm."""
import ast
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

from runtime_support import RUNTIME_IMAGE, WRAPPER


SERVER = Path(__file__).resolve().parents[2] / "server"

RUNTIME = r'''#!/usr/bin/env python3
import json, os, pathlib, subprocess, sys, time
args = sys.argv[1:]
prefix = "SINGULARITY" if pathlib.Path(sys.argv[0]).name == "singularity" else "APPTAINER"
row = {"operation": args[0], "args": args, "node": os.environ.get("SLURM_NODEID"),
       "bind": os.environ.get(prefix + "_BIND"), "tmpdir": os.environ.get(prefix + "ENV_TMPDIR")}
fd = os.open(os.environ["TEST_EVENTS"], os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
os.write(fd, (json.dumps(row) + "\n").encode()); os.close(fd)
if args[0] == "build":
    if os.environ.get("TEST_BUILD_FAIL"):
        raise SystemExit(17)
    if os.environ.get("TEST_BUILD_SLEEP"):
        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
        pathlib.Path(os.environ["TEST_CHILD_PID"]).write_text(str(child.pid))
        time.sleep(60)
    destination = pathlib.Path(args[-2])
    destination.mkdir()
    (destination / "ready").write_text("complete")
elif args[0] == "exec":
    image = pathlib.Path(args[args.index("--pwd") + 2])
    if image.is_dir() and not (image / "ready").is_file():
        raise SystemExit(18)
    if not image.exists():
        raise SystemExit(19)
'''

SRUN = r'''#!/usr/bin/env python3
from concurrent.futures import ThreadPoolExecutor
import os, subprocess, sys
args = sys.argv[1:]
while args and args[0].startswith("--"):
    args.pop(0)
count = int(os.environ["TEST_RANKS"])
nodes = int(os.environ["TEST_NODES"])
def run(rank):
    environment = dict(os.environ, SLURM_PROCID=str(rank), SLURM_NODEID=str(rank // (count // nodes)))
    return subprocess.call(args, env=environment)
with ThreadPoolExecutor(max_workers=count) as pool:
    raise SystemExit(max(pool.map(run, range(count))))
'''

RUNNER = """
import os, runpy, sys
module = runpy.run_path(sys.argv[1], run_name='isolated_wrapper')
if os.environ.get('TEST_OTHER_OWNER'):
    original_stat = os.stat
    def stat(path, *args, **kwargs):
        result = original_stat(path, *args, **kwargs)
        if os.fspath(path) == os.path.dirname(sys.argv[1]):
            values = list(result)
            values[4] = os.geteuid() + 1
            return os.stat_result(values)
        return result
    os.stat = stat
module['main'].__globals__['_metrics_sidecar'] = lambda *args: None
module['main'].__globals__['_check_oom'] = lambda: (False, 0)
module['main']()
"""


@unittest.skipUnless(
    sys.platform.startswith("linux") and shutil.which("bash") and shutil.which("timeout"),
    "Slurm wrapper process checks require Linux, Bash and GNU timeout",
)
class RuntimeImagePreparationTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.bin = self.root / "bin"
        self.bin.mkdir()
        for name, source in (("apptainer", RUNTIME), ("singularity", RUNTIME), ("srun", SRUN)):
            path = self.bin / name
            path.write_text(source)
            path.chmod(0o755)
        self.image = self.root / "image with spaces.sif"
        self.image.write_bytes(b"fixture-image")

    def run_wrapper(
        self,
        *,
        name="job",
        mode="sandbox",
        runtime="singularity",
        nodes=2,
        tasks=4,
        timeout=5,
        extra_environment=None,
    ):
        work = self.root / (name + " with spaces")
        work.mkdir()
        (work / "repository").mkdir()
        generated = WRAPPER._build_wrapper_content(
            job_working_table=str(work),
            job_ephemeral_table=str(work),
            repo_dir=str(work / "repository"),
            sif_path=str(self.image),
            system_entry_command="",
            user_token="fixture",
            magnus_address="https://example.invalid",
            job_id=name,
            ephemeral_storage="1G",
            allow_root=False,
            entry_command="exit 0",
            effective_runner="fixture",
            container_runtime=runtime,
            node_count=nodes,
            tasks_per_node=tasks,
            mpi_type="pmi2",
            container_image_mode=mode,
            container_prepare_timeout_seconds=timeout,
        )
        ast.parse(generated, feature_version=(3, 6))
        script = work / "wrapper.py"
        script.write_text(generated)
        events = work / "events.jsonl"
        environment = {
            **os.environ,
            "PATH": str(self.bin) + os.pathsep + os.environ["PATH"],
            "TEST_EVENTS": str(events),
            "TEST_RANKS": str(nodes * tasks),
            "TEST_NODES": str(nodes),
            "TEST_CHILD_PID": str(work / "child.pid"),
            **(extra_environment or {}),
        }
        result = subprocess.run(
            [sys.executable, "-c", RUNNER, str(script)],
            env=environment,
            capture_output=True,
            text=True,
            timeout=20,
        )
        rows = [json.loads(line) for line in events.read_text().splitlines()] if events.exists() else []
        return result, rows, work

    def test_one_preparation_precedes_all_tasks_with_node_shared_temporary_paths(self):
        for runtime in ("singularity", "apptainer"):
            with self.subTest(runtime=runtime):
                result, rows, work = self.run_wrapper(name=runtime, runtime=runtime)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(rows[0]["operation"], "build")
                self.assertEqual(sum(row["operation"] == "build" for row in rows), 1)
                tasks = [row for row in rows if row["operation"] == "exec"]
                self.assertEqual(len(tasks), 8)
                images = {row["args"][row["args"].index("--pwd") + 2] for row in tasks}
                self.assertEqual(len(images), 1)
                self.assertEqual(len({row["bind"] for row in tasks}), 2)
                for row in tasks:
                    self.assertIn(str(work) + ":/magnus/workspace", row["bind"].split(","))
                    self.assertNotIn("--writable-tmpfs", row["args"])
                    self.assertNotIn("--overlay", row["args"])
                    self.assertEqual(row["tmpdir"], "/tmp")
                    self.assertIn(":" + "/magnus/.tmp", row["bind"])
                    paths = row["bind"].split(",")
                    temporary = next(item.split(":")[0] for item in paths if item.endswith(":/tmp"))
                    self.assertTrue(Path(temporary).is_dir())
                    self.assertIn("/" + row["node"] + "/tmp", temporary)
                self.assertTrue((work / ".magnus_success").exists())

    def test_sandbox_rejects_a_different_directory_owner_before_conversion(self):
        result, rows, work = self.run_wrapper(extra_environment={"TEST_OTHER_OWNER": "1"})
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("requires ownership", result.stderr)
        self.assertEqual(rows, [])
        self.assertFalse((work / ".magnus_success").exists())
        result, _, _ = self.run_wrapper(name="native", mode="native", extra_environment={"TEST_OTHER_OWNER": "1"})
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_single_task_uses_the_same_sandbox_and_temporary_directory_contract(self):
        result, rows, _ = self.run_wrapper(nodes=1, tasks=1)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual([row["operation"] for row in rows], ["build", "exec"])
        self.assertIn("/0/tmp:/tmp", rows[-1]["bind"])

    def test_native_mode_keeps_direct_sif_execution_and_overlay_behavior(self):
        for nodes, tasks in ((1, 1), (2, 4)):
            with self.subTest(nodes=nodes, tasks=tasks):
                result, rows, _ = self.run_wrapper(name=str(nodes), mode="native", nodes=nodes, tasks=tasks)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertNotIn("build", [row["operation"] for row in rows])
                for row in rows:
                    if row["operation"] == "exec":
                        self.assertIn(str(self.image), row["args"])
                        self.assertIn("--overlay" if nodes == 1 else "--writable-tmpfs", row["args"])

    def test_conversion_failure_stops_before_any_task_or_success_marker(self):
        result, rows, work = self.run_wrapper(extra_environment={"TEST_BUILD_FAIL": "1"})
        self.assertEqual(result.returncode, 17, result.stderr)
        self.assertIn("no tasks started", result.stderr)
        self.assertEqual([row["operation"] for row in rows], ["build"])
        self.assertFalse((work / ".magnus_success").exists())

    def test_conversion_timeout_terminates_children_and_never_launches_tasks(self):
        result, rows, work = self.run_wrapper(timeout=1, extra_environment={"TEST_BUILD_SLEEP": "1"})
        self.assertEqual(result.returncode, 124, result.stderr)
        self.assertIn("no tasks started", result.stderr)
        self.assertEqual([row["operation"] for row in rows], ["build"])
        pid = (work / "child.pid").read_text()
        status = Path("/proc") / pid / "stat"
        for _ in range(20):
            if not status.exists() or status.read_text().rsplit(")", 1)[1].split()[0] == "Z":
                break
            time.sleep(0.05)
        else:
            self.fail("Preparation child survived its timeout")
        self.assertFalse((work / ".magnus_success").exists())

    def test_concurrent_jobs_never_share_prepared_images_or_temporary_directories(self):
        with ThreadPoolExecutor(max_workers=2) as pool:
            outputs = list(pool.map(lambda name: self.run_wrapper(name=name), ("first", "second")))
        image_paths = []
        temporary_paths = []
        for result, rows, _ in outputs:
            self.assertEqual(result.returncode, 0, result.stderr)
            build = next(row for row in rows if row["operation"] == "build")
            image_paths.append(build["args"][-2])
            temporary_paths.append({row["bind"] for row in rows if row["operation"] == "exec"})
        self.assertNotEqual(*image_paths)
        self.assertFalse(temporary_paths[0] & temporary_paths[1])

    def test_repeated_preparation_never_reuses_a_partial_image(self):
        (self.root / ".magnus_tmp").mkdir()
        first = RUNTIME_IMAGE.prepare_runtime_image(
            "singularity",
            "sandbox",
            str(self.image),
            str(self.root),
            5,
        )
        Path(first.image_path).mkdir()
        (Path(first.image_path) / "partial").write_text("incomplete")
        second = RUNTIME_IMAGE.prepare_runtime_image(
            "singularity",
            "sandbox",
            str(self.image),
            str(self.root),
            5,
        )
        self.assertNotEqual(first.image_path, second.image_path)
        self.assertFalse(Path(second.image_path).exists())
        self.assertTrue((Path(first.image_path) / "partial").exists())

    def test_invalid_options_fail_before_creating_scratch(self):
        for runtime, mode, timeout in (
            ("docker", "sandbox", 5),
            ("singularity", "unknown", 5),
            ("singularity", "sandbox", True),
            ("singularity", "sandbox", 0),
        ):
            with self.subTest(runtime=runtime, mode=mode, timeout=timeout):
                with self.assertRaises(ValueError):
                    RUNTIME_IMAGE.prepare_runtime_image(
                        runtime,
                        mode,
                        str(self.image),
                        str(self.root),
                        timeout,
                    )
        self.assertFalse((self.root / ".magnus_tmp").exists())


if __name__ == "__main__":
    unittest.main()
