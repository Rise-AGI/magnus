"""SQLite schema upgrades, run before the application starts serving requests.

The caller owns the engine and creates missing ORM tables first. The migration
functions use only that engine, with no dependency on site configuration.
"""
import logging
import re

from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine


logger = logging.getLogger(__name__)


def _repair_legacy_job_metrics_reference(
    engine: Engine,
) -> None:
    """Repair the dangling jobs_old reference left by an old jobs-table rebuild.

    Preserve real orphan metrics and unrelated foreign-key violations. Counts
    stay in SQLite: deployed metrics tables can contain millions of rows.
    """
    connection = engine.raw_connection()
    cursor = connection.cursor()
    foreign_keys = 0
    try:
        foreign_keys = cursor.execute("PRAGMA foreign_keys").fetchone()[0]
        references = cursor.execute("PRAGMA foreign_key_list(job_metrics)").fetchall()
        legacy_references = [row for row in references if row[2].lower() == "jobs_old"]
        if not legacy_references:
            return
        if (
            len(legacy_references) != 1
            or legacy_references[0][3:5] != ("job_id", "id")
            or sum(row[0] == legacy_references[0][0] for row in references) != 1
        ):
            raise RuntimeError("Cannot safely repair job_metrics: unrecognized jobs_old reference")
        if cursor.execute(
            "SELECT 1 FROM sqlite_master WHERE name='jobs_old' COLLATE NOCASE",
        ).fetchone():
            logger.warning("Skipping job_metrics reference repair: jobs_old still exists")
            return
        if not cursor.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='jobs' COLLATE NOCASE",
        ).fetchone():
            raise RuntimeError("Cannot repair job_metrics reference: jobs table is missing")

        cursor.execute("PRAGMA foreign_keys=OFF")
        cursor.execute("BEGIN IMMEDIATE")
        original = cursor.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='job_metrics'",
        ).fetchone()[0]
        # Mask literals/comments before matching SQL syntax. A default or comment
        # containing a FOREIGN KEY example must not become a migration target.
        protected_tokens = re.compile(
            r"'(?:''|[^'])*'|\"(?:\"\"|[^\"])*\"|`(?:``|[^`])*`|\[[^\]]*\]|--[^\r\n]*|/\*[\s\S]*?\*/",
        )
        masked = protected_tokens.sub(
            lambda match: match[0] if match[0].lower() in {
                '"job_metrics"', '"job_id"', '"jobs_old"', '"id"',
            } else " " * len(match[0]),
            original,
        )
        matches = list(re.finditer(
            r'\bFOREIGN\s+KEY\s*\(\s*(?:"job_id"|job_id)\s*\)\s+'
            r'REFERENCES\s+(?P<target>"jobs_old"|jobs_old)\s*\(\s*(?:"id"|id)\s*\)',
            masked,
            flags = re.IGNORECASE,
        ))
        if len(matches) != 1:
            raise RuntimeError("Cannot safely repair job_metrics: unrecognized foreign-key definition")
        start, end = matches[0].span("target")
        create_sql = original[:start] + '"jobs"' + original[end:]
        create_sql, changes = re.subn(
            r'^CREATE\s+TABLE\s+(?:"job_metrics"|job_metrics)(?=\s|\()',
            'CREATE TABLE "job_metrics_reference_migration"',
            create_sql,
            count = 1,
            flags = re.IGNORECASE,
        )
        if changes != 1:
            raise RuntimeError("Cannot safely repair job_metrics: unrecognized table definition")

        counts_sql = (
            "SELECT COUNT(*), COALESCE(SUM(job_metrics.job_id IS NOT NULL AND jobs.id IS NULL), 0) "
            "FROM job_metrics LEFT JOIN jobs ON jobs.id=job_metrics.job_id"
        )
        original_counts = cursor.execute(counts_sql).fetchone()
        uses_autoincrement = bool(re.search(
            r"\bAUTOINCREMENT\b",
            masked,
            flags = re.IGNORECASE,
        ))
        sequence = None
        if uses_autoincrement:
            sequence = cursor.execute("SELECT seq FROM sqlite_sequence WHERE name='job_metrics'").fetchone()
        objects = cursor.execute(
            "SELECT sql FROM sqlite_master WHERE tbl_name='job_metrics' "
            "AND type IN ('index','trigger') AND sql IS NOT NULL",
        ).fetchall()
        columns = cursor.execute("PRAGMA table_info(job_metrics)").fetchall()
        quoted_columns = ", ".join('"' + row[1].replace('"', '""') + '"' for row in columns)
        if not re.search(
            r"\bWITHOUT\s+ROWID\b",
            masked,
            flags = re.IGNORECASE,
        ):
            column_names = {row[1].lower() for row in columns}
            rowid_alias = next(
                (name for name in ("_rowid_", "rowid", "oid") if name not in column_names),
                None,
            )
            if rowid_alias is None:
                raise RuntimeError("Cannot preserve job_metrics rowids: all SQLite rowid aliases are shadowed")
            quoted_columns = f'"{rowid_alias}", ' + quoted_columns
        cursor.execute(create_sql)
        cursor.execute(
            f"INSERT INTO job_metrics_reference_migration ({quoted_columns}) "
            f"SELECT {quoted_columns} FROM job_metrics",
        )
        cursor.execute("DROP TABLE job_metrics")
        cursor.execute("ALTER TABLE job_metrics_reference_migration RENAME TO job_metrics")
        if uses_autoincrement:
            cursor.execute("DELETE FROM sqlite_sequence WHERE name='job_metrics'")
            if sequence is not None:
                cursor.execute(
                    "INSERT INTO sqlite_sequence(name, seq) VALUES ('job_metrics', ?)",
                    sequence,
                )
        for (ddl,) in objects:
            cursor.execute(ddl)
        if cursor.execute(counts_sql).fetchone() != original_counts:
            raise RuntimeError("Job metrics counts changed during reference repair; migration rolled back")
        connection.commit()
        logger.info(
            "Repaired job_metrics foreign key to jobs, preserving %s rows",
            original_counts[0],
        )
    except BaseException:
        connection.rollback()
        raise
    finally:
        cursor.execute(f"PRAGMA foreign_keys={int(foreign_keys)}")
        cursor.close()
        connection.close()


def _make_job_container_image_nullable(
    engine: Engine,
) -> None:
    """SQLite table rebuild preserving the deployed schema, indexes and triggers.

    Recreate from sqlite_master rather than the current ORM: old sites may have
    additional columns or indexes. No user data or foreign-key targets are renamed.
    The full copy/drop/rename is one transaction, before serving any requests.
    """
    connection = engine.raw_connection()
    cursor = connection.cursor()
    foreign_keys = 0
    try:
        foreign_keys = cursor.execute("PRAGMA foreign_keys").fetchone()[0]
        columns = cursor.execute("PRAGMA table_info(jobs)").fetchall()
        if not any(row[1] == "container_image" and row[3] for row in columns):
            return
        cursor.execute("PRAGMA foreign_keys=OFF")
        cursor.execute("BEGIN IMMEDIATE")
        existing_violations = set(cursor.execute("PRAGMA foreign_key_check").fetchall())
        original = cursor.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='jobs'").fetchone()[0]
        create_sql, changes = re.subn(
            r'((?:"container_image"|container_image)\s+[A-Za-z]+(?:\(\d+\))?)\s+NOT\s+NULL\b',
            r'\1',
            original,
            count = 1,
            flags = re.IGNORECASE,
        )
        if changes != 1:
            raise RuntimeError("Cannot safely migrate jobs.container_image: unrecognized table definition")
        create_sql, changes = re.subn(
            r'^CREATE\s+TABLE\s+(?:"jobs"|jobs)(?=\s|\()',
            'CREATE TABLE "jobs_image_build_migration"',
            create_sql,
            count = 1,
            flags = re.IGNORECASE,
        )
        if changes != 1:
            raise RuntimeError("Cannot safely migrate jobs: unrecognized table name")
        objects = cursor.execute(
            "SELECT sql FROM sqlite_master WHERE tbl_name='jobs' AND type IN ('index','trigger') AND sql IS NOT NULL"
        ).fetchall()
        quoted_columns = ", ".join('"' + row[1].replace('"', '""') + '"' for row in columns)
        if "WITHOUT ROWID" not in original.upper():
            # Keep rowids too: foreign_key_check reports them, and old sites may
            # contain pre-existing orphan rows while enforcement was disabled.
            quoted_columns = '"_rowid_", ' + quoted_columns
        cursor.execute(create_sql)
        cursor.execute(f"INSERT INTO jobs_image_build_migration ({quoted_columns}) SELECT {quoted_columns} FROM jobs")
        cursor.execute("DROP TABLE jobs")
        cursor.execute("ALTER TABLE jobs_image_build_migration RENAME TO jobs")
        for (ddl,) in objects:
            cursor.execute(ddl)
        if set(cursor.execute("PRAGMA foreign_key_check").fetchall()) - existing_violations:
            raise RuntimeError("Foreign-key check failed while migrating jobs; migration rolled back")
        connection.commit()
        logger.info("Migrated jobs.container_image to nullable, preserving rows and indexes")
    except BaseException:
        connection.rollback()
        raise
    finally:
        cursor.execute(f"PRAGMA foreign_keys={int(foreign_keys)}")
        cursor.close()
        connection.close()


def run_migrations(
    engine: Engine,
) -> None:
    inspector = inspect(engine)
    columns = [col["name"] for col in inspector.get_columns("explorer_sessions")]
    if "is_shared" not in columns:
        logger.info("🔧 Adding is_shared column to explorer_sessions table...")
        with engine.connect() as conn:
            conn.execute(text("ALTER TABLE explorer_sessions ADD COLUMN is_shared BOOLEAN DEFAULT 0"))
            conn.commit()
        logger.info("✅ Migration completed.")

    job_columns = [col["name"] for col in inspector.get_columns("jobs")]
    if "time_limit" not in job_columns:
        logger.info("🔧 Adding time_limit column to jobs table...")
        with engine.connect() as conn:
            conn.execute(text("ALTER TABLE jobs ADD COLUMN time_limit INTEGER"))
            conn.commit()
        logger.info("✅ Migration completed.")

    # 多节点字段（node_count / tasks_per_node）。老库 ADD COLUMN 为 NULL，代码把
    # NULL 当 1（单节点），与历史行为一致。
    for _multinode_col in ("node_count", "tasks_per_node"):
        if _multinode_col not in job_columns:
            logger.info(f"🔧 Adding {_multinode_col} column to jobs table...")
            with engine.connect() as conn:
                conn.execute(text(f"ALTER TABLE jobs ADD COLUMN {_multinode_col} INTEGER"))
                conn.commit()
            logger.info("✅ Migration completed.")

    for column in ("dockerfile", "build_result", "resource_mode", "finalization_status", "execution_target", "submission_attempt"):
        if column not in job_columns:
            with engine.begin() as conn:
                conn.execute(text(f"ALTER TABLE jobs ADD COLUMN {column} TEXT"))
    for table_name in ("services", "cluster_snapshots"):
        columns = {column["name"] for column in inspector.get_columns(table_name)}
        if "execution_target" not in columns:
            with engine.begin() as conn:
                conn.execute(text(f"ALTER TABLE {table_name} ADD COLUMN execution_target TEXT"))
    with engine.begin() as conn:
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_cluster_snapshots_execution_target ON cluster_snapshots (execution_target)"))
    _repair_legacy_job_metrics_reference(
        engine,
    )
    _make_job_container_image_nullable(
        engine,
    )

    # 索引由 models 声明，create_all 只对新库生效、不会给已存在的 jobs 表补建。
    # 站点上线前落库的老库需要在这里幂等补齐，否则热点读路径（列表分页 / cluster
    # 视图 / 调度循环）会一直全表扫描。索引对应的查询语义见 models/_job.py。
    job_indexes = {
        "ix_jobs_target_status": "CREATE INDEX IF NOT EXISTS ix_jobs_target_status ON jobs (execution_target, status)",
        "ix_jobs_target_slurm_id": "CREATE INDEX IF NOT EXISTS ix_jobs_target_slurm_id ON jobs (execution_target, slurm_job_id)",
        "ix_jobs_created_at": "CREATE INDEX IF NOT EXISTS ix_jobs_created_at ON jobs (created_at)",
        "ix_jobs_user_id_created_at": "CREATE INDEX IF NOT EXISTS ix_jobs_user_id_created_at ON jobs (user_id, created_at)",
        "ix_jobs_status": "CREATE INDEX IF NOT EXISTS ix_jobs_status ON jobs (status)",
        "ix_jobs_slurm_job_id": "CREATE INDEX IF NOT EXISTS ix_jobs_slurm_job_id ON jobs (slurm_job_id)",
        "ix_jobs_finalization_status": "CREATE INDEX IF NOT EXISTS ix_jobs_finalization_status ON jobs (finalization_status)",
        "ix_jobs_submission_attempt": "CREATE INDEX IF NOT EXISTS ix_jobs_submission_attempt ON jobs (submission_attempt)",
    }
    existing_job_indexes = {idx["name"] for idx in inspector.get_indexes("jobs")}
    missing_job_indexes = {
        name: ddl
        for name, ddl in job_indexes.items()
        if name not in existing_job_indexes
    }
    if missing_job_indexes:
        logger.info(f"🔧 Creating {len(missing_job_indexes)} missing index(es) on jobs table: {', '.join(missing_job_indexes)}...")
        with engine.connect() as conn:
            for ddl in missing_job_indexes.values():
                conn.execute(text(ddl))
            conn.commit()
        logger.info("✅ Migration completed.")
