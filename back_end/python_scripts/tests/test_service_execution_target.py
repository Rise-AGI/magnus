"""Service updates retain execution identity across old and new clients."""
from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
import unittest
import runpy
from pathlib import Path
from unittest.mock import Mock

from fastapi import HTTPException
from sqlalchemy import Boolean, String, create_engine
from sqlalchemy.orm import Mapped, Session, mapped_column

from test_execution_targets import Base, load_method, targets_module


class ServiceRow(Base):
    __tablename__ = "services"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    owner_id: Mapped[str] = mapped_column(String)
    execution_target: Mapped[str | None] = mapped_column(String, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean)
    current_job_id: Mapped[str | None] = mapped_column(String, nullable=True)
    max_concurrency: Mapped[str] = mapped_column(String)


class ServiceTargetUpdateTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://")
        Base.metadata.create_all(self.engine)
        self.db = Session(self.engine)
        self.addCleanup(self.engine.dispose)
        self.addCleanup(self.db.close)
        self.service = ServiceRow(id="service", owner_id="owner", execution_target="second", is_active=True, max_concurrency="4")
        self.db.add(self.service)
        self.db.commit()
        self.apply_defaults = Mock()
        namespace = {
            "requested_execution_target": targets_module.requested_execution_target,
            "assert_target_submission_allowed": Mock(),
            "Depends": lambda dependency: None,
            "database": SimpleNamespace(get_db=lambda: None),
            "get_current_user": lambda: None,
            "Service": ServiceRow,
            "_resolve_target": lambda key: SimpleNamespace(id=key or "first", config={"transport": {"mode": "local"}}),
            "assert_can_manage_resource": Mock(),
            "assert_id_available": Mock(),
            "apply_cluster_defaults": self.apply_defaults,
            "normalize_per_cpu_resources": Mock(),
            "validate_cluster_limits": Mock(),
            "enforce_field_size_limits": Mock(),
            "HTTPException": HTTPException,
            "datetime": datetime,
            "timezone": timezone,
        }
        namespace["service_operation"] = runpy.run_path(str(Path(__file__).resolve().parents[2] / "server/_service_lifecycle.py"))["service_operation"]
        self.update = load_method("routers/services.py", "create_service", namespace)

    def save(self, explicit=False, target=None):
        data = {"execution_target": target, "max_concurrency": "4"}
        request = SimpleNamespace(id="service", execution_target=target, max_concurrency="4", model_fields_set={"execution_target"} if explicit else set(), model_dump=lambda: dict(data))
        return self.update(request, self.db, SimpleNamespace(id="owner"))

    def test_manager_update_preserves_existing_owner(self):
        request = SimpleNamespace(
            id="service", execution_target=None, max_concurrency="4",
            model_fields_set=set(), model_dump=lambda: {"max_concurrency": "4"},
        )
        updated = self.update(request, self.db, SimpleNamespace(id="admin"))
        self.assertEqual(updated.owner_id, "owner")

    def test_legacy_update_keeps_nondefault_target(self):
        self.assertEqual(self.save().execution_target, "second")

    def test_active_target_change_is_rejected_without_mutation(self):
        with self.assertRaises(HTTPException) as caught:
            self.save(explicit=True, target="first")
        self.assertEqual(caught.exception.status_code, 409)
        self.assertEqual(self.service.execution_target, "second")
        self.apply_defaults.assert_not_called()

    def test_same_target_update_is_allowed_while_active(self):
        self.assertEqual(self.save(explicit=True, target="second").execution_target, "second")

    def test_inactive_with_live_job_cannot_switch(self):
        self.service.is_active = False
        self.service.current_job_id = "running-job"
        self.db.commit()
        with self.assertRaises(HTTPException):
            self.save(explicit=True, target="first")

    def test_stopped_service_can_switch_to_default(self):
        self.service.is_active = False
        self.db.commit()
        self.assertEqual(self.save(explicit=True).execution_target, "first")


if __name__ == "__main__":
    unittest.main()
