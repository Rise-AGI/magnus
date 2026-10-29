"""Target grants and legacy endpoint defaults without a live scheduler."""
import logging
from types import SimpleNamespace
import unittest

from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from test_execution_targets import configuration, targets_module
from test_user_lifecycle import load_function, load_leaf, ns


context = dict(ns)
load_leaf("_execution_access.py", context)
authorize = context["assert_target_submission_allowed"]
resolve_request = targets_module.requested_execution_target
User = ns["User"]


class ExecutionAccessTests(unittest.TestCase):
    def setUp(self):
        engine = create_engine("sqlite://")
        self.addCleanup(engine.dispose)
        ns["Base"].metadata.create_all(engine)
        self.db = Session(engine)
        self.addCleanup(self.db.close)
        self.db.add_all([
            User(id="owner", name="owner"),
            User(id="agent", name="agent", parent_id="owner", user_type="agent"),
            User(id="child", name="child", parent_id="agent", user_type="agent"),
            User(id="unrelated", name="unrelated"),
        ])
        self.db.commit()
        self.target = SimpleNamespace(
            id="restricted",
            allowed_users=frozenset({"owner"}),
        )

    def test_grants_include_descendants_but_not_unrelated_users(self):
        for user_id in ("owner", "agent", "child"):
            authorize(
                self.db,
                user_id,
                self.target,
            )
        for user_id in ("unrelated", "missing"):
            with self.subTest(user_id=user_id), self.assertRaises(HTTPException) as error:
                authorize(
                    self.db,
                    user_id,
                    self.target,
                )
            self.assertEqual(error.exception.status_code, 403)

    def test_cycles_and_removed_grants_fail_closed(self):
        self.db.get(User, "agent").parent_id = "child"
        self.db.commit()
        with self.assertRaises(HTTPException):
            authorize(
                self.db,
                "agent",
                self.target,
            )
        self.target.allowed_users = frozenset()
        with self.assertRaises(HTTPException):
            authorize(
                self.db,
                "owner",
                self.target,
            )

    def test_unrestricted_default_does_not_query_database(self):
        authorize(
            None,
            "any-existing-caller",
            SimpleNamespace(allowed_users=None),
        )

    def test_access_lists_are_explicit_per_target_and_validated(self):
        config = configuration()
        config["execution"]["allowed_users"] = ["owner"]
        targets = targets_module.execution_targets(config)
        self.assertEqual(targets["first"].allowed_users, frozenset({"owner"}))
        self.assertIsNone(targets["second"].allowed_users)
        config["execution"]["targets"]["second"]["allowed_users"] = []
        self.assertEqual(
            targets_module.execution_targets(config)["second"].allowed_users,
            frozenset(),
        )
        for value in ("owner", True, {}, [""], [None], [" owner"]):
            with self.subTest(value=value), self.assertRaises(ValueError):
                config["execution"]["targets"]["second"]["allowed_users"] = value
                targets_module.execution_targets(config)

    def test_forwarded_default_does_not_change_persisted_target_selection(self):
        targets = targets_module.execution_targets(configuration())
        for requested in (None, "default"):
            self.assertEqual(resolve_request(requested, "second"), "second")
        self.assertIsNone(resolve_request(None, None))
        self.assertEqual(resolve_request("first", "second"), "first")
        self.assertEqual(targets_module.select_execution_target(targets, None).id, "first")
        with self.assertRaises(ValueError):
            targets_module.select_execution_target(
                targets,
                resolve_request(None, "removed"),
            )

    def test_imported_blueprint_keeps_origin_without_changing_python(self):
        blueprint = ns["Blueprint"](
            id="imported", title="imported", description="example", code="unchanged",
            user_id="owner", default_execution_target="source",
        )
        self.db.add(blueprint)
        job = ns["Job"](
            id="produced", user_id="owner", task_name="example", description="example",
            entry_command="true", namespace="example", repo_name="example", branch="main",
            commit_sha="example", container_image="example", gpu_count=0, gpu_type="cpu",
        )
        self.db.add(job)
        self.db.commit()
        chosen = []
        def create(data, user_id, db, *, default_target):
            chosen.append(resolve_request(data["execution_target"], default_target))
            return job
        returned = {"execution_target": None}
        boundary = dict(context)
        boundary.update(
            models=SimpleNamespace(**ns),
            Depends=lambda dependency: None,
            database=SimpleNamespace(get_db=lambda: None),
            get_current_user=lambda: None,
            blueprint_manager=SimpleNamespace(execute=lambda *args: SimpleNamespace(
                model_dump=lambda: dict(returned),
            )),
            create_job=create,
            logger=logging.getLogger(__name__),
        )
        run = load_function("routers/blueprints.py", "run_blueprint", boundary)
        request = SimpleNamespace(parameters={}, use_preference=False, save_preference=False)
        for explicit in (None, "default", "selected"):
            returned["execution_target"] = explicit
            run("imported", request, self.db, self.db.get(User, "owner"), default_target="forwarded")
        self.assertEqual(chosen, ["source", "source", "selected"])
        self.assertEqual(blueprint.code, "unchanged")
        returned["execution_target"] = None
        blueprint.default_execution_target = ""
        run("imported", request, self.db, self.db.get(User, "owner"), default_target="forwarded")
        self.assertEqual(chosen[-1], "")
        with self.assertRaises(ValueError):
            targets_module.select_execution_target(
                targets_module.execution_targets(configuration()),
                chosen[-1],
            )

    def test_submission_denies_before_scheduler_queries_or_job_writes(self):
        from test_job_resources import load_schemas
        schemas = load_schemas()
        boundary = dict(context)
        boundary.update(
            JobSubmission=schemas["JobSubmission"],
            magnus_config={"execution": {"image_build": {"enabled": True}}},
            requested_execution_target=resolve_request,
            scheduler=SimpleNamespace(for_target=lambda target_id: SimpleNamespace(
                target=self.target,
            )),
        )
        create = load_function("routers/jobs.py", "create_job", boundary)
        with self.assertRaises(HTTPException) as error:
            create(
                {"task_name": "denied", "entry_command": "true"},
                "unrelated",
                self.db,
                default_target="restricted",
            )
        self.assertEqual(error.exception.status_code, 403)
        self.assertEqual(self.db.query(ns["Job"]).count(), 0)


if __name__ == "__main__":
    unittest.main()
