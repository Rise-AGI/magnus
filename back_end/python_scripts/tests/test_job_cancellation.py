"""Cancellation acknowledgement must precede state changes, release precedes GC."""
import ast
import __future__
import logging
from pathlib import Path
from types import SimpleNamespace
import threading
import unittest


SERVER = Path(__file__).resolve().parents[2] / "server"


def method(
    relative_path,
    name,
    namespace,
):
    source = SERVER / relative_path
    tree = ast.parse(source.read_text(encoding="utf-8"))
    function = next(node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef) and node.name == name)
    exec(compile(ast.Module(body=[function], type_ignores=[]), str(source), "exec", flags=__future__.annotations.compiler_flag), namespace)
    return namespace[name]


class JobCancellationTests(unittest.TestCase):
    def setUp(self):
        import os
        self.namespace = {"os": os, "logger": logging.getLogger(__name__)}
        self.kill = method("_slurm_manager/_control.py", "kill_job", self.namespace)
        self.signal = method("_slurm_manager/_control.py", "send_signal", self.namespace)

    def manager(
        self,
        *results,
    ):
        outcomes = iter(results)
        def run(*args, **kwargs):
            result = next(outcomes)
            if isinstance(result, Exception):
                raise result
            return SimpleNamespace(returncode=result, stderr="failure" if result else "")
        return SimpleNamespace(_transport=SimpleNamespace(run=run))

    def test_unconfirmed_cancel_raises_even_after_kill_signal_ack(self):
        for outcomes in ((0, 255), (255, 255), (0, RuntimeError("private-token"))):
            with self.subTest(outcomes=outcomes), self.assertRaisesRegex(RuntimeError, "cancellation was not confirmed") as error:
                self.kill(self.manager(*outcomes), "123", "user", "private-token")
            self.assertNotIn("private-token", str(error.exception))

    def test_accepted_cancellation_can_follow_failed_fast_signal(self):
        self.kill(self.manager(255, 0), "123", "user", "private-token")

    def test_signal_failure_is_not_reported_as_success(self):
        with self.assertRaisesRegex(RuntimeError, "signal was not confirmed"):
            self.signal(self.manager(255), "123", "TERM", "user", "private-token")

    def test_preemption_keeps_workspace_until_release_and_failure_keeps_state(self):
        statuses = SimpleNamespace(RUNNING="Running", PAUSED="Paused")
        namespace = dict(self.namespace, JobStatus=statuses, Job=SimpleNamespace(id="id", status="status", start_time="start"))
        pause = method("_scheduler/_decisions.py", "_kill_and_pause", namespace)
        for accepted in (False, True):
            with self.subTest(accepted=accepted):
                updates, cleanups, commits = [], [], []
                manager = self.manager(0, 0 if accepted else 255)
                actor = SimpleNamespace(
                    _job_query=lambda db, *entities: db.query(*entities),
                    slurm_manager=SimpleNamespace(kill_job=lambda *args, **kwargs: self.kill(manager, *args, **kwargs)),
                    _clean_up_working_table=lambda value: cleanups.append(value),
                )
                query = SimpleNamespace()
                query.filter = lambda *args: query
                query.update = lambda value, **kwargs: updates.append(value)
                db = SimpleNamespace(query=lambda *args: query, commit=lambda: commits.append(True))
                job = SimpleNamespace(id="job", slurm_job_id="123", runner="user", user=SimpleNamespace(token="private-token"))
                if accepted:
                    pause(actor, db, job)
                    self.assertEqual(updates[0]["status"], "Paused")
                    self.assertEqual(len(commits), 1)
                else:
                    with self.assertRaises(RuntimeError):
                        pause(actor, db, job)
                    self.assertEqual(updates, [])
                    self.assertEqual(commits, [])
                self.assertEqual(cleanups, [])
                self.assertEqual(job.slurm_job_id, "123")

    def test_user_termination_preserves_live_job_on_cancel_failure(self):
        statuses = SimpleNamespace(TERMINATED="Terminated")
        namespace = dict(self.namespace, JobStatus=statuses, is_local_mode=False)
        terminate = method("_scheduler/_core.py", "terminate_job", namespace)
        for accepted in (False, True):
            with self.subTest(accepted=accepted):
                cleanups, commits = [], []
                manager = self.manager(0, 0 if accepted else 255)
                actor = SimpleNamespace(
                    _job_query=lambda db, *entities: db.query(*entities),
                    enabled=True, preparing_jobs={}, _submitting_jobs=set(), _preparing_jobs_lock=threading.Lock(), _loop=None,
                    slurm_manager=SimpleNamespace(kill_job=lambda *args, **kwargs: self.kill(manager, *args, **kwargs)),
                    _clean_up_working_table=lambda value: cleanups.append(value),
                )
                job = SimpleNamespace(id="job", status="Running", slurm_job_id="123", finalization_status=None, runner="user", user=SimpleNamespace(token="private-token"))
                db = SimpleNamespace(commit=lambda: commits.append(True))
                if accepted:
                    terminate(actor, db, job)
                    self.assertEqual(job.status, "Terminated")
                    self.assertEqual(len(commits), 1)
                else:
                    with self.assertRaises(RuntimeError):
                        terminate(actor, db, job)
                    self.assertEqual(job.status, "Running")
                    self.assertEqual(commits, [])
                self.assertEqual(cleanups, [])
                self.assertEqual(job.slurm_job_id, "123")



if __name__ == "__main__":
    unittest.main()
