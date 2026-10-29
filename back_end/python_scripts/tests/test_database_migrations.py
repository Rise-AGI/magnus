"""Run complete schema upgrades against disposable SQLite databases."""
import importlib.util
from pathlib import Path
import sqlite3
import tempfile
import unittest

from sqlalchemy import create_engine, event, inspect


SOURCE = Path(__file__).resolve().parents[2] / "server/_database_migrations.py"
SPEC = importlib.util.spec_from_file_location(
    "database_migrations_under_test",
    SOURCE,
)
MIGRATIONS = importlib.util.module_from_spec(
    SPEC,
)
SPEC.loader.exec_module(
    MIGRATIONS,
)


class DatabaseMigrationTests(unittest.TestCase):
    def setUp(
        self,
    ) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(
            directory.cleanup,
        )
        self.engine = create_engine(
            f"sqlite:///{Path(directory.name) / 'history.db'}",
        )
        self.addCleanup(
            self.engine.dispose,
        )

        @event.listens_for(self.engine, "connect")
        def enable_foreign_keys(
            connection,
            _record,
        ) -> None:
            connection.execute(
                "PRAGMA foreign_keys=ON",
            )

        self.script(
            """
            CREATE TABLE jobs (
                id TEXT PRIMARY KEY,
                container_image VARCHAR(255) NOT NULL,
                status TEXT,
                slurm_job_id TEXT,
                created_at TEXT,
                user_id TEXT,
                result TEXT,
                memory_demand TEXT,
                site_note TEXT DEFAULT 'keep'
            );
            INSERT INTO jobs (rowid, id, container_image, status, result, memory_demand)
                VALUES (42, 'old', 'docker://example/image:old', 'FAILED', 'evidence', '4000M');
            CREATE INDEX site_job_note ON jobs (site_note);
            CREATE TABLE audit (job_id TEXT);
            CREATE TRIGGER job_audit AFTER UPDATE ON jobs BEGIN
                INSERT INTO audit VALUES (new.id);
            END;
            CREATE TABLE explorer_sessions (id TEXT PRIMARY KEY);
            CREATE TABLE services (id TEXT PRIMARY KEY);
            CREATE TABLE cluster_snapshots (id INTEGER PRIMARY KEY);
            """,
        )

    def script(
        self,
        sql: str,
    ) -> None:
        connection = self.engine.raw_connection()
        try:
            connection.executescript(
                sql,
            )
            connection.commit()
        finally:
            connection.close()

    def rows(
        self,
        sql: str,
    ) -> list[tuple]:
        with self.engine.connect() as connection:
            return [
                tuple(row)
                for row in connection.exec_driver_sql(
                    sql,
                ).fetchall()
            ]

    def test_blueprint_origin_is_nullable_and_preserved_across_repeated_upgrade(self):
        with self.engine.begin() as connection:
            connection.exec_driver_sql("CREATE TABLE blueprints (id TEXT PRIMARY KEY, code TEXT)")
            connection.exec_driver_sql("INSERT INTO blueprints VALUES ('original', 'unchanged')")
        MIGRATIONS.run_migrations(self.engine)
        with self.engine.begin() as connection:
            self.assertEqual(
                connection.exec_driver_sql("SELECT code,default_execution_target FROM blueprints").one(),
                ("unchanged", None),
            )
            connection.exec_driver_sql("UPDATE blueprints SET default_execution_target='source'")
        MIGRATIONS.run_migrations(self.engine)
        with self.engine.connect() as connection:
            self.assertEqual(
                connection.exec_driver_sql("SELECT default_execution_target FROM blueprints").scalar_one(),
                "source",
            )

    def test_complete_upgrade_preserves_history_and_site_schema(self) -> None:
        MIGRATIONS.run_migrations(
            self.engine,
        )
        self.assertEqual(
            self.rows(
                "SELECT rowid, id, status, result, memory_demand, site_note, resource_mode, "
                "execution_target, submission_attempt, finalization_status FROM jobs",
            ),
            [(42, "old", "FAILED", "evidence", "4000M", "keep", None, None, None, None)],
        )
        self.script(
            "UPDATE jobs SET result='updated' WHERE id='old';"
            "INSERT INTO jobs (id, container_image, dockerfile) VALUES ('build', NULL, 'FROM x');",
        )
        self.assertEqual(
            self.rows("SELECT job_id FROM audit"),
            [("old",)],
        )
        indexes = {
            item["name"]
            for item in inspect(self.engine).get_indexes("jobs")
        }
        self.assertTrue(
            {"site_job_note", "ix_jobs_finalization_status", "ix_jobs_target_status"} <= indexes,
        )
        self.assertEqual(
            self.rows("PRAGMA foreign_keys"),
            [(1,)],
        )

    def test_repeated_upgrade_does_not_rewrite_rows_or_schema(self) -> None:
        MIGRATIONS.run_migrations(
            self.engine,
        )
        schema = self.rows("SELECT name, sql FROM sqlite_master ORDER BY name")
        history = self.rows("SELECT rowid, * FROM jobs")
        MIGRATIONS.run_migrations(
            self.engine,
        )
        self.assertEqual(
            self.rows("SELECT name, sql FROM sqlite_master ORDER BY name"),
            schema,
        )
        self.assertEqual(
            self.rows("SELECT rowid, * FROM jobs"),
            history,
        )

    def add_legacy_metrics(
        self,
    ) -> None:
        self.script(
            """
            PRAGMA foreign_keys=OFF;
            CREATE TABLE job_metrics (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id TEXT,
                value REAL,
                FOREIGN KEY (job_id) REFERENCES jobs_old (id)
            );
            CREATE INDEX site_metric_value ON job_metrics (value);
            INSERT INTO job_metrics (id, job_id, value) VALUES (7, 'old', 3.5);
            INSERT INTO job_metrics (id, job_id, value) VALUES (9, 'orphan', 2.0);
            UPDATE sqlite_sequence SET seq=100 WHERE name='job_metrics';
            PRAGMA foreign_keys=ON;
            """,
        )

    def test_legacy_metrics_repair_preserves_orphans_rowids_and_sequence(self) -> None:
        self.add_legacy_metrics()
        MIGRATIONS.run_migrations(
            self.engine,
        )
        self.assertEqual(
            self.rows("SELECT id, job_id, value FROM job_metrics ORDER BY id"),
            [(7, "old", 3.5), (9, "orphan", 2.0)],
        )
        self.assertEqual(
            self.rows("PRAGMA foreign_key_check"),
            [("job_metrics", 9, "jobs", 0)],
        )
        self.script(
            "INSERT INTO job_metrics (job_id, value) VALUES ('old', 5.0);",
        )
        self.assertEqual(
            self.rows("SELECT max(id) FROM job_metrics"),
            [(101,)],
        )
        self.assertIn(
            "site_metric_value",
            {item["name"] for item in inspect(self.engine).get_indexes("job_metrics")},
        )
        MIGRATIONS.run_migrations(
            self.engine,
        )
        self.assertEqual(
            self.rows("SELECT count(*) FROM job_metrics"),
            [(3,)],
        )

    def test_reference_to_existing_jobs_old_is_not_rewritten(self) -> None:
        self.add_legacy_metrics()
        self.script(
            "CREATE TABLE jobs_old (id TEXT PRIMARY KEY);",
        )
        MIGRATIONS._repair_legacy_job_metrics_reference(
            self.engine,
        )
        self.assertEqual(
            self.rows("PRAGMA foreign_key_list(job_metrics)")[0][2],
            "jobs_old",
        )

    def test_unrecognized_reference_fails_without_losing_metrics(self) -> None:
        self.script(
            "CREATE TABLE job_metrics (id INTEGER PRIMARY KEY, "
            "job_id TEXT, FOREIGN KEY(job_id) REFERENCES jobs_old(other_id));",
        )
        before = self.rows("SELECT name, sql FROM sqlite_master ORDER BY name")
        with self.assertRaisesRegex(RuntimeError, "unrecognized jobs_old reference"):
            MIGRATIONS._repair_legacy_job_metrics_reference(
                self.engine,
            )
        self.assertEqual(
            self.rows("SELECT name, sql FROM sqlite_master ORDER BY name"),
            before,
        )
        self.assertEqual(
            self.rows("PRAGMA foreign_keys"),
            [(1,)],
        )

    def test_failed_jobs_rebuild_rolls_back_and_restores_foreign_keys(self) -> None:
        self.script(
            "CREATE TABLE jobs_image_build_migration (sentinel TEXT);",
        )
        before = self.rows("SELECT rowid, * FROM jobs")
        schema = self.rows("SELECT name, sql FROM sqlite_master ORDER BY name")
        with self.assertRaises(sqlite3.OperationalError):
            MIGRATIONS._make_job_container_image_nullable(
                self.engine,
            )
        self.assertEqual(
            self.rows("SELECT rowid, * FROM jobs"),
            before,
        )
        self.assertEqual(
            self.rows("SELECT name, sql FROM sqlite_master ORDER BY name"),
            schema,
        )
        self.assertEqual(
            self.rows("PRAGMA foreign_keys"),
            [(1,)],
        )


if __name__ == "__main__":
    unittest.main()
