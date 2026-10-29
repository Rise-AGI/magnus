"""Real SQLite transactions and mappings; no application singleton or executor."""
from __future__ import annotations

import ast
import __future__
from concurrent.futures import ThreadPoolExecutor
import logging
from pathlib import Path
import sys
import tempfile
import threading
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import Mock

from fastapi import HTTPException
from sqlalchemy import create_engine, event, func
from sqlalchemy.orm import declarative_base, sessionmaker

from test_job_resources import load_schemas


SERVER = Path(__file__).resolve().parents[2] / "server"
module = ModuleType("user_lifecycle_test_models")
sys.modules[module.__name__] = module
ns = module.__dict__
ns["Base"] = declarative_base()


def load_leaf(
    relative,
    namespace,
):
    path = SERVER / relative
    tree = ast.parse(path.read_text())
    tree.body = [node for node in tree.body if not isinstance(node, ast.ImportFrom) or not node.level]
    exec(compile(tree, str(path), "exec", dont_inherit=True), namespace)


def load_function(
    relative,
    name,
    namespace,
):
    path = SERVER / relative
    node = next(node for node in ast.walk(ast.parse(path.read_text())) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name)
    node.decorator_list = []
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(path), "exec", flags=__future__.annotations.compiler_flag), namespace)
    return namespace[name]


load_leaf("models/_helpers.py", ns)
for name in ("_user", "_job", "_service", "_blueprint", "_skill", "_image", "_conversation", "_explorer"):
    load_leaf(f"models/{name}.py", ns)
User, Job, JobStatus = (ns[name] for name in ("User", "Job", "JobStatus"))
load_leaf("_user_lifecycle.py", ns)
lock_user_for_write = ns["lock_user_for_write"]


class UserLifecycleTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.engine = create_engine(f"sqlite:///{temporary.name}/state.db", connect_args={"timeout": 3})
        self.addCleanup(self.engine.dispose)
        with self.engine.connect() as connection:
            connection.exec_driver_sql("PRAGMA journal_mode=WAL")
        ns["Base"].metadata.create_all(self.engine)
        self.sessions = sessionmaker(self.engine, autoflush=False)
        self.avatar_delete, self.evict = Mock(), Mock()
        self.namespace = dict(ns)
        self.namespace.update(
            models=SimpleNamespace(**ns),
            Depends=lambda dependency: None,
            database=SimpleNamespace(get_db=lambda: None),
            get_current_user=lambda: None,
            is_admin_user=lambda user: user.id == "admin",
            logger=logging.getLogger(__name__),
            file_custody_manager=SimpleNamespace(delete_entry=self.avatar_delete),
            evict_token_from_cache=self.evict,
        )
        for name in ("_is_ancestor", "_can_manage", "delete_user"):
            load_function("routers/users.py", name, self.namespace)
        self.delete = self.namespace["delete_user"]
        self.admin = SimpleNamespace(id="admin", name="admin")
        with self.sessions() as db:
            db.add(User(id="agent", name="agent", user_type="agent", token="synthetic", avatar_url="/api/files/download/avatar"))
            db.commit()

    def job(
        self,
        status=JobStatus.PENDING,
    ):
        return Job(
            id="job", user_id="agent", task_name="lifecycle", namespace="example",
            repo_name="example", entry_command="true", gpu_type="cpu", gpu_count=0,
            status=status, cpu_count=1, memory_demand="1G", resource_mode="slurm_per_node",
            slurm_job_id="123" if status == JobStatus.RUNNING else None,
        )

    def test_running_and_historical_jobs_prevent_owner_deletion(self):
        schemas = load_schemas()
        for state in (JobStatus.RUNNING, JobStatus.SUCCESS):
            with self.subTest(state=state), self.sessions() as db:
                db.add(self.job(state))
                db.commit()
                with self.assertRaises(HTTPException) as error:
                    self.delete("agent", db, self.admin)
                self.assertEqual(error.exception.status_code, 409)
                db.rollback()
                job = db.get(Job, "job")
                self.assertEqual(job.user_id, "agent")
                self.assertEqual(job.user.token, "synthetic")
                schemas["JobListItem"].model_validate(job, from_attributes=True)
                db.delete(job)
                db.commit()
        self.avatar_delete.assert_not_called()
        self.evict.assert_not_called()

    def test_every_declared_user_reference_is_protected(self):
        # Empty payloads suffice for testing each FK because these are table-level
        # inserts with the actual non-null columns supplied from their SQL types.
        from sqlalchemy import Integer, Enum, DateTime
        from datetime import datetime, timezone
        for table in User.metadata.tables.values():
            for foreign_key in table.foreign_keys:
                if foreign_key.column is not User.__table__.c.id:
                    continue
                if foreign_key.ondelete == "CASCADE":
                    continue
                with self.subTest(table=table.name, column=foreign_key.parent.name), self.sessions() as db:
                    values = {}
                    for column in table.columns:
                        if column.default is not None or column.server_default is not None or column.nullable:
                            continue
                        if isinstance(column.type, Integer):
                            values[column.name] = 1
                        elif isinstance(column.type, Enum):
                            values[column.name] = column.type.enums[0]
                        elif isinstance(column.type, DateTime) or column.type.__class__.__name__ == "UtcDateTime":
                            values[column.name] = datetime.now(timezone.utc)
                        else:
                            values[column.name] = "dependent"
                    values[foreign_key.parent.name] = "agent"
                    db.execute(table.insert().values(**values))
                    with self.assertRaises(HTTPException) as error:
                        self.delete("agent", db, self.admin)
                    self.assertEqual(error.exception.status_code, 409)
                    db.rollback()

    def test_empty_agent_deletion_remains_supported(self):
        with self.sessions() as db:
            self.delete("agent", db, self.admin)
            self.assertIsNone(db.get(User, "agent"))
        self.evict.assert_called_once_with("synthetic")
        self.avatar_delete.assert_called_once_with("avatar")

    def test_owned_credentials_do_not_prevent_agent_deletion(self):
        alias_model = ns["UserTokenAlias"]
        with self.sessions() as db:
            db.add(alias_model(
                digest="imported",
                user_id="agent",
                primary_digest="bound",
            ))
            db.commit()
            self.delete("agent", db, self.admin)
            self.assertIsNone(db.get(User, "agent"))
            self.assertIsNone(db.get(alias_model, "imported"))

    def test_reparenting_updates_both_relationships_and_persists(self):
        with self.sessions() as db:
            first = User(
                id="first",
                name="first",
            )
            second = User(
                id="second",
                name="second",
            )
            child = db.get(
                User,
                "agent",
            )
            child.parent = first
            self.assertEqual(
                first.children,
                [child],
            )
            child.parent = second
            self.assertEqual(
                first.children,
                [],
            )
            self.assertEqual(
                second.children,
                [child],
            )
            db.add_all(
                [first, second],
            )
            db.commit()
        with self.sessions() as db:
            child = db.get(
                User,
                "agent",
            )
            self.assertEqual(
                child.parent_id,
                "second",
            )
            self.assertEqual(
                child.parent.children,
                [child],
            )

    def test_child_collection_changes_update_parent_without_deleting_user(self):
        with self.sessions() as db:
            parent = User(
                id="parent",
                name="parent",
            )
            child = db.get(
                User,
                "agent",
            )
            parent.children.append(
                child,
            )
            self.assertIs(
                child.parent,
                parent,
            )
            db.add(
                parent,
            )
            db.commit()
            self.assertEqual(
                child.parent_id,
                "parent",
            )
            parent.children.remove(
                child,
            )
            self.assertIsNone(
                child.parent,
            )
            db.commit()
        with self.sessions() as db:
            child = db.get(
                User,
                "agent",
            )
            self.assertIsNotNone(
                child,
            )
            self.assertIsNone(
                child.parent_id,
            )

    def test_chat_record_boundaries_preserve_responses_and_ownership(self):
        namespace = self.namespace
        load_leaf("schemas/_user.py", namespace)
        load_leaf("schemas/_chat.py", namespace)
        for name in ("_build_member_response", "_ensure_member", "create_conversation", "_add_member_record", "_create_message_record"):
            load_function("routers/chat.py", name, namespace)
        with self.sessions() as db:
            db.add_all([User(id="peer", name="peer"), User(id="third", name="third")])
            db.commit()
            owner = db.get(User, "agent")
            conversation = namespace["create_conversation"](
                namespace["ConversationCreate"](type="group", member_ids=["peer"]),
                db,
                owner,
            )
            response = namespace["_add_member_record"](
                conversation.id, namespace["AddMemberRequest"](user_id="third"), db, owner,
            )
            self.assertEqual(response.user.id, "third")
            message = namespace["_create_message_record"](
                conversation.id, namespace["MessageCreate"](content="example"), db, owner,
            )
            self.assertEqual((message.content, message.sender.id), ("example", "agent"))
            with self.assertRaises(HTTPException) as error:
                self.delete("agent", db, self.admin)
            self.assertEqual(error.exception.status_code, 409)

    def test_transfer_target_reservation_rejects_a_stale_identity_map(self):
        transfer = load_function("routers/_authz.py", "assert_valid_transfer_target", self.namespace)
        with self.sessions() as requester:
            stale_owner = requester.get(User, "agent")
            with self.sessions() as remover:
                self.delete("agent", remover, self.admin)
            with self.assertRaises(HTTPException) as error:
                transfer(requester, self.admin, stale_owner.id)
            self.assertEqual(error.exception.status_code, 404)

    def test_late_authenticated_create_rechecks_deleted_owner(self):
        schemas = load_schemas()
        self.namespace.update(
            JobSubmission=schemas["JobSubmission"],
            resource_mode_for_execution=schemas["resource_mode_for_execution"],
            magnus_config={"execution": {"image_build": {"enabled": True}}},
            scheduler=SimpleNamespace(for_target=lambda key: SimpleNamespace(
                target=SimpleNamespace(id="default"), config={"execution": {}}, slurm_manager=None,
            )),
            is_local_mode=True,
            apply_cluster_defaults=lambda data, **kwargs: data,
            normalize_per_cpu_resources=lambda *args, **kwargs: None,
            validate_cluster_limits=lambda *args, **kwargs: None,
            enforce_field_size_limits=lambda *args: None,
        )
        create = load_function("routers/jobs.py", "create_job", self.namespace)
        with self.sessions() as requester:
            stale_owner = requester.get(User, "agent")
            with self.sessions() as remover:
                self.delete("agent", remover, self.admin)
            with self.assertRaises(HTTPException) as error:
                create({"task_name": "late", "entry_command": "true"}, stale_owner.id, requester)
            self.assertEqual(error.exception.status_code, 404)
            requester.rollback()
            self.assertEqual(requester.query(Job).count(), 0)

    def test_creation_reservation_blocks_deletion_until_dependency_commits(self):
        attempted = threading.Event()
        with self.sessions() as creator, ThreadPoolExecutor(max_workers=1) as workers:
            lock_user_for_write(creator, "agent")
            def deleting():
                with self.sessions() as db:
                    def before_execute(connection, cursor, statement, parameters, context, many):
                        if statement.startswith("UPDATE users"):
                            attempted.set()
                    event.listen(db.connection(), "before_cursor_execute", before_execute)
                    try:
                        self.delete("agent", db, self.admin)
                    except HTTPException as error:
                        return error.status_code
                    return 204
            result = workers.submit(deleting)
            try:
                self.assertTrue(attempted.wait(1))
                self.assertFalse(result.done())
                creator.add(self.job())
                creator.commit()
            finally:
                creator.rollback()
            self.assertEqual(result.result(timeout=3), 409)
        with self.sessions() as db:
            self.assertEqual(db.get(Job, "job").user.id, "agent")


if __name__ == "__main__":
    unittest.main()
