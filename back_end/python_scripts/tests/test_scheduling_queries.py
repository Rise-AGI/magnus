"""Scheduler observations under old CLI formats, outages, and concurrent polls."""

from __future__ import annotations

import ast
import enum
import importlib.util
import subprocess
import sys
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Enum, String, create_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column
from sqlalchemy.pool import StaticPool
from test_execution_targets import targets_module
from test_gpu_resource_query import load_queries as load_resource_queries

SERVER = Path(__file__).resolve().parents[2] / "server"


def module(
    name: str,
    relative: str,
):
    spec = importlib.util.spec_from_file_location(
        name,
        SERVER / relative,
    )
    loaded = importlib.util.module_from_spec(spec)
    sys.modules[name] = loaded
    spec.loader.exec_module(loaded)
    return loaded


schemas = module(
    "isolated_scheduling_schemas",
    "schemas/_scheduling.py",
)
cache = module(
    "isolated_scheduling_cache",
    "_slurm_manager/_query_cache.py",
)


def load_queries():
    resources = load_resource_queries()
    namespace = {
        "__name__": __name__,
        "_Transport": object,
        "_parse_tres_int": resources._parse_tres_int,
        "_parse_tres_mem_mb": resources._parse_tres_mem_mb,
        **{
            name: getattr(
                schemas,
                name,
            )
            for name in (
                "ClusterSchedulingResponse",
                "SchedulerPolicySection",
                "SchedulerUsage",
                "SchedulerAssociationUsageSection",
            )
        },
    }
    parser_namespace = {
        "__name__": __name__,
        "SchedulerAssociationUsage": schemas.SchedulerAssociationUsage,
        "SchedulerLimitUsage": schemas.SchedulerLimitUsage,
    }
    parser_path = SERVER / "_slurm_manager/_association_usage.py"
    parser_tree = ast.parse(parser_path.read_text())
    parser_tree.body = [
        node for node in parser_tree.body
        if not isinstance(node, ast.ImportFrom) or not node.level
    ]
    exec(compile(parser_tree, str(parser_path), "exec"), parser_namespace)
    namespace.update({
        name: parser_namespace[name]
        for name in ("parse_association_record", "select_association_chain")
    })
    path = SERVER / "_slurm_manager/_scheduling.py"
    tree = ast.parse(path.read_text())
    tree.body = [
        node
        for node in tree.body
        if not isinstance(
            node,
            ast.ImportFrom,
        )
        or not node.level
    ]
    exec(
        compile(
            tree,
            str(path),
            "exec",
        ),
        namespace,
    )
    return SimpleNamespace(**namespace)


class Transport:
    def __init__(
        self,
        **outputs,
    ):
        self.outputs = outputs
        self.commands = []

    def run(
        self,
        command,
        **options,
    ):
        self.commands.append((command, options))
        if "sacctmgr" in command:
            key = "assoc" if "assoc" in command else "qos"
        elif "scontrol" in command:
            if "assoc_mgr" in command:
                key = "association_usage"
            else:
                key = "configuration" if "config" in command else "partition"
        elif "id" in command:
            key = "identity"
        else:
            key = "queue"
        if key == "queue" and any(arg.startswith("--account=") for arg in command):
            key = "account_queue" if "account_queue" in self.outputs else "queue"
        value = self.outputs.get(
            key,
            "",
        )
        if callable(value):
            value = value(command, options)
        elif key == "association_usage" and isinstance(value, str):
            account = next(arg.removeprefix("accounts=") for arg in command if arg.startswith("accounts="))
            records = []
            for line in value.splitlines():
                if line.startswith("ClusterName="):
                    records.append([line])
                elif records:
                    records[-1].append(line)
            value = "\n".join(
                "\n".join(record) for record in records
                if f"Account={account} " in record[0]
            )
        if isinstance(
            value,
            BaseException,
        ):
            raise value
        if isinstance(
            value,
            subprocess.CompletedProcess,
        ):
            return value
        return subprocess.CompletedProcess(
            command,
            0,
            value,
            "",
        )


class QueryTests(unittest.TestCase):
    def setUp(self):
        self.queries = load_queries()
        self.created = datetime(
            2026,
            10,
            29,
            10,
            tzinfo=timezone.utc,
        )
        self.job_id = "0123456789abcdef"
        self.comment = f"magnus:{self.job_id}:" + "a" * 32

    def observe(
        self,
        output,
        identifier="37",
    ):
        self.transport = Transport(queue=output)
        return self.queries.read_job_scheduling(
            self.transport,
            self.job_id,
            identifier,
        )

    def test_pending_reason_and_estimate_are_utc(self):
        result = self.observe(
            f"37|PENDING|Resources|2026-10-29T18:30:00|2026-10-29T10:00:02|proj|normal|cpu|{self.comment}\n",
        )
        self.assertEqual(
            result["availability"],
            "ready",
        )
        self.assertEqual(
            result["reason"],
            "Resources",
        )
        self.assertEqual(
            result["expected_start_time"],
            datetime(
                2026,
                10,
                29,
                18,
                30,
                tzinfo=timezone.utc,
            ),
        )
        self.assertIsNone(result["actual_start_time"])
        command, options = self.transport.commands[0]
        self.assertIn(
            "TZ=UTC",
            command,
        )
        self.assertIn(
            "--all",
            command,
        )
        self.assertIn(
            "--local",
            command,
        )
        self.assertIn(
            "SLURM_TIME_FORMAT=standard",
            command,
        )
        self.assertLessEqual(
            options["timeout"],
            6,
        )

    def test_unknown_estimate_remains_null_and_running_time_is_actual(self):
        pending = self.observe(
            f"37|PENDING|AssocGrpNodeLimit|N/A|2026-10-29T10:00:02|proj|normal|cpu|{self.comment}",
        )
        self.assertIsNone(pending["expected_start_time"])
        running = self.observe(
            f"37|RUNNING|None|2026-10-29T11:00:00|2026-10-29T10:00:02|proj|normal|cpu|{self.comment}",
        )
        self.assertIsNone(running["reason"])
        self.assertIsNone(running["expected_start_time"])
        self.assertEqual(
            running["actual_start_time"].hour,
            11,
        )

    def test_recycled_allocation_and_unverifiable_legacy_identity(self):
        foreign = self.comment.replace(
            self.job_id,
            "f" * 16,
        )
        row = f"37|PENDING|Priority|N/A|2026-10-29T10:00:02|p|q|cpu|{foreign}"
        self.assertEqual(
            self.observe(row)["availability"],
            "identity_mismatch",
        )
        self.assertEqual(
            self.observe(
                row.rsplit(
                    "|",
                    1,
                )[0]
                + "|(null)"
            )["availability"],
            "identity_unverified",
        )
        old = (
            row.replace(
                "2026-10-29T10:00:02",
                "2026-10-28T10:00:02",
            ).rsplit(
                "|",
                1,
            )[0]
            + "|(null)"
        )
        self.assertEqual(
            self.observe(old)["availability"],
            "identity_unverified",
        )
        later = (
            row.replace(
                "2026-10-29T10:00:02",
                "2027-10-29T10:00:02",
            ).rsplit(
                "|",
                1,
            )[0]
            + "|(null)"
        )
        self.assertEqual(
            self.observe(later)["availability"],
            "identity_unverified",
        )

    def test_missing_and_invalid_output_never_invents_reason_or_capacity(self):
        self.assertEqual(
            self.observe("")["availability"],
            "not_in_queue",
        )
        for output in (
            "banner\n37|PENDING",
            "37|PENDING|x|bad|bad|p|q|cpu|",
            "38|PENDING|x|N/A|N/A|p|q|cpu|",
        ):
            with self.subTest(output=output):
                result = self.observe(output)
                self.assertEqual(
                    result["availability"],
                    "unavailable",
                )
                self.assertNotIn(
                    "reason",
                    result,
                )
        result = self.observe(
            "",
            identifier="37;touch /tmp/no",
        )
        self.assertEqual(
            result["error"],
            "invalid_slurm_job_id",
        )
        self.assertEqual(
            self.transport.commands,
            [],
        )

    def test_transport_failure_exposes_only_public_code(self):
        for error, expected in (
            (
                subprocess.TimeoutExpired(
                    ["secret"],
                    6,
                    stderr="credential",
                ),
                "timeout",
            ),
            (OSError("secret credential"), "transport_error"),
            (RuntimeError("connector secret credential"), "transport_error"),
            (
                subprocess.CompletedProcess(
                    ["secret"],
                    1,
                    "",
                    "credential",
                ),
                "command_failed",
            ),
        ):
            with self.subTest(expected=expected):
                result = self.observe(error)
                self.assertEqual(
                    result["error"],
                    expected,
                )
                self.assertNotIn(
                    "credential",
                    str(result),
                )
                self.assertNotIn(
                    "secret",
                    str(result),
                )

    def cluster(
        self,
        **outputs,
    ):
        defaults = {
            "assoc": "one|project|user||root|normal|normal|cpu=1920|||||50|\n",
            "qos": "normal|||cpu=6400,node=100|cpu=6400||5000|5000|5-00:00:00|12|24\n",
            "partition": "PartitionName=cpu State=UP QoS=N/A MaxTime=1-00:00:00 MaxNodes=8 Secret=hidden\n",
            "queue": "12|RUNNING|cpu=128,node=2,mem=512G\n13|PENDING|cpu=64,node=1,mem=256G\n",
            "configuration": "PrivateData = none\n",
            "identity": "real-user\n",
            "association_usage": (
                "ClusterName=one Account=root UserName= Partition= ID=1 ParentAccount= "
                "GrpJobs=N(10) GrpSubmitJobs=N(11) GrpTRES=cpu=N(512),node=N(8) Comment=(null)\n"
                "ClusterName=one Account=project UserName= Partition= ID=2 ParentAccount=root(1) "
                "GrpJobs=32(4) GrpSubmitJobs=N(5) GrpTRES=cpu=N(128),mem=N(524288),node=32(32) Comment=(null)\n"
                "ClusterName=one Account=project UserName=user(1000) Partition= ID=3 ParentAccount= "
                "GrpJobs=N(0) GrpSubmitJobs=32(1) GrpTRES=cpu=N(0),mem=N(0),node=N(0) Comment=(null)\n"
            ),
        }
        self.transport = Transport(**(defaults | outputs))
        return self.queries.read_cluster_scheduling(
            self.transport,
            "target",
            "user",
            "project",
            "normal",
            ["cpu"],
        )

    def test_visible_limits_keep_slurm_units_without_fabricating_remaining(self):
        result = self.cluster()
        self.assertEqual(
            result.availability,
            "ready",
        )
        self.assertEqual(
            result.associations.rows[0]["GrpTRES"],
            "cpu=1920",
        )
        self.assertEqual(
            result.associations.rows[0]["MaxSubmitJobs"],
            "50",
        )
        self.assertIsNone(result.associations.rows[0]["MaxJobs"])
        self.assertIsNone(result.remaining_resources)
        self.assertNotIn(
            "Secret",
            result.partition_limits.rows[0],
        )
        self.assertEqual(
            result.usage.allocated_cpus,
            128,
        )
        self.assertEqual(
            result.usage.pending_cpus,
            64,
        )
        self.assertEqual(
            result.usage.scope,
            "slurm_user",
        )
        qos_command = next(command for command, _ in self.transport.commands if "qos" in command)
        self.assertIn(
            "format=Name,Flags,GrpTRES,MaxTRESPU,MaxTRESPA,MaxTRES,MaxJobsPU,MaxSubmitJobsPU,MaxWall,MaxJobsPA,MaxSubmitJobsPA",
            qos_command,
        )
        self.assertTrue(all(options["timeout"] <= 6 for _, options in self.transport.commands))

    def test_parent_usage_is_distinct_from_user_and_account_queue_usage(self):
        result = self.cluster(configuration="PrivateData = jobs,usage")
        section = result.association_usage
        self.assertEqual(section.availability, "ready")
        self.assertEqual(result.account_usage.availability, "unavailable")
        self.assertEqual({(row.account, row.user) for row in section.rows}, {
            ("project", None), ("root", None),
        })
        parent = next(row for row in section.rows if row.account == "project" and row.user is None)
        self.assertEqual(parent.group_tres["node"].model_dump(), {"limit": 32, "used": 32})
        self.assertEqual(parent.job_limits["GrpJobs"].model_dump(), {"limit": 32, "used": 4})
        self.assertEqual(result.usage.allocated_node_slots, 2)
        self.assertIsNone(result.remaining_resources)
        commands = [command for command, _ in self.transport.commands if "assoc_mgr" in command]
        self.assertEqual(len(commands), 2)
        self.assertIn("accounts=project", commands[0])
        self.assertIn("accounts=root", commands[1])
        self.assertTrue(all("flags=assoc" in command and "users=user" in command for command in commands))

    def test_unavailable_parent_preserves_visible_limits_without_ready_claim(self):
        valid = self.cluster().association_usage
        self.assertEqual(len(valid.rows), 2)
        original = self.transport.outputs["association_usage"]

        def lookup(command, options):
            if "accounts=root" in command:
                raise subprocess.TimeoutExpired(["secret"], 6, stderr="credential")
            return original.splitlines()[1]

        result = self.cluster(association_usage=lookup)
        self.assertEqual(result.association_usage.availability, "partial")
        self.assertEqual(result.association_usage.error, "timeout")
        self.assertEqual(result.association_usage.missing_accounts, ["root"])
        self.assertEqual(len(result.association_usage.rows), 1)
        self.assertEqual(result.availability, "partial")
        self.assertNotIn("credential", result.model_dump_json())
        self.assertNotIn("secret", result.model_dump_json())

    def test_filter_ignoring_provider_does_not_expose_other_users_or_accounts(self):
        self.cluster()
        output = self.transport.outputs["association_usage"]
        foreign = output.replace("Account=project ", "Account=foreign ").replace("user(1000)", "other(2000)")
        foreign = foreign.replace("ID=1 ", "ID=11 ").replace("ID=2 ", "ID=12 ").replace("ID=3 ", "ID=13 ")
        result = self.cluster(association_usage=lambda *_: output + foreign)
        self.assertEqual(result.association_usage.availability, "unavailable")
        self.assertEqual(result.association_usage.rows, [])
        self.assertNotIn("foreign", result.association_usage.model_dump_json())
        self.assertNotIn("other", result.association_usage.model_dump_json())

    def test_missing_and_malformed_associations_do_not_invent_zero_usage(self):
        self.cluster()
        original = self.transport.outputs["association_usage"]
        for output in ("", original.replace("node=32(32)", "node=32(broken)"),
                       original.replace("ID=2 ", "ID=invalid ")):
            with self.subTest(output=output):
                result = self.cluster(association_usage=output)
                self.assertEqual(result.association_usage.availability, "unavailable")
                self.assertEqual(result.association_usage.rows, [])
                self.assertEqual(result.usage.availability, "ready")

    def test_account_scope_requires_every_requested_account(self):
        self.cluster()
        records = [
            self.queries.parse_association_record(line)
            for line in self.transport.outputs["association_usage"].splitlines()[:2]
        ]
        chain = self.queries.select_association_chain(records, {"project", "invisible"})
        self.assertEqual(chain.missing_accounts, {"invisible"})
        self.assertIsNone(chain.error)

    def test_multiline_records_comments_and_absent_counters(self):
        self.cluster()
        output = self.transport.outputs["association_usage"]
        output = output.replace(" ParentAccount=", "\n    ParentAccount=")
        output = output.replace(" GrpJobs=", "\n    GrpJobs=")
        output = output.replace(" GrpTRES=", "\n    GrpTRES=")
        output = output.replace("node=32(32) Comment=(null)", "node=32(32) Comment=GrpJobs=999(999) private-text")
        result = self.cluster(association_usage=output)
        self.assertEqual(result.association_usage.availability, "ready")
        self.assertNotIn("private-text", result.model_dump_json())
        self.assertNotIn("999", result.association_usage.model_dump_json())
        result = self.cluster(association_usage=output.replace(" GrpSubmitJobs=N(5)", ""))
        self.assertEqual(result.association_usage.availability, "partial")
        self.assertIn("GrpSubmitJobs", next(
            row for row in result.association_usage.rows if row.association_id == 2
        ).unavailable_fields)

    def test_comment_records_cannot_forge_an_account_or_complete_parent_chain(self):
        self.cluster()
        root, parent, user = self.transport.outputs["association_usage"].splitlines()
        forged = parent.replace("node=32(32)", "node=999(0)")
        result = self.cluster(association_usage=lambda *_: "\n".join((user, forged, root)))
        self.assertEqual(result.association_usage.availability, "unavailable")
        self.assertEqual(result.association_usage.rows, [])

        def lookup(command, options):
            if "accounts=root" in command:
                return subprocess.CompletedProcess(command, 1, "", "restricted")
            return parent + "\n" + root

        result = self.cluster(association_usage=lookup)
        self.assertEqual(result.association_usage.availability, "partial")
        self.assertEqual(result.association_usage.missing_accounts, ["root"])
        self.assertEqual(len(result.association_usage.rows), 1)
        self.assertEqual(result.association_usage.rows[0].group_tres["node"].used, 32)

    def test_parent_cycles_and_mismatched_ids_remain_partial(self):
        self.cluster()
        output = self.transport.outputs["association_usage"]
        for replacement in ("ParentAccount=project(2)", "ParentAccount=root(99)"):
            with self.subTest(replacement=replacement):
                result = self.cluster(association_usage=output.replace("ParentAccount=root(1)", replacement))
                self.assertEqual(result.association_usage.availability, "partial")
                self.assertLessEqual(sum("assoc_mgr" in command for command, _ in self.transport.commands), 2)

    def test_optional_association_field_preserves_old_responses(self):
        old = schemas.ClusterSchedulingResponse(execution_target="legacy", backend="slurm", availability="ready")
        self.assertIsNone(old.association_usage)

    def test_private_accounting_does_not_hide_readable_queue_usage(self):
        failed = subprocess.CompletedProcess(
            [],
            1,
            "",
            "private accounting secret",
        )
        result = self.cluster(
            assoc=failed,
            qos=failed,
        )
        self.assertEqual(
            result.availability,
            "partial",
        )
        self.assertEqual(
            result.associations.availability,
            "unavailable",
        )
        self.assertEqual(
            result.usage.allocated_cpus,
            128,
        )
        self.assertNotIn(
            "secret",
            result.model_dump_json(),
        )

    def test_unknown_resources_and_duplicate_ids_are_not_zero(self):
        for output in (
            "12|RUNNING|node=1,mem=2G,broken",
            "12|RUNNING|cpu=8,node=1,mem=2G\n12|RUNNING|cpu=8,node=1,mem=2G",
            "12|PREEMPTED|cpu=8,node=1,mem=2G",
        ):
            with self.subTest(output=output):
                result = self.cluster(queue=output)
                self.assertEqual(
                    result.usage.availability,
                    "unavailable",
                )
                self.assertIsNone(result.usage.allocated_cpus)
        empty = self.cluster(queue="")
        self.assertEqual(
            empty.usage.allocated_cpus,
            0,
        )

    def test_arrays_and_nonrunning_holders_are_retained(self):
        result = self.cluster(
            queue="12_0|RUNNING|cpu=4,node=1,mem=2G\n12_1|PENDING|cpu=4,node=1,mem=2G\n13|COMPLETING|cpu=8,node=1,mem=2G\n14|SUSPENDED|cpu=2,node=1,mem=2G"
        )
        self.assertEqual(
            result.usage.allocated_cpus,
            14,
        )
        self.assertEqual(
            result.usage.allocated_records,
            3,
        )
        self.assertEqual(
            result.usage.pending_records,
            1,
        )

    def test_partition_qos_is_queried_alongside_job_qos(self):
        result = self.cluster(
            partition="PartitionName=cpu State=UP QoS=partition-policy",
            qos="normal||||||||||\npartition-policy|||cpu=512|||||||\n",
        )
        self.assertEqual(
            {row["Name"] for row in result.qos_limits.rows},
            {"normal", "partition-policy"},
        )
        commands = [command for command, _ in self.transport.commands]
        self.assertTrue(any("name=normal,partition-policy" in command for command in commands))

    def test_pending_array_elements_are_counted_separately(self):
        result = self.cluster(
            queue=(
                "16352277_0|PENDING|cpu=4,mem=16000M,node=1,billing=4\n"
                "16352277_1|PENDING|cpu=4,mem=16000M,node=1,billing=4\n"
                "16352278|PENDING|cpu=1,mem=4000M,node=1,billing=1\n"
            ),
        )
        for usage in (result.usage, result.account_usage):
            self.assertEqual(
                usage.availability,
                "ready",
            )
            self.assertEqual(
                (usage.pending_records, usage.pending_cpus, usage.pending_memory_mb),
                (3, 9, 36000),
            )
        for command, _ in self.transport.commands:
            if "squeue" in command:
                self.assertIn(
                    "--Format=JobArrayID:0|,State:0|,tres-alloc:0",
                    command,
                )

    def test_account_scope_and_user_scope_are_distinct_with_native_tres(self):
        result = self.cluster(
            account_queue="23|RUNNING|cpu=512,node=8,mem=2T,gres/gpu=8,gres/gpu:a100=8\n",
        )
        self.assertEqual(
            result.usage.scope,
            "slurm_user",
        )
        self.assertEqual(
            result.usage.allocated_cpus,
            128,
        )
        self.assertEqual(
            result.account_usage.scope,
            "slurm_account",
        )
        self.assertEqual(
            result.account_usage.allocated_cpus,
            512,
        )
        self.assertEqual(
            result.account_usage.allocated_memory_mb,
            2 * 1024**2,
        )
        self.assertEqual(
            result.account_usage.allocated_gpus,
            8,
        )
        self.assertEqual(
            result.qos_limits.rows[0]["MaxJobsPA"],
            "12",
        )
        self.assertEqual(
            result.qos_limits.rows[0]["MaxSubmitJobsPA"],
            "24",
        )
        for command, _ in self.transport.commands:
            if "squeue" in command:
                self.assertIn(
                    "--all",
                    command,
                )
                self.assertIn(
                    "--local",
                    command,
                )
        self.assertTrue(
            any("--account=project" in command for command, _ in self.transport.commands)
        )

    def test_hidden_account_usage_does_not_become_ready_zero(self):
        for configuration in ("PrivateData = jobs,users", "PrivateData = all", ""):
            with self.subTest(configuration=configuration):
                result = self.cluster(configuration=configuration)
                self.assertEqual(
                    result.account_usage.availability,
                    "unavailable",
                )
                self.assertIsNone(result.account_usage.allocated_cpus)
                self.assertEqual(
                    result.usage.allocated_cpus,
                    128,
                )
                self.assertEqual(
                    result.availability,
                    "partial",
                )
                self.assertFalse(
                    any("--account=project" in command for command, _ in self.transport.commands)
                )

    def test_missing_tres_dimensions_are_unknown_and_gpu_total_is_not_doubled(self):
        result = self.cluster(queue="12|RUNNING|cpu=8,node=1,gres/gpu=2,gres/gpu:a100=2")
        self.assertEqual(
            result.usage.allocated_gpus,
            2,
        )
        self.assertIsNone(result.usage.allocated_memory_mb)
        result = self.cluster(queue="12|RUNNING|cpu=8,node=1,gres/gpu=1,gres/gpu:a100=2")
        self.assertEqual(
            result.usage.availability,
            "unavailable",
        )

    def test_optional_qos_fields_can_fail_without_hiding_legacy_limits(self):
        self.cluster()
        original = self.transport.run

        def run(command, **options):
            if any("MaxJobsPA" in item for item in command):
                return subprocess.CompletedProcess(
                    command,
                    1,
                    "",
                    "Unknown field",
                )
            return original(
                command,
                **options,
            )

        self.transport.outputs["qos"] = (
            "normal|||cpu=6400,node=100|cpu=6400||5000|5000|5-00:00:00\n"
        )
        self.transport.run = run
        result = self.queries.read_cluster_scheduling(
            self.transport,
            "target",
            "user",
            "project",
            "normal",
            ["cpu"],
        )
        self.assertEqual(
            result.qos_limits.availability,
            "ready",
        )
        self.assertEqual(
            result.qos_limits.rows[0]["MaxTRESPU"],
            "cpu=6400,node=100",
        )
        self.assertEqual(
            result.qos_limits.unavailable_fields,
            ["MaxJobsPA", "MaxSubmitJobsPA"],
        )
        self.assertEqual(
            result.availability,
            "partial",
        )

    def test_all_selected_gpu_and_cpu_partitions_are_queried_once(self):
        self.cluster()
        self.transport.outputs["partition"] = (
            "PartitionName=cpu State=UP QoS=N/A\n"
            "PartitionName=gpu State=UP QoS=accelerator\n"
            "PartitionName=outside State=UP QoS=unused\n"
        )
        self.transport.outputs["qos"] = "normal||||||||||\naccelerator||||||||||\n"
        result = self.queries.read_cluster_scheduling(
            self.transport,
            "target",
            "user",
            "project",
            "normal",
            ["cpu", "gpu"],
        )
        self.assertEqual(
            {row["PartitionName"] for row in result.partition_limits.rows},
            {"cpu", "gpu"},
        )
        self.assertEqual(
            {row["Name"] for row in result.qos_limits.rows},
            {"normal", "accelerator"},
        )
        self.assertTrue(
            all(
                "--all" in command
                for command, _ in self.transport.commands
                if "partition" in command
            )
        )

    def test_local_submission_identity_is_resolved_by_transport(self):
        self.cluster()
        self.transport.outputs["identity"] = "real-submitter\n"
        result = self.queries.read_cluster_scheduling(
            self.transport,
            "target",
            None,
            None,
            "normal",
            ["cpu"],
        )
        self.assertEqual(
            result.scheduler_user,
            "real-submitter",
        )
        self.assertTrue(
            any("--user=real-submitter" in command for command, _ in self.transport.commands)
        )


class CacheTests(unittest.TestCase):
    def test_parallel_misses_cannot_flood_one_connection(self):
        queries = cache.SchedulerQueryCache()
        entered, release = threading.Event(), threading.Event()

        def producer():
            entered.set()
            self.assertTrue(release.wait(3))
            return "result"

        with ThreadPoolExecutor(max_workers=2) as pool:
            future = pool.submit(
                queries.get,
                "job",
                "connection",
                producer,
            )
            self.assertTrue(entered.wait(3))
            try:
                with self.assertRaises(cache.QueryBusy):
                    queries.get(
                        "other-job",
                        "connection",
                        lambda: self.fail("duplicate query"),
                    )
                self.assertEqual(
                    queries.get(
                        "other-target",
                        "other-connection",
                        lambda: "ok",
                    ),
                    "ok",
                )
            finally:
                release.set()
            self.assertEqual(
                future.result(),
                "result",
            )
        self.assertEqual(
            queries.get(
                "job",
                "connection",
                lambda: self.fail("cache miss"),
            ),
            "result",
        )

    def test_failed_refresh_releases_slot_and_lru_and_ttl_are_bounded(self):
        now = [0]
        queries = cache.SchedulerQueryCache(
            maxsize=2,
            ttl=10,
            timer=lambda: now[0],
        )

        def failure():
            raise RuntimeError("failed")

        with self.assertRaises(RuntimeError):
            queries.get(
                "bad",
                "one",
                failure,
            )
        self.assertEqual(
            queries.get(
                "bad",
                "one",
                lambda: "recovered",
            ),
            "recovered",
        )
        queries.get(
            "two",
            "two",
            lambda: 2,
        )
        queries.get(
            "three",
            "three",
            lambda: 3,
        )
        self.assertEqual(
            len(queries._cache),
            2,
        )
        self.assertEqual(
            queries.get(
                "bad",
                "one",
                lambda: "new",
            ),
            "new",
        )
        now[0] = 11
        self.assertEqual(
            queries.get(
                "bad",
                "one",
                lambda: "expired",
            ),
            "expired",
        )

    def test_global_query_budget_refuses_without_running_producer(self):
        queries = cache.SchedulerQueryCache(max_inflight=1)

        def producer():
            with self.assertRaises(cache.QueryBusy):
                queries.get(
                    "two",
                    "two",
                    lambda: self.fail("limit ignored"),
                )
            return "ok"

        self.assertEqual(
            queries.get(
                "one",
                "one",
                producer,
            ),
            "ok",
        )


class Base(DeclarativeBase):
    pass


class Status(str, enum.Enum):
    QUEUED = "Queued"
    PENDING = "Pending"


class Job(Base):
    __tablename__ = "jobs"
    id: Mapped[str] = mapped_column(
        String,
        primary_key=True,
    )
    execution_target: Mapped[str | None] = mapped_column(
        String,
        nullable=True,
    )
    slurm_job_id: Mapped[str | None] = mapped_column(
        String,
        nullable=True,
    )
    status: Mapped[Status] = mapped_column(Enum(Status))
    created_at: Mapped[str] = mapped_column(String)


class RouteTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite://",
            poolclass=StaticPool,
            connect_args={"check_same_thread": False},
        )
        self.addCleanup(self.engine.dispose)
        Base.metadata.create_all(self.engine)
        self.db = Session(self.engine)
        self.addCleanup(self.db.close)
        self.db.add_all(
            [
                Job(
                    id="one",
                    execution_target="primary",
                    slurm_job_id="37",
                    status=Status.QUEUED,
                    created_at="now",
                ),
                Job(
                    id="other",
                    execution_target="secondary",
                    slurm_job_id="37",
                    status=Status.QUEUED,
                    created_at="now",
                ),
                Job(
                    id="preparing",
                    execution_target="primary",
                    slurm_job_id=None,
                    status=Status.PENDING,
                    created_at="now",
                ),
                Job(
                    id="old",
                    execution_target="retired",
                    slurm_job_id="37",
                    status=Status.QUEUED,
                    created_at="now",
                ),
            ]
        )
        self.db.commit()
        self.config = {
            "execution": {
                "backend": "slurm",
                "target_id": "primary",
                "slurm": {"partition": "cpu", "account": "project", "qos": "normal"},
                "targets": {
                    "secondary": {
                        "transport": {
                            "mode": "ssh",
                            "ssh": {
                                "host": "two",
                                "user": "two",
                                "control_path": "two.sock",
                            },
                        },
                    }
                },
            },
            "transport": {
                "mode": "ssh",
                "ssh": {
                    "host": "one",
                    "user": "one",
                    "control_path": "one.sock",
                },
            },
            "cluster": {"name": "one", "default_runner": "one", "scheduling": {"mode": "tenant"}},
        }
        self.targets = targets_module.execution_targets(self.config)
        self.read_job = Mock(return_value={"availability": "ready", "reason": "Priority"})
        self.read_cluster = Mock(
            side_effect=lambda transport, target_id, *args: schemas.ClusterSchedulingResponse(
                execution_target=target_id,
                backend="slurm",
                availability="ready",
            )
        )

        def get_db():
            return self.db

        from fastapi import HTTPException, Security
        from fastapi.security import HTTPBearer

        bearer = HTTPBearer(auto_error=False)

        def current_user(credentials=Security(bearer)):
            if credentials is None or credentials.credentials != "valid":
                raise HTTPException(status_code=401)
            return SimpleNamespace(id="unrelated-reader")

        namespace = {
            "__name__": __name__,
            "database": SimpleNamespace(get_db=get_db),
            "models": SimpleNamespace(
                Job=Job,
                User=object,
            ),
            "ExecutionTarget": targets_module.ExecutionTarget,
            "requested_execution_target": targets_module.requested_execution_target,
            "select_execution_target": targets_module.select_execution_target,
            "configured_execution_targets": self.targets,
            "magnus_config": self.config,
            "SlurmManager": lambda config: SimpleNamespace(
                transport=config["transport"]["ssh"]["user"]
            ),
            "QueryBusy": cache.QueryBusy,
            "SchedulerQueryCache": cache.SchedulerQueryCache,
            "QueryFailure": load_queries().QueryFailure,
            "read_cluster_scheduling": self.read_cluster,
            "read_job_scheduling": self.read_job,
            "ClusterSchedulingResponse": schemas.ClusterSchedulingResponse,
            "JobSchedulingResponse": schemas.JobSchedulingResponse,
            "get_current_user": current_user,
        }
        path = SERVER / "routers/scheduling.py"
        tree = ast.parse(path.read_text())
        tree.body = [
            node
            for node in tree.body
            if not isinstance(
                node,
                ast.ImportFrom,
            )
            or not node.level
        ]
        exec(
            compile(
                tree,
                str(path),
                "exec",
            ),
            namespace,
        )
        self.namespace = namespace
        app = FastAPI()
        app.include_router(
            namespace["router"],
            prefix="/api",
        )
        self.client = TestClient(app)
        self.addCleanup(self.client.close)
        self.auth = {"Authorization": "Bearer valid"}

    def test_auth_required_and_missing_job_is_404(self):
        self.assertEqual(
            self.client.get("/api/jobs/one/scheduling").status_code,
            401,
        )
        self.assertEqual(
            self.client.get("/api/cluster/scheduling").status_code,
            401,
        )
        self.assertEqual(
            self.client.get(
                "/api/jobs/missing/scheduling",
                headers=self.auth,
            ).status_code,
            404,
        )
        self.read_job.assert_not_called()

    def test_shared_job_reading_and_target_identity_override_legacy_header(self):
        headers = self.auth | {"X-Magnus-Default-Execution-Target": "secondary"}
        first = self.client.get(
            "/api/jobs/one/scheduling",
            headers=headers,
        )
        second = self.client.get(
            "/api/jobs/other/scheduling",
            headers=headers,
        )
        self.assertEqual(
            (first.status_code, second.status_code),
            (200, 200),
        )
        self.assertEqual(
            first.json()["execution_target"],
            "primary",
        )
        self.assertEqual(
            second.json()["execution_target"],
            "secondary",
        )
        self.assertEqual(
            [call.args[0] for call in self.read_job.call_args_list],
            ["one", "two"],
        )
        self.assertEqual(
            first.headers["cache-control"],
            "private, no-store",
        )
        self.assertEqual(
            self.db.get(
                Job,
                "one",
            ).status,
            Status.QUEUED,
        )

    def test_unbound_retired_and_local_jobs_do_not_query_slurm(self):
        response = self.client.get(
            "/api/jobs/preparing/scheduling",
            headers=self.auth,
        )
        self.assertEqual(
            response.json()["availability"],
            "no_scheduler_id",
        )
        response = self.client.get(
            "/api/jobs/old/scheduling",
            headers=self.auth,
        )
        self.assertEqual(
            response.json()["availability"],
            "target_retired",
        )
        self.config["execution"]["backend"] = "docker"
        response = self.client.get(
            "/api/jobs/one/scheduling",
            headers=self.auth,
        )
        self.assertEqual(
            response.json()["availability"],
            "unsupported",
        )
        self.read_job.assert_not_called()

    def test_default_target_header_is_only_for_cluster_selection(self):
        response = self.client.get(
            "/api/cluster/scheduling",
            headers=self.auth | {"X-Magnus-Default-Execution-Target": "secondary"},
        )
        self.assertEqual(
            response.json()["execution_target"],
            "secondary",
        )
        self.assertEqual(
            self.client.get(
                "/api/cluster/scheduling?execution_target=missing",
                headers=self.auth,
            ).status_code,
            400,
        )
        self.assertEqual(
            self.read_cluster.call_count,
            1,
        )

    def test_gpu_partition_union_and_local_identity_do_not_use_runner(self):
        target = self.targets["primary"]
        target.config["execution"]["slurm"]["partition"] = "cpu,short"
        target.config["cluster"]["gpus"] = [{"partition": "gpu"}]
        target.config["transport"] = {"mode": "local"}
        target.config["cluster"]["default_runner"] = "different-runtime-user"
        self.namespace["SlurmManager"] = lambda config: SimpleNamespace(transport="local")
        response = self.client.get(
            "/api/cluster/scheduling",
            headers=self.auth,
        )
        self.assertEqual(
            response.status_code,
            200,
        )
        arguments = self.read_cluster.call_args.args
        self.assertIsNone(arguments[2])
        self.assertEqual(
            arguments[5],
            ["cpu", "short", "gpu"],
        )

    def test_authoritative_scope_uses_submission_partitions(self):
        target = self.targets["primary"]
        target.config["cluster"]["scheduling"]["mode"] = "authoritative"
        target.config["execution"]["slurm"]["partition"] = "cpu, short"
        target.config["cluster"]["gpus"] = [{"partition": "short, gpu"}]
        response = self.client.get(
            "/api/cluster/scheduling",
            headers=self.auth,
        )
        self.assertEqual(
            response.status_code,
            200,
        )
        self.assertEqual(
            self.read_cluster.call_args.args[5],
            ["cpu", "short", "gpu"],
        )

    def test_busy_query_has_bounded_retry_and_does_not_change_job(self):
        self.namespace["_queries"] = SimpleNamespace(get=Mock(side_effect=cache.QueryBusy()))
        response = self.client.get(
            "/api/jobs/one/scheduling",
            headers=self.auth,
        )
        self.assertEqual(
            response.status_code,
            503,
        )
        self.assertEqual(
            response.headers["retry-after"],
            "2",
        )
        self.assertEqual(
            self.db.get(
                Job,
                "one",
            ).status,
            Status.QUEUED,
        )


if __name__ == "__main__":
    unittest.main()
