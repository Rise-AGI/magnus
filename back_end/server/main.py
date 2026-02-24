# back_end/server/main.py
import anyio
import asyncio
import logging
import uvicorn
import argparse
import concurrent.futures
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from library import *
from .routers import *
from ._github_client import *
from ._magnus_config import *
from . import models
from .database import *
from ._scheduler import scheduler
from ._service_manager import service_manager
from ._file_custody_manager import file_custody_manager
from ._feishu_client import feishu_client


class EndpointFilter(logging.Filter):
    def filter(self, record: logging.LogRecord)-> bool:
        msg = record.getMessage()
        # 屏蔽高频噪音
        return not any(x in msg for x in [
            "GET /api/jobs",
            "GET /api/cluster/stats",
            "GET /api/dashboard/stats",
            "GET /api/dashboard/my-active-jobs",
            "GET /api/blueprints",
            "GET /api/services",
            "/logs HTTP",
            "OPTIONS /api",
        ])
logging.basicConfig(level=logging.INFO)
logging.getLogger("uvicorn.access").addFilter(EndpointFilter())
logger = logging.getLogger(__name__)


models.Base.metadata.create_all(
    bind = engine,
)


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


run_migrations()


def _log_admin_status()-> None:
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


async def _refresh_all_user_info() -> None:
    """
    从飞书 Contact API 并发刷新所有用户的头像和姓名。
    单个用户失败不影响其他用户。
    """
    CONCURRENCY = 10

    with SessionLocal() as db:
        users = db.query(models.User).filter(models.User.feishu_open_id.isnot(None)).all()
        user_map = {u.feishu_open_id: u for u in users}

    if not user_map:
        logger.info("用户信息刷新：数据库中无飞书用户，跳过")
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

    logger.info(f"用户信息刷新完成：共 {len(user_map)} 人，{updated} 人有更新")


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
    app: FastAPI
):
    
    thread_pool_size = 200
    
    # 调整 asyncio 默认线程池 (影响 await asyncio.to_thread)
    loop = asyncio.get_running_loop()
    executor = concurrent.futures.ThreadPoolExecutor(max_workers=thread_pool_size)
    loop.set_default_executor(executor)
    # 调整 AnyIO 默认限流器 (影响 FastAPI 的 def 路由)
    limiter = anyio.to_thread.current_default_thread_limiter() # type: ignore
    limiter.total_tokens = thread_pool_size
    
    scheduler_task = asyncio.create_task(run_scheduler_loop())
    service_manager_task = asyncio.create_task(service_manager.start_background_loop())
    file_custody_task = asyncio.create_task(file_custody_manager.cleanup_loop())

    # 用户信息刷新：首次同步执行（quick fail），然后启动后台循环
    refresh_interval = magnus_config["server"]["auth"]["feishu_client"]["refresh_interval"]
    user_refresh_task = None
    if refresh_interval > 0:
        await _refresh_all_user_info()
        user_refresh_task = asyncio.create_task(_run_user_info_refresh_loop())
    else:
        logger.info("用户信息刷新已禁用 (refresh_interval = 0)")

    yield

    logger.info("Shutting down...")

    scheduler_task.cancel()
    service_manager_task.cancel()
    file_custody_task.cancel()
    if user_refresh_task:
        user_refresh_task.cancel()
    try:
        await scheduler_task
        await service_manager_task
        await file_custody_task
        if user_refresh_task:
            await user_refresh_task
    except asyncio.CancelledError:
        logger.info("Scheduler loop stopped.")

    file_custody_manager.shutdown()
    await github_client.close()


app = FastAPI(
    title = "Magnus API", 
    lifespan = lifespan,
)


app.add_middleware(
    CORSMiddleware,
    allow_origins = ["*"],
    allow_methods = ["*"],
    allow_headers = ["*"],
)


app.include_router(router, prefix="/api")


if __name__ == "__main__":
    
    parser = argparse.ArgumentParser(description="Magnus Server")
    parser.add_argument("--deliver", action="store_true", help="Run in delivery mode (production)")
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