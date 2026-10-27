"""Deadline tests without SSH, authentication or any external endpoint."""
from __future__ import annotations

import ast
from pathlib import Path
import subprocess
import os
import tempfile
import hashlib
import sys
import time
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from test_gpu_resource_query import load_queries


SERVER = Path(__file__).resolve().parents[2] / "server"


def load_transport():
    namespace = {"__name__": __name__}
    for name in ("_ssh_auto_connect.py", "_file_receive.py", "_transport.py"):
        path = SERVER / "_slurm_manager" / name
        tree = ast.parse(path.read_text(encoding="utf-8"))
        tree.body = [node for node in tree.body if not isinstance(node, ast.ImportFrom) or not node.level]
        exec(compile(tree, str(path), "exec"), namespace)
    return namespace


class TransportDeadlineTests(unittest.TestCase):
    def test_cancelled_scp_and_stream_reap_only_their_transfer(self):
        namespace = load_transport()
        independent = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
        self.addCleanup(independent.wait)
        self.addCleanup(independent.kill)
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "source"
            source.write_bytes(b"x" * 2**20)
            for stream in (False, True):
                with self.subTest(stream=stream):
                    cancelled = threading.Event()
                    children = []
                    original = subprocess.Popen
                    def capture(*args, **kwargs):
                        child = original(*args, **kwargs)
                        children.append(child)
                        return child
                    timer = threading.Timer(0.1, cancelled.set)
                    started = time.monotonic()
                    timer.start()
                    try:
                        with patch.object(subprocess, "Popen", capture), self.assertRaises(InterruptedError):
                            command = [sys.executable, "-c", "import time; time.sleep(30)"]
                            if stream:
                                namespace["_stream_file_to_command"](
                                    command, str(source), prefix=b"header", timeout=30,
                                    cancel_event=cancelled,
                                )
                            else:
                                namespace["_run_scp"](command, timeout=30, cancel_event=cancelled)
                    finally:
                        timer.join()
                    self.assertLess(time.monotonic() - started, 3)
                    self.assertEqual(len(children), 1)
                    self.assertIsNotNone(children[0].poll())
                    self.assertIsNone(independent.poll())

    def test_cancelled_upload_never_probes_shared_master(self):
        namespace = load_transport()
        cancelled = threading.Event()
        cancelled.set()
        actor = namespace["_SshControlMasterTransport"]("socket", "host", "user", auto_connect={})
        actor._ensure_socket = lambda **kwargs: self.fail("cancelled upload probes master")
        with self.assertRaises(InterruptedError):
            actor.push("local", "remote", cancel_event=cancelled)
        with self.assertRaises(InterruptedError):
            actor.run_with_file_input(["receiver"], "local", prefix=b"", timeout=10, cancel_event=cancelled)

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

    def test_scp_timeout_reaps_actual_process_without_waiting_for_pipe_eof(self):
        namespace = load_transport()
        started = time.monotonic()
        with self.assertRaises(subprocess.TimeoutExpired):
            namespace["_run_scp"](
                [sys.executable, "-c", "import time; time.sleep(30)"],
                timeout=0.1,
            )
        self.assertLess(time.monotonic() - started, 3)

    def test_scp_auth_and_transfer_share_a_deadline_and_do_not_expose_paths(self):
        namespace = load_transport()
        clock = SimpleNamespace(now=10)
        namespace["time"] = SimpleNamespace(monotonic=lambda: clock.now)
        def ensure(*args, deadline=None):
            self.assertEqual(deadline, 15)
            clock.now += 2
        def transfer(command, *, timeout, cancel_event=None):
            self.assertEqual(timeout, 3)
            raise subprocess.TimeoutExpired(command, timeout)
        namespace["ensure_control_master"] = ensure
        namespace["_run_scp"] = transfer
        actor = namespace["_SshControlMasterTransport"]("socket", "host", "user", auto_connect={})
        with self.assertRaisesRegex(TimeoutError, "^SCP transfer exceeded its time budget$"):
            actor.fetch("/capability-token/file", "local", timeout=5)

    def test_binary_stream_delivers_header_and_content_and_bounded_output(self):
        namespace = load_transport()
        payload = bytes(range(256)) * 8192
        prefix = b"header\n"
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "source"
            path.write_bytes(payload)
            result = namespace["_stream_file_to_command"](
                [sys.executable, "-c", "import sys,hashlib; data=sys.stdin.buffer.read(); print(hashlib.sha256(data).hexdigest()); sys.stderr.write('x'*100000)"],
                str(path),
                prefix=prefix,
                timeout=5,
            )
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout.strip(), hashlib.sha256(prefix + payload).hexdigest())
        self.assertEqual(len(result.stderr), 65536)

    @unittest.skipIf(os.name == "nt", "separate POSIX session models a persistent SSH master")
    def test_stream_timeout_does_not_join_pipes_retained_by_existing_master(self):
        namespace = load_transport()
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "source"
            source.write_bytes(b"x" * 1048576)
            pid_file = Path(temporary) / "master.pid"
            script = (
                "import subprocess,sys,time; "
                "p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(30)'],start_new_session=True); "
                "open(sys.argv[1],'w').write(str(p.pid)); time.sleep(30)"
            )
            started = time.monotonic()
            try:
                with self.assertRaises(subprocess.TimeoutExpired):
                    namespace["_stream_file_to_command"](
                        [sys.executable, "-c", script, str(pid_file)],
                        str(source),
                        prefix=b"",
                        timeout=0.3,
                    )
                self.assertLess(time.monotonic() - started, 3)
                self.assertTrue(pid_file.exists())
            finally:
                if pid_file.exists():
                    try:
                        os.kill(int(pid_file.read_text()), 9)
                    except ProcessLookupError:
                        pass

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
        lock = namespace["_connection_lock"]("socket")
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
        namespace["subprocess"] = SimpleNamespace(**vars(subprocess))
        namespace["subprocess"].run = lambda *args, **kwargs: calls.append(kwargs) or subprocess.CompletedProcess(args[0], 0, "", "")
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
            _slurm_view_refresh_locks={"cold": SimpleNamespace(
                acquire=lambda **kwargs: waits.append(kwargs) or False,
                release=lambda: self.fail("released another refresh owner's lock"),
            )},
        )
        exec(compile(ast.Module(body=[function], type_ignores=[]), str(path), "exec"), namespace)
        with self.assertRaises(TimeoutError):
            namespace["_cached_slurm_view"]("cold", 5, lambda: self.fail("lock not acquired"))
        self.assertEqual(waits, [{"timeout": 30}])


class ConnectorCommandTests(unittest.TestCase):
    def test_connector_gets_socket_identity_and_must_prove_master_ready(self):
        namespace = load_transport()
        with tempfile.TemporaryDirectory() as directory:
            result = Path(directory) / "identity"
            script = "import os,pathlib; pathlib.Path("+repr(str(result))+").write_text('|'.join(os.environ[k] for k in ['MAGNUS_SSH_CONTROL_PATH','MAGNUS_SSH_HOST','MAGNUS_SSH_USER']))"
            config = {"command": [sys.executable, "-c", script], "timeout_seconds": 2}
            namespace["master_alive"] = lambda *args, **kwargs: False
            with self.assertRaisesRegex(RuntimeError, "requested control socket"):
                namespace["_establish_command"]("socket", "host", "user", config)
            self.assertEqual(result.read_text(), "socket|host|user")
            namespace["master_alive"] = lambda *args, **kwargs: True
            namespace["_establish_command"]("socket", "host", "user", config)

    def test_connector_errors_do_not_expose_authentication_output(self):
        namespace = load_transport()
        config = {"command": [sys.executable, "-c", "import sys; print('private-authentication-material',file=sys.stderr);sys.exit(3)"]}
        with self.assertRaisesRegex(RuntimeError, r"SSH connector failed \(exit 3\)") as failure:
            namespace["_establish_command"]("socket", "host", "user", config)
        self.assertNotIn("private-authentication-material", str(failure.exception))

    def test_connector_timeout_is_bounded_and_reaps_process(self):
        namespace = load_transport()
        started = time.monotonic()
        with self.assertRaises(subprocess.TimeoutExpired):
            namespace["_establish_command"](
                "socket", "host", "user",
                {"command": [sys.executable, "-c", "import time;time.sleep(60)"], "timeout_seconds": 0.05},
            )
        self.assertLess(time.monotonic() - started, 2)

    def test_other_target_authentication_does_not_share_the_blocked_lock(self):
        namespace = load_transport()
        lock = namespace["_connection_lock"]("one")
        namespace["master_alive"] = lambda *args, **kwargs: False
        established = []
        namespace["_establish"] = lambda *args, **kwargs: established.append(args[0])
        with lock:
            namespace["ensure_control_master"]("two", "host", "user", {}, deadline=time.monotonic()+1)
        self.assertEqual(established, ["two"])


if __name__ == "__main__":
    unittest.main()
