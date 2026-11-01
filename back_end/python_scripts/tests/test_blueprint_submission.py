"""Real SQLite acceptance transactions without evaluating code or running Jobs."""

import ast
import hashlib
import json
import sys
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any, Dict
from unittest.mock import Mock

from fastapi import Depends, HTTPException, Response
from pydantic import BaseModel, Field
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import sessionmaker
from test_job_resources import load_schemas
from test_user_lifecycle import SERVER, load_function, load_leaf, ns

if "JobAcceptance" not in ns:
    load_leaf(
        "models/_submission.py",
        ns,
    )
module = ModuleType(
    "blueprint_acceptance_tests",
)
sys.modules[module.__name__] = module
namespace = module.__dict__
namespace.update(
    models=SimpleNamespace(
        **ns,
    ),
    lock_user_for_write=ns["lock_user_for_write"],
)
load_leaf(
    "_blueprint_signature.py",
    namespace,
)
load_leaf(
    "_submission.py",
    namespace,
)
SubmissionContext = namespace["SubmissionContext"]
BlueprintRun = namespace["BlueprintRun"]


class BlueprintSubmissionTests(unittest.TestCase):
    def test_schema_identity_and_accepted_lookup_keep_existing_response_shapes(
        self,
    ):
        load_leaf(
            "_blueprint_protocol.py",
            self.boundary,
        )
        self.boundary["signature_hash"] = namespace["signature_hash"]
        self.boundary["_analyze_schema"] = lambda code: [
            self.boundary["BlueprintParamSchema"](
                key="dt",
                label="Dt",
                type="float",
                default=1e-7,
            )
        ]
        schema_route = load_function(
            "routers/blueprints.py",
            "get_blueprint_schema",
            self.boundary,
        )
        response = Response()
        with self.sessions() as db:
            schema = schema_route(
                "blueprint",
                response,
                db,
                self.user,
            )
        self.assertIsInstance(
            schema,
            list,
        )
        self.assertEqual(
            response.headers["X-Magnus-Blueprint-Revision"],
            hashlib.sha256(
                b"frozen-source",
            ).hexdigest(),
        )
        self.assertEqual(
            response.headers["X-Magnus-Submission-Idempotency"],
            "1",
        )
        self.assertEqual(
            response.headers["X-Magnus-Blueprint-Signature"],
            namespace["signature_hash"](
                [
                    item.model_dump(
                        mode="json",
                    )
                    for item in schema
                ],
            ),
        )
        job_id = self.submit()
        lookup = load_function(
            "routers/jobs.py",
            "get_accepted_submission",
            self.boundary,
        )
        with self.sessions() as db:
            self.assertEqual(
                lookup(
                    "retry",
                    db,
                    self.user,
                ).id,
                job_id,
            )
            with self.assertRaises(
                HTTPException,
            ) as error:
                lookup(
                    "retry",
                    db,
                    SimpleNamespace(
                        id="another-user",
                    ),
                )
            self.assertEqual(
                error.exception.status_code,
                404,
            )

    def setUp(
        self,
    ):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(
            temporary.cleanup,
        )
        self.engine = create_engine(
            f"sqlite:///{Path(temporary.name) / 'db'}",
            connect_args={"timeout": 5},
        )
        self.addCleanup(
            self.engine.dispose,
        )
        with self.engine.begin() as connection:
            connection.execute(
                text(
                    "PRAGMA journal_mode=WAL",
                ),
            )
        ns["Base"].metadata.create_all(
            self.engine,
        )
        self.sessions = sessionmaker(
            self.engine,
            autoflush=False,
        )
        self.user = SimpleNamespace(
            id="agent",
        )
        with self.sessions() as db:
            db.add(
                ns["User"](
                    id="agent",
                    name="agent",
                    user_type="agent",
                    token="synthetic",
                ),
            )
            db.add(
                ns["Blueprint"](
                    id="blueprint",
                    title="sample",
                    description="",
                    code="frozen-source",
                    user_id="agent",
                ),
            )
            db.commit()
        self.schemas = load_schemas()
        self.precheck = Mock(
            return_value=True,
        )
        config = {"execution": {"image_build": {"enabled": True}}}
        self.boundary = dict(
            ns,
        )
        self.boundary.update(
            models=SimpleNamespace(
                **ns,
            ),
            SubmissionContext=SubmissionContext,
            BlueprintRun=BlueprintRun,
            code_revision=namespace["code_revision"],
            save_preference=namespace["save_preference"],
            replace=replace,
            JobSubmission=self.schemas["JobSubmission"],
            magnus_config=config,
            resource_mode_for_execution=lambda cfg: "slurm_per_node",
            scheduler=SimpleNamespace(
                for_target=lambda key: SimpleNamespace(
                    target=SimpleNamespace(
                        id=key or "default",
                    ),
                    config=config,
                    slurm_manager=SimpleNamespace(
                        has_schedulable_node=self.precheck,
                    ),
                ),
            ),
            assert_target_submission_allowed=lambda *args: None,
            is_local_mode=False,
            apply_cluster_defaults=lambda data, **kw: None,
            normalize_per_cpu_resources=lambda data, **kw: None,
            validate_cluster_limits=lambda data, **kw: None,
            enforce_field_size_limits=lambda *args: None,
            serialize_json=json.dumps,
            deserialize_json=json.loads,
            _compute_signature_hash=lambda code: "schema",
            blueprint_manager=SimpleNamespace(
                execute=lambda code, args: self.schemas["JobSubmission"](
                    task_name="example",
                    entry_command="true",
                ),
            ),
            BlueprintUnavailable=type(
                "BlueprintUnavailable",
                (RuntimeError,),
                {},
            ),
            BaseModel=BaseModel,
            Field=Field,
            Any=Any,
            Dict=Dict,
            Depends=Depends,
            database=SimpleNamespace(
                get_db=lambda: None,
            ),
            get_current_user=lambda: None,
            assert_can_manage_resource=lambda *args, **kwargs: None,
        )
        path = SERVER / "routers/blueprints.py"
        tree = ast.parse(
            path.read_text(),
        )
        run_request = next(
            node
            for node in tree.body
            if isinstance(
                node,
                ast.ClassDef,
            )
            and node.name == "BlueprintRunRequest"
        )
        exec(
            compile(
                ast.Module(
                    body=[run_request],
                    type_ignores=[],
                ),
                str(
                    path,
                ),
                "exec",
            ),
            self.boundary,
        )
        self.request_type = self.boundary["BlueprintRunRequest"]
        self.request_type.model_rebuild(
            _types_namespace=self.boundary,
        )
        self.create = load_function(
            "routers/jobs.py",
            "create_job",
            self.boundary,
        )
        self.run = load_function(
            "routers/blueprints.py",
            "run_blueprint",
            self.boundary,
        )
        self.delete = load_function(
            "routers/blueprints.py",
            "delete_blueprint",
            self.boundary,
        )

    def submit(
        self,
        key="retry",
        **overrides,
    ):
        request = self.request_type(
            parameters={"value": 1},
            save_preference=True,
            **overrides,
        )
        with self.sessions() as db:
            return self.run(
                "blueprint",
                request,
                db,
                self.user,
                idempotency_key=key,
            )["job_id"]

    def test_lost_response_replays_after_preference_and_blueprint_changes(
        self,
    ):
        job_id = self.submit()
        with self.sessions() as db:
            preference = db.query(
                ns["BlueprintUserPreference"],
            ).one()
            preference.cached_params = '{"later": true}'
            db.get(
                ns["Blueprint"],
                "blueprint",
            ).code = "new-source"
            db.commit()
        self.precheck.side_effect = AssertionError(
            "replay queried scheduler",
        )
        self.assertEqual(
            self.submit(),
            job_id,
        )
        with self.sessions() as db:
            self.assertEqual(
                db.query(
                    ns["Job"],
                ).count(),
                1,
            )
            self.assertEqual(
                db.query(
                    ns["BlueprintUserPreference"],
                )
                .one()
                .cached_params,
                '{"later": true}',
            )
            record = db.query(
                ns["JobAcceptance"],
            ).one()
            self.assertEqual(
                record.blueprint_revision,
                hashlib.sha256(
                    b"frozen-source",
                ).hexdigest(),
            )

    def test_same_key_different_parameters_conflicts(
        self,
    ):
        self.submit()
        with (
            self.sessions() as db,
            self.assertRaises(
                HTTPException,
            ) as error,
        ):
            self.run(
                "blueprint",
                self.request_type(
                    parameters={"value": 2},
                    save_preference=True,
                ),
                db,
                self.user,
                idempotency_key="retry",
            )
        self.assertEqual(
            error.exception.status_code,
            409,
        )

    def test_preference_failure_rolls_back_job_and_receipt(
        self,
    ):
        def fail(
            *args,
        ):
            raise RuntimeError(
                "injected preference failure",
            )

        event.listen(
            ns["BlueprintUserPreference"],
            "before_insert",
            fail,
        )
        try:
            with self.assertRaisesRegex(
                RuntimeError,
                "preference failure",
            ):
                self.submit()
        finally:
            event.remove(
                ns["BlueprintUserPreference"],
                "before_insert",
                fail,
            )
        with self.sessions() as db:
            self.assertEqual(
                db.query(
                    ns["Job"],
                ).count(),
                0,
            )
            self.assertEqual(
                db.query(
                    ns["JobAcceptance"],
                ).count(),
                0,
            )
        self.submit()

    def test_concurrent_retry_is_one_job_and_one_preference(
        self,
    ):
        barrier = threading.Barrier(
            2,
        )
        self.precheck.side_effect = lambda: (
            barrier.wait(
                timeout=3,
            ),
            True,
        )[1]
        with ThreadPoolExecutor(
            max_workers=2,
        ) as workers:
            results = list(
                workers.map(
                    lambda _: self.submit(),
                    range(
                        2,
                    ),
                ),
            )
        self.assertEqual(
            results[0],
            results[1],
        )
        with self.sessions() as db:
            self.assertEqual(
                db.query(
                    ns["Job"],
                ).count(),
                1,
            )
            self.assertEqual(
                db.query(
                    ns["BlueprintUserPreference"],
                ).count(),
                1,
            )
            self.assertEqual(
                db.query(
                    ns["JobAcceptance"],
                ).count(),
                1,
            )

    def test_precheck_failure_replays_concurrent_acceptance(
        self,
    ):
        accepted = []

        def commit_then_reject():
            self.precheck.side_effect = None
            accepted.append(
                self.submit(),
            )
            return False

        self.precheck.side_effect = commit_then_reject
        self.assertEqual(
            self.submit(),
            accepted[0],
        )

    def test_schema_failure_replays_concurrent_acceptance(
        self,
    ):
        accepted = []

        def commit_then_fail(
            code,
        ):
            self.boundary["_compute_signature_hash"] = lambda code: "schema"
            accepted.append(
                self.submit(),
            )
            raise self.boundary["BlueprintUnavailable"](
                "runtime temporarily unavailable",
            )

        self.boundary["_compute_signature_hash"] = commit_then_fail
        self.assertEqual(
            self.submit(),
            accepted[0],
        )

    def test_revision_conflict_and_concurrent_edit_create_no_job(
        self,
    ):
        with self.assertRaises(
            HTTPException,
        ) as error:
            self.submit(
                blueprint_revision="0" * 64,
            )
        self.assertEqual(
            error.exception.status_code,
            409,
        )

        def edit():
            with self.sessions() as db:
                db.get(
                    ns["Blueprint"],
                    "blueprint",
                ).code = "new-source"
                db.commit()
            return True

        self.precheck.side_effect = edit
        with self.assertRaises(
            HTTPException,
        ) as error:
            self.submit()
        self.assertEqual(
            error.exception.status_code,
            409,
        )
        with self.sessions() as db:
            self.assertEqual(
                db.query(
                    ns["Job"],
                ).count(),
                0,
            )

    def test_delete_removes_preferences_but_keeps_history_and_replay(
        self,
    ):
        job_id = self.submit()
        with self.sessions() as db:
            self.delete(
                "blueprint",
                db,
                self.user,
            )
        with self.sessions() as db:
            self.assertEqual(
                db.query(
                    ns["BlueprintUserPreference"],
                ).count(),
                0,
            )
            self.assertIsNotNone(
                db.get(
                    ns["Job"],
                    job_id,
                ),
            )
        self.assertEqual(
            self.submit(),
            job_id,
        )

    def test_legacy_without_key_creates_independent_jobs(
        self,
    ):
        first = self.submit(
            key=None,
        )
        second = self.submit(
            key=None,
        )
        self.assertNotEqual(
            first,
            second,
        )
        with self.sessions() as db:
            self.assertEqual(
                db.query(
                    ns["BlueprintUserPreference"],
                ).count(),
                1,
            )


if __name__ == "__main__":
    unittest.main()
