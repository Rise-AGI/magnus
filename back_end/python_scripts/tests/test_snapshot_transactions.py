"""Snapshot database retries preserve one sample without repeating scheduler I/O."""
from datetime import datetime, timedelta, timezone
import logging
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from sqlalchemy import DateTime, Integer, String, create_engine, event
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

from test_database_transactions import busy_error, is_sqlite_busy, write_transaction
from test_job_finalization import load_method


class Base(DeclarativeBase):
    pass


class Job(Base):
    __tablename__ = "jobs"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    slurm_job_id: Mapped[str] = mapped_column(String)


class Snapshot(Base):
    __tablename__ = "snapshots"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    execution_target: Mapped[str] = mapped_column(String)
    total_gpus: Mapped[int] = mapped_column(Integer)
    slurm_used_gpus: Mapped[int] = mapped_column(Integer)
    magnus_used_gpus: Mapped[int] = mapped_column(Integer)
    timestamp: Mapped[datetime] = mapped_column(DateTime)


class SnapshotTransactionTests(unittest.TestCase):
    def test_commit_busy_rolls_back_and_keeps_single_sample(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        engine = create_engine(f"sqlite:///{Path(temp.name) / 'db'}")
        self.addCleanup(engine.dispose)
        Base.metadata.create_all(engine)
        sessions = sessionmaker(bind=engine)
        with sessions() as db:
            db.add(Job(id="job", slurm_job_id="12"))
            db.commit()
        attempts = []

        def reject_first(db):
            db.flush()
            attempts.append(db)
            if len(attempts) == 1:
                raise busy_error()

        event.listen(sessions, "before_commit", reject_first)
        manager = SimpleNamespace(
            get_node_snapshot=Mock(return_value=SimpleNamespace(nodes=(), total_gpus=4)),
            get_all_running_tasks=Mock(return_value=[{"id": "12", "gpu_count": 2}]),
        )
        actor = SimpleNamespace(
            config={"server": {"scheduler": {"snapshot_interval": 1}}},
            target=SimpleNamespace(id="local"),
            slurm_manager=manager,
            last_snapshot_time=datetime.now(timezone.utc) - timedelta(seconds=5),
            _job_query=lambda db, *columns: db.query(*columns),
        )
        record = load_method(
            "_scheduler/_sync.py",
            "_record_snapshot",
            {
                "Session": Session,
                "SessionLocal": sessions,
                "Job": Job,
                "ClusterSnapshot": Snapshot,
                "datetime": datetime,
                "timezone": timezone,
                "is_local_mode": False,
                "resource_partitions": lambda config: (),
                "validate_allocation_visibility": lambda *args, **kwargs: None,
                "write_transaction": write_transaction,
                "is_sqlite_busy": is_sqlite_busy,
                "logger": logging.getLogger(__name__),
            },
        )
        record(actor)
        with sessions() as db:
            rows = db.query(Snapshot).all()
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0].magnus_used_gpus, 2)
        self.assertEqual(len(attempts), 2)
        self.assertIsNot(attempts[0], attempts[1])
        manager.get_all_running_tasks.assert_called_once()
        manager.get_node_snapshot.assert_called_once()
        record(actor)
        manager.get_all_running_tasks.assert_called_once()
