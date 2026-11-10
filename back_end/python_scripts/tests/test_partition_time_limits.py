"""Time admission uses bounded read-only policy and respects QOS exceptions."""
import concurrent.futures
import importlib.util
from pathlib import Path
import subprocess
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

SOURCE = Path(__file__).resolve().parents[2] / "server/_slurm_manager/_time_limits.py"
spec = importlib.util.spec_from_file_location("isolated_partition_times", SOURCE)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

PARTITIONS = """PartitionName=regular Default=YES MaxTime=2-00:00:00
PartitionName=debug Default=NO MaxTime=00:30:00
PartitionName=long Default=NO MaxTime=UNLIMITED
"""


class TimeLimitsTests(unittest.TestCase):
    def policy(
        self,
        partitions=PARTITIONS,
        qos="normal|\n",
    ):
        transport = Mock()
        transport.run.side_effect = [SimpleNamespace(stdout=partitions), SimpleNamespace(stdout=qos)]
        return module.PartitionTimeLimits(transport), transport

    def test_default_partition_rejects_seven_days_and_allows_exact_boundary(self):
        policy, transport = self.policy()
        with self.assertRaisesRegex(ValueError, "10080.*regular: 2-00:00:00"):
            policy.validate(minutes=10080, partition=None, qos=None)
        policy.validate(minutes=2880, partition=None, qos=None)
        self.assertEqual(transport.run.call_count, 2)
        self.assertTrue(all(0 < call.kwargs["timeout"] <= 5 for call in transport.run.call_args_list))

    def test_explicit_partition_and_multiple_candidates(self):
        policy, _ = self.policy()
        with self.assertRaisesRegex(ValueError, "debug"):
            policy.validate(minutes=31, partition="debug", qos="normal")
        policy.validate(minutes=31, partition="debug,regular", qos="normal")
        policy.validate(minutes=10080, partition="long", qos="normal")
        policy.validate(minutes=10080, partition="missing", qos="normal")

    def test_no_walltime_keeps_scheduler_default_without_query(self):
        policy, transport = self.policy()
        policy.validate(minutes=None, partition=None, qos=None)
        transport.run.assert_not_called()

    def test_zero_means_unlimited_not_zero_minutes(self):
        policy, _ = self.policy()
        with self.assertRaises(ValueError):
            policy.validate(minutes=0, partition="regular", qos=None)
        policy.validate(minutes=0, partition="long", qos=None)

    def test_qos_exception_and_unknown_default_do_not_false_reject(self):
        policy, _ = self.policy(qos="normal|\nprivileged|PartitionTimeLimit,DenyOnLimit\n")
        for qos in (None, "privileged", "unknown"):
            policy.validate(minutes=10080, partition=None, qos=qos)
        with self.assertRaises(ValueError):
            policy.validate(minutes=10080, partition=None, qos="normal")

    def test_unknown_or_failed_policy_does_not_invent_limit(self):
        for error in (OSError(), subprocess.TimeoutExpired("scontrol", 5), subprocess.CalledProcessError(1, "scontrol")):
            policy, transport = self.policy()
            transport.run.side_effect = error
            policy.validate(minutes=10080, partition=None, qos=None)
            policy.validate(minutes=10080, partition=None, qos=None)
            self.assertEqual(transport.run.call_count, 1)
        for rows, qos in (("bad", "normal|"), (PARTITIONS.replace("2-00:00:00", "Unknown"), "normal|"), (PARTITIONS, ""), (PARTITIONS, "normal|bad|extra")):
            policy, _ = self.policy(rows, qos)
            policy.validate(minutes=10080, partition=None, qos=None)

    def test_expired_policy_refreshes(self):
        policy, transport = self.policy()
        policy.validate(minutes=2880, partition=None, qos=None)
        policy.expires = 0
        transport.run.side_effect = [SimpleNamespace(stdout=PARTITIONS.replace("2-00:00:00", "01:00:00")), SimpleNamespace(stdout="normal|\n")]
        with self.assertRaises(ValueError):
            policy.validate(minutes=2880, partition=None, qos=None)
        self.assertEqual(transport.run.call_count, 4)

    def test_concurrent_checks_share_policy_read(self):
        policy, transport = self.policy()
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(lambda _: policy.validate(minutes=10, partition=None, qos=None), range(32)))
        self.assertEqual(transport.run.call_count, 2)

    def test_duration(self):
        self.assertEqual(module._duration_seconds("2-00:00:00"), 172800)
        self.assertEqual(module._duration_seconds("00:30:00"), 1800)
        self.assertEqual(module._duration_seconds("100:00:00"), 360000)
        for value in ("INFINITE", "UNLIMITED"):
            self.assertIsNone(module._duration_seconds(value))
        for value in ("NONE", "00:90:00", "garbage"):
            with self.assertRaises(ValueError):
                module._duration_seconds(value)


if __name__ == "__main__":
    unittest.main()
