"""SQLite contention uses fresh, bounded transactions and never retries I/O faults."""
import logging
from pathlib import Path
import runpy
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from sqlalchemy import create_engine, event, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import sessionmaker

from test_job_finalization import SERVER

TRANSACTIONS = runpy.run_path(str(SERVER / "_database_transactions.py"))
write_transaction = TRANSACTIONS["write_transaction"]
is_sqlite_busy = TRANSACTIONS["is_sqlite_busy"]


def busy_error():
    error = sqlite3.OperationalError("database is locked")
    error.sqlite_errorcode = sqlite3.SQLITE_BUSY
    return OperationalError("update", None, error)


class DatabaseTransactionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "db.sqlite"
        self.engine = create_engine(
            f"sqlite:///{self.path}",
            connect_args={"timeout": 0.01},
        )
        self.addCleanup(self.engine.dispose)
        self.sessions = sessionmaker(bind=self.engine)
        with self.engine.begin() as db:
            db.exec_driver_sql("PRAGMA journal_mode=WAL")
            db.exec_driver_sql("CREATE TABLE counts (value INTEGER)")
            db.exec_driver_sql("INSERT INTO counts VALUES (0)")

    def value(self):
        with self.engine.connect() as db:
            return db.exec_driver_sql("SELECT value FROM counts").scalar()

    def test_real_writer_releases_between_attempts(self):
        holder = sqlite3.connect(self.path)
        self.addCleanup(holder.close)
        holder.execute("BEGIN IMMEDIATE")
        calls = []

        def action(db):
            calls.append(db)
            db.execute(text("UPDATE counts SET value=value+1"))
            return 1

        def release(_):
            self.assertFalse(calls[-1].in_transaction())
            holder.rollback()

        with patch.object(TRANSACTIONS["time"], "sleep", release):
            self.assertEqual(write_transaction(self.sessions, action, operation="test"), 1)
        self.assertEqual(len(calls), 2)
        self.assertIsNot(calls[0], calls[1])
        self.assertEqual(self.value(), 1)

    def test_persistent_busy_is_bounded(self):
        holder = sqlite3.connect(self.path)
        self.addCleanup(holder.close)
        holder.execute("BEGIN IMMEDIATE")
        calls = []

        def action(db):
            calls.append(db)
            db.execute(text("UPDATE counts SET value=1"))

        with patch.object(TRANSACTIONS["time"], "sleep"), self.assertRaises(OperationalError):
            write_transaction(self.sessions, action, operation="test")
        self.assertEqual(len(calls), 3)
        self.assertTrue(all(not db.in_transaction() for db in calls))
        holder.rollback()
        self.assertEqual(self.value(), 0)

    def test_busy_during_commit_rolls_back_before_replaying(self):
        attempts = []

        def action(db):
            attempts.append(db)
            db.execute(text("UPDATE counts SET value=value+1"))
            if len(attempts) == 1:
                def fail(_):
                    raise busy_error()
                event.listen(db, "before_commit", fail, once=True)

        with patch.object(TRANSACTIONS["time"], "sleep"):
            write_transaction(self.sessions, action, operation="test")
        self.assertEqual(len(attempts), 2)
        self.assertEqual(self.value(), 1)

    def test_busy_snapshot_restarts_entire_transaction(self):
        calls = []

        def action(db):
            calls.append(db)
            db.execute(text("BEGIN"))
            value = db.execute(text("SELECT value FROM counts")).scalar()
            if len(calls) == 1:
                with self.engine.begin() as writer:
                    writer.exec_driver_sql("UPDATE counts SET value=5")
            db.execute(text("UPDATE counts SET value=:value"), {"value": value + 1})

        with patch.object(TRANSACTIONS["time"], "sleep"):
            write_transaction(self.sessions, action, operation="test")
        self.assertEqual(len(calls), 2)
        self.assertEqual(self.value(), 6)
        self.assertIsNot(calls[0], calls[1])

    def test_non_busy_errors_do_not_retry(self):
        for code in (sqlite3.SQLITE_LOCKED, sqlite3.SQLITE_IOERR, sqlite3.SQLITE_FULL):
            calls = []
            def action(db):
                calls.append(db)
                db.execute(text("UPDATE counts SET value=7"))
                error = busy_error()
                error.orig.sqlite_errorcode = code
                raise error
            with self.assertRaises(OperationalError):
                write_transaction(self.sessions, action, operation="test")
            self.assertEqual(len(calls), 1)
            self.assertEqual(self.value(), 0)
        self.assertFalse(is_sqlite_busy(sqlite3.OperationalError("database is locked")))
        self.assertFalse(is_sqlite_busy(RuntimeError("database is locked")))


if __name__ == "__main__":
    unittest.main()
