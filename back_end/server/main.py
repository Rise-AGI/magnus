# back_end/server/main.py
import anyio
import asyncio
import shutil
import re
import json
import logging
import uvicorn
import argparse
import concurrent.futures
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from library import *
from .routers import *
from .routers.ws_chat import ws_router
from ._magnus_config import *
from . import models
from .database import *
from ._scheduler import scheduler
from ._service_manager import service_manager
from ._file_custody_manager import file_custody_manager
from ._metrics_collector import DockerMetricsCollector
from ._resource_manager import resource_manager
from .routers.images import recover_stuck_images
from ._feishu_client import feishu_client
from ._blocking_io import _run_blocking_io
from ._lifecycle import cancel_background_tasks, lifespan_resources


class EndpointFilter(logging.Filter):
    SUPPRESSED_GET_PATHS = {
        "/api/cluster/my-active-jobs",
        "/api/cluster/stats",
        "/api/conversations",
        "/api/explorer/sessions",
        "/api/skills",
        "/api/users",
        "/api/users/roster",
    }
    SUPPRESSED_GET_PREFIXES = (
        "/api/blueprints",
        "/api/jobs",
        "/api/services",
    )
    SUPPRESSED_MESSAGE_FRAGMENTS = (
        "/logs HTTP",
    )

    @staticmethod
    def _extract_access_request(record: logging.LogRecord):
        args = record.args
        if not isinstance(args, tuple) or len(args) < 5:
            return None

        _, method, full_path, _, status_code = args[:5]
        if not isinstance(method, str) or not isinstance(full_path, str):
            return None

        try:
            parsed_status_code = int(status_code)
        except (TypeError, ValueError):
            return None

        normalized_path = full_path.split("?", 1)[0]
        return method.upper(), normalized_path, parsed_status_code

    def filter(self, record: logging.LogRecord)-> bool:
        access_request = self._extract_access_request(record)
        if access_request is not None:
            method, path, status_code = access_request

            # 保留真实异常请求，例如 explorer 里的 401。
            if status_code < 400:
                if method == "OPTIONS" and path.startswith("/api"):
                    return False

                if method == "GET":
                    if path in self.SUPPRESSED_GET_PATHS:
                        return False
                    if any(path.startswith(prefix) for prefix in self.SUPPRESSED_GET_PREFIXES):
                        return False

        msg = record.getMessage()
        return not any(fragment in msg for fragment in self.SUPPRESSED_MESSAGE_FRAGMENTS)
logging.basicConfig(level=logging.INFO)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("uvicorn.access").addFilter(EndpointFilter())
logger = logging.getLogger(__name__)


# ── 系统依赖检查（启动时 fast fail） ──────────────────────────────
# 新站点部署时，缺什么一目了然

HPC_DEPENDENCIES = {
    "SLURM 调度":  ["sbatch", "squeue", "scancel", "sinfo", "scontrol"],
    "容器运行时":   ["apptainer"],
    "版本控制":     ["git"],
    "音频转码":     ["ffmpeg"],
    "文件权限":     ["setfacl"],
}

LOCAL_DEPENDENCIES = {
    "容器运行时 (Docker)": ["docker"],
    "版本控制":            ["git"],
}

LOCAL_INSTALL_HINTS = {
    "docker": "请安装 Docker Desktop: https://docs.docker.com/get-docker/",
    "git": "请安装 Git: https://git-scm.com/downloads",
}

def _check_system_dependencies() -> None:
    deps = dict(LOCAL_DEPENDENCIES if is_local_mode else HPC_DEPENDENCIES)
    if magnus_config["execution"]["image_build"]["enabled"]:
        deps["镜像构建 (BuildKit)"] = [magnus_config["execution"]["image_build"]["buildctl"]]
    missing: Dict[str, List[str]] = {}
    for category, commands in deps.items():
        not_found = [cmd for cmd in commands if shutil.which(cmd) is None]
        if not_found:
            missing[category] = not_found

    if missing:
        lines = []
        for cat, cmds in missing.items():
            lines.append(f"  {cat}: {', '.join(cmds)}")
            if is_local_mode:
                for cmd in cmds:
                    hint = LOCAL_INSTALL_HINTS.get(cmd)
                    if hint:
                        lines.append(f"    → {hint}")
        msg = "❌ 系统依赖缺失（请安装后重启）:\n" + "\n".join(lines)
        logger.critical(msg)
        raise RuntimeError(msg)

    mode_label = "LOCAL (Docker)" if is_local_mode else "HPC (SLURM)"
    logger.info(f"✅ 系统依赖检查通过 ({mode_label})")

_check_system_dependencies()


models.Base.metadata.create_all(
    bind = engine,
)


def _repair_legacy_job_metrics_reference() -> None:
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


def _make_job_container_image_nullable() -> None:
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


def run_migrations()-> None:
    from sqlalchemy import inspect, text
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

    for column in ("dockerfile", "build_result", "resource_mode", "finalization_status", "execution_target"):
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
    _repair_legacy_job_metrics_reference()
    _make_job_container_image_nullable()

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


run_migrations()


def _log_admin_status()-> None:
    if is_local_auth:
        logger.info("🔑 Admin: 本地模式，所有用户均为管理员")
        return
    if not admin_open_ids:
        logger.info("🔑 Admin: 未配置管理员")
        return
    with SessionLocal() as db:
        found = db.query(models.User).filter(models.User.feishu_open_id.in_(admin_open_ids)).all()
        found_map = {u.feishu_open_id: u.name for u in found}
        for open_id in admin_open_ids:
            if open_id in found_map:
                logger.info(f"🔑 Admin: {found_map[open_id]} ({open_id})")
            else:
                logger.warning(f"⚠️ Admin open_id {open_id} 在数据库中未找到对应用户，该用户首次飞书登录后生效")


_log_admin_status()


# ── 本地模式：自动创建默认用户 ──────────────────────────────

def _ensure_local_user() -> None:
    if not is_local_auth:
        return
    import getpass
    with SessionLocal() as db:
        existing = db.query(models.User).first()
        if existing:
            logger.info(f"🏠 Local user: {existing.name}")
            return
        user = models.User(
            name=getpass.getuser(),
            user_type="human",
        )
        db.add(user)
        db.commit()
        logger.info(f"🏠 Created local user: {user.name}")

_ensure_local_user()


async def _refresh_all_user_info() -> None:
    """
    从飞书 Contact API 并发刷新所有用户的头像和姓名。
    单个用户失败不影响其他用户。
    """
    CONCURRENCY = 10

    with SessionLocal() as db:
        users = db.query(models.User).filter(models.User.feishu_open_id.isnot(None)).all()
        user_map: Dict[str, models.User] = {}
        for u in users:
            if u.feishu_open_id is not None:
                user_map[u.feishu_open_id] = u

    if not user_map:
        logger.debug("用户信息刷新：数据库中无飞书用户，跳过")
        return

    semaphore = asyncio.Semaphore(CONCURRENCY)
    updated = 0
    failed = 0

    async def fetch_one(open_id: str, client: httpx.AsyncClient) -> tuple:
        async with semaphore:
            info = await feishu_client.get_user_info_by_open_id(open_id, client)
            return (open_id, info)

    async with httpx.AsyncClient() as client:
        tasks = [fetch_one(oid, client) for oid in user_map]
        results = await asyncio.gather(*tasks, return_exceptions=True)

    with SessionLocal() as db:
        for result in results:
            if isinstance(result, Exception):
                failed += 1
                logger.warning(f"用户信息刷新失败: {result}")
                continue

            assert not isinstance(result, BaseException)
            open_id, info = result
            user = user_map[open_id]
            changes = []
            if info["avatar_url"] and info["avatar_url"] != user.avatar_url:
                changes.append(f"avatar: {user.avatar_url} -> {info['avatar_url']}")
                db.query(models.User).filter(models.User.id == user.id).update({"avatar_url": info["avatar_url"]})
            if info["name"] and info["name"] != user.name:
                changes.append(f"name: {user.name} -> {info['name']}")
                db.query(models.User).filter(models.User.id == user.id).update({"name": info["name"]})
            if changes:
                updated += 1
                logger.info(f"用户信息已更新 {user.name} ({open_id}): {', '.join(changes)}")

        db.commit()

    if failed:
        raise RuntimeError(f"用户信息刷新：{failed}/{len(user_map)} 人失败")

    if updated > 0:
        logger.info(f"用户信息刷新完成：共 {len(user_map)} 人，{updated} 人有更新")
    else:
        logger.debug(f"用户信息刷新完成：共 {len(user_map)} 人，0 人有更新")


async def _run_user_info_refresh_loop() -> None:
    refresh_interval = magnus_config["server"]["auth"]["feishu_client"]["refresh_interval"]
    while True:
        await asyncio.sleep(refresh_interval)
        try:
            await _refresh_all_user_info()
        except Exception as e:
            logger.error(f"用户信息刷新循环异常: {e}")


async def run_scheduler_loop(
)-> None:

    """
    后台调度循环，定期心跳
    """

    logger.info("🚀 Scheduler loop started.")
    while True:
        try:
            await scheduler.tick()
        except Exception as e:
            logger.error(f"Scheduler loop error: {e}")

        await asyncio.sleep(magnus_config["server"]["scheduler"]["heartbeat_interval"])


@asynccontextmanager
async def lifespan(
    app: FastAPI,
):
    async with lifespan_resources() as cleanup:
        cleanup.push_async_callback(resource_manager.builds.shutdown)
        cleanup.push_async_callback(
            _run_blocking_io,
            file_custody_manager.shutdown,
        )
        cleanup.push_async_callback(scheduler.shutdown)

        thread_pool_size = 200

        # 调整 asyncio 默认线程池 (影响 await asyncio.to_thread)
        loop = asyncio.get_running_loop()
        executor = concurrent.futures.ThreadPoolExecutor(max_workers=thread_pool_size)
        loop.set_default_executor(executor)
        # 调整 AnyIO 默认限流器 (影响 FastAPI 的 def 路由)
        limiter = anyio.to_thread.current_default_thread_limiter() # type: ignore
        limiter.total_tokens = thread_pool_size

        # 镜像恢复：清理上次异常退出遗留的 .tmp 和卡在 pulling/refreshing 的记录
        recover_stuck_images()

        # Restore cache protection before any new preparation or cache collection.
        with SessionLocal() as db:
            rows = db.query(models.Job.id, models.Job.build_result, models.Job.execution_target).filter(
                models.Job.build_result.isnot(None),
                (models.Job.status.in_([
                    models.JobStatus.PREPARING,
                    models.JobStatus.BUILDING,
                    models.JobStatus.PENDING,
                    models.JobStatus.QUEUED,
                    models.JobStatus.RUNNING,
                    models.JobStatus.PAUSED,
                ]) | models.Job.slurm_job_id.isnot(None)),
            ).all()
            pins = {job_id: json.loads(result) for job_id, result, _ in rows}
            active_targets = db.query(models.Job.execution_target).filter(
                models.Job.status.notin_([models.JobStatus.SUCCESS, models.JobStatus.FAILED, models.JobStatus.TERMINATED])
                | models.Job.slurm_job_id.isnot(None)
                | models.Job.finalization_status.isnot(None),
            ).distinct().all()
            for (target_id,) in active_targets:
                scheduler.for_target(target_id)
        resource_manager.builds.restore_pins(pins)
        scheduler.restore_pins([(job_id, target_id) for job_id, _, target_id in rows])

        if is_local_mode:
            collector = DockerMetricsCollector()
            cleanup.push_async_callback(collector.stop)
            collector.start()

        tasks = []
        cleanup.push_async_callback(
            cancel_background_tasks,
            tasks,
        )
        tasks.append(asyncio.create_task(run_scheduler_loop()))
        tasks.append(asyncio.create_task(service_manager.start_background_loop()))
        tasks.append(asyncio.create_task(file_custody_manager.cleanup_loop()))

        if not is_local_auth:
            refresh_interval = magnus_config["server"]["auth"]["feishu_client"]["refresh_interval"]
            if refresh_interval > 0:
                await _refresh_all_user_info()
                tasks.append(asyncio.create_task(_run_user_info_refresh_loop()))
            else:
                logger.info("用户信息刷新已禁用 (refresh_interval = 0)")
        else:
            logger.info("🏠 本地认证模式：跳过飞书用户信息刷新")

        yield


app = FastAPI(
    title = "Magnus API", 
    lifespan = lifespan,
)


app.add_middleware(
    CORSMiddleware,
    allow_origins = magnus_config["server"]["cors_origins"],
    allow_methods = ["*"],
    allow_headers = ["*"],
)


app.include_router(router, prefix="/api")
app.include_router(ws_router)


@app.get("/health")
def health():
    return {"status": "ok"}


if __name__ == "__main__":
    
    parser = argparse.ArgumentParser(description="Magnus Server")
    parser.add_argument("--deliver", action="store_true", help="Run in delivery mode (production)")
    parser.add_argument("--config", type=str, default=None, help="Path to magnus_config.yaml (consumed by _magnus_config.py)")
    args = parser.parse_args()
    deliver = args.deliver

    reload = not deliver
    if reload:
        logger.info("🛠️ Starting Magnus Backend in DEV Mode (Reload Enabled)")
    else:
        logger.info("🏭 Starting Magnus Backend in DELIVERY Mode (Reload Disabled)")
    
    uvicorn.run(
        "server.main:app",
        host = "127.0.0.1",
        port = magnus_config["server"]["back_end_port"],
        reload = reload,
    )
