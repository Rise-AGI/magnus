"""Slurm controller eviction and accounting must not masquerade as transport failure."""
import subprocess
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from test_gpu_resource_query import load_queries


def response(
    stdout="",
    *,
    returncode=0,
    stderr="",
):
    return SimpleNamespace(stdout=stdout, returncode=returncode, stderr=stderr)


def allocation(
    state="FAILED",
    *,
    job_id="15796634",
    submitted="2026-10-17T18:00:00",
    ended="2026-10-19T21:44:08",
    exit_code="1:0",
):
    return "|".join((job_id, state, submitted, ended, exit_code)) + "\n"


class QueryTransport:
    def __init__(
        self,
        responses,
    ):
        self.responses = iter(responses)
        self.calls = []

    def run(
        self,
        command,
        **kwargs,
    ):
        self.calls.append((command, kwargs))
        result = next(self.responses)
        if isinstance(result, Exception):
            raise result
        return result


class SlurmTerminalLookupTests(unittest.TestCase):
    def setUp(self):
        self.module = load_queries()
        self.manager = self.module._ResourceQueryMixin()

    def check(
        self,
        *responses,
        job_id="15796634",
    ):
        self.manager._transport = QueryTransport(responses)
        return self.manager.check_job_status(job_id)

    def evicted(self):
        return response(
            returncode=1,
            stderr="slurm_load_jobs error: Invalid job id specified\n",
        )

    def test_evicted_failed_allocation_uses_history(self):
        self.assertEqual(self.check(self.evicted(), response(allocation())), "FAILED")
        queue, accounting = [call[0] for call in self.manager._transport.calls]
        self.assertIn("--states=all", queue)
        for command in (queue, accounting):
            self.assertIn("--local", command)
            self.assertIn("LC_ALL=C", command)
            self.assertIn("15796634", command)
        for flag in ("--allocations", "--allusers", "--duplicates", "--parsable2"):
            self.assertIn(flag, accounting)
        self.assertNotIn("--state", accounting)
        self.assertIn("--jobs", accounting)  # --jobs defaults its time window to epoch.

    def test_successful_empty_queue_still_prefers_recorded_failure(self):
        self.assertEqual(self.check(response(), response(allocation())), "FAILED")

    def test_visible_live_or_terminal_controller_record_does_not_query_history(self):
        cases = {
            "RUNNING": "RUNNING", "COMPLETING": "RUNNING", "SUSPENDED": "RUNNING",
            "STOPPED": "RUNNING", "POWER_UP_NODE": "RUNNING", "STAGE_OUT": "RUNNING",
            "PENDING": "PENDING", "REQUEUED": "PENDING", "REQUEUE_HOLD": "PENDING",
            "SPECIAL_EXIT": "PENDING", "COMPLETED": "COMPLETED", "FAILED": "FAILED",
            "OUT_OF_MEMORY": "FAILED", "NODE_FAIL": "FAILED", "BOOT_FAIL": "FAILED",
            "DEADLINE": "FAILED", "CANCELLED": "FAILED", "TIMEOUT": "FAILED",
        }
        for state, expected in cases.items():
            with self.subTest(state=state):
                self.assertEqual(self.check(response(state + "\n")), expected)
                self.assertEqual(len(self.manager._transport.calls), 1)

    def test_unknown_or_conflicting_live_states_do_not_release_ownership(self):
        for output in ("COMPLETED\nRUNNING\n", "UNKNOWN_FUTURE_STATE\n", "FAILED+COMPLETING"):
            with self.subTest(output=output):
                self.assertEqual(self.check(response(output)), "UNKNOWN")
                self.assertEqual(len(self.manager._transport.calls), 1)

    def test_preemption_waits_for_controller_transition_or_archived_termination(self):
        self.assertEqual(self.check(response("PREEMPTED\n")), "UNKNOWN")
        self.assertEqual(len(self.manager._transport.calls), 1)
        self.assertEqual(self.check(response("PENDING\n")), "PENDING")
        self.assertEqual(self.check(self.evicted(), response(allocation("PREEMPTED"))), "FAILED")

    def test_ssh_controller_and_mixed_errors_do_not_use_old_history(self):
        failures = [
            response(returncode=255, stderr="Connection closed"),
            response(returncode=1, stderr="slurm_load_jobs error: Unable to contact slurm controller"),
            response(returncode=1, stderr="Invalid job id specified"),
            response(returncode=1, stderr="slurm_load_jobs error: Invalid job id specified\nConnection closed"),
            response(returncode=1, stdout="RUNNING", stderr="slurm_load_jobs error: Invalid job id specified"),
            response("COMPLETED", stderr="partial query failed"),
            OSError("SSH socket unavailable"),
            subprocess.TimeoutExpired("squeue", 30),
        ]
        for failure in failures:
            with self.subTest(failure=failure):
                self.assertEqual(self.check(failure), "UNKNOWN")
                self.assertEqual(len(self.manager._transport.calls), 1)

    def test_history_step_and_other_job_records_never_override_allocation(self):
        rows = allocation("COMPLETED", job_id="15796634.batch", exit_code="0:0")
        rows += allocation("COMPLETED", job_id="15796634.0", exit_code="0:0")
        rows += allocation("COMPLETED", job_id="157966340", exit_code="0:0")
        self.assertEqual(self.check(self.evicted(), response(rows + allocation())), "FAILED")
        self.assertEqual(self.check(response(), response(rows)), "UNKNOWN")

    def test_latest_submit_wins_even_when_it_is_active_after_requeue(self):
        old = allocation()
        for state, expected in (("RUNNING", "RUNNING"), ("PENDING", "PENDING"), ("REQUEUED", "PENDING")):
            latest = allocation(state, submitted="2026-10-19T22:00:00", ended="Unknown", exit_code="0:0")
            for rows in (latest + old, old + latest):
                with self.subTest(state=state, rows=rows):
                    self.assertEqual(self.check(self.evicted(), response(rows)), expected)

    def test_reused_numeric_id_uses_newest_allocation_not_input_order(self):
        old = allocation("FAILED", submitted="2025-01-01T00:00:00", ended="2025-01-01T01:00:00")
        newest = allocation("COMPLETED", exit_code="0:0")
        for rows in (old + newest, newest + old):
            self.assertEqual(self.check(self.evicted(), response(rows)), "COMPLETED")

    def test_conflicting_equal_timestamp_records_are_unknown_but_duplicates_are_ok(self):
        failed = allocation()
        self.assertEqual(self.check(self.evicted(), response(failed + failed)), "FAILED")
        self.assertEqual(
            self.check(self.evicted(), response(failed + allocation("RUNNING", ended="Unknown"))),
            "UNKNOWN",
        )

    def test_cancelled_by_uid_is_terminal(self):
        for state in ("CANCELLED by 1015", "CANCELLED+"):
            self.assertEqual(self.check(self.evicted(), response(allocation(state))), "FAILED")

    def test_terminal_history_requires_end_and_valid_exit_code(self):
        rows = [
            allocation(ended="Unknown"), allocation(ended="2026-10-17T00:00:00"),
            allocation(exit_code=""), allocation(exit_code="bad"),
            allocation(submitted="Unknown"), allocation("UNKNOWN"), "malformed\n",
            allocation("OUT_OF_ME+"),
        ]
        for row in rows:
            with self.subTest(row=row):
                self.assertEqual(self.check(self.evicted(), response(row)), "UNKNOWN")
        self.assertEqual(self.check(self.evicted(), response(allocation("COMPLETED"))), "FAILED")
        self.assertEqual(self.check(self.evicted(), response(allocation("OUT_OF_MEMORY", exit_code="0:0"))), "FAILED")

    def test_empty_history_after_invalid_job_id_waits_for_accounting(self):
        self.assertEqual(self.check(self.evicted(), response()), "UNKNOWN")

    def test_successful_empty_controller_and_history_keep_legacy_contract(self):
        self.assertEqual(self.check(response(), response()), "COMPLETED")

    def test_disabled_accounting_keeps_sites_without_history_working(self):
        disabled = response(returncode=1, stderr="Slurm accounting storage is disabled\n")
        for queue in (response(), self.evicted()):
            self.assertEqual(self.check(queue, disabled), "COMPLETED")

    def test_failed_accounting_is_never_inferred_complete(self):
        errors = [
            response(returncode=1, stderr="Problem talking to the database"),
            response(returncode=1, stderr="Slurm unable to initialize storage plugin"),
            response(returncode=255, stderr="Slurm accounting storage is disabled"),
            response(returncode=1, stderr="Slurm accounting storage is disabled\nPermission denied"),
            response(allocation(), stderr="sacct: error: partial query"),
            response(returncode=127, stderr="sacct: command not found"),
            subprocess.TimeoutExpired("sacct", 1),
        ]
        for queue in (response(), self.evicted()):
            for error in errors:
                with self.subTest(error=error):
                    self.assertEqual(self.check(queue, error), "UNKNOWN")

    def test_both_queries_share_one_deadline(self):
        with patch.object(self.module.time, "monotonic", side_effect=[100.0, 100.0, 127.5]):
            self.assertEqual(self.check(self.evicted(), response(allocation())), "FAILED")
        self.assertEqual([kwargs["timeout"] for _, kwargs in self.manager._transport.calls], [30.0, 2.5])

    def test_exhausted_budget_does_not_start_accounting(self):
        with patch.object(self.module.time, "monotonic", side_effect=[100.0, 100.0, 130.0]):
            self.assertEqual(self.check(self.evicted()), "UNKNOWN")
        self.assertEqual(len(self.manager._transport.calls), 1)

    def test_only_single_allocation_ids_reach_cli(self):
        for identifier in (None, 123, "", "123.0", "123,124", "--all", "123;true", "123_1", "123+1"):
            self.assertEqual(self.check(job_id=identifier), "UNKNOWN")
            self.assertEqual(self.manager._transport.calls, [])


if __name__ == "__main__":
    unittest.main()
