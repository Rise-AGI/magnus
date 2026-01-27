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


class EndpointFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
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


def run_migrations() -> None:
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


async def run_scheduler_loop(
)-> None:
    
    """
    后台调度循环，定期心跳
    """
    
    logger.info("🚀 Scheduler loop started.")
    while True:
        try:
            await asyncio.to_thread(scheduler.tick)
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
    
    yield
    
    logger.info("Shutting down...")
    
    scheduler_task.cancel()
    service_manager_task.cancel()
    try:
        await scheduler_task
        await service_manager_task
    except asyncio.CancelledError:
        logger.info("Scheduler loop stopped.")
    
    await github_client.close()


app = FastAPI(
    title = "Magnus API", 
    lifespan = lifespan,
)


app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], 
    allow_methods=["*"],
    allow_headers=["*"],
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
        host = "0.0.0.0",
        port = magnus_config["server"]["back_end_port"],
        reload = reload,
    )