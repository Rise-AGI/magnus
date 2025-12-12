# back_end/server/main.py
import asyncio
import logging
import uvicorn
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

# 导入本地模块
from library import *
from .routers import *
from ._github_client import *
from ._magnus_config import *
from . import models
from .database import *
from ._scheduler import scheduler # ✅ 导入调度器单例

# 配置日志
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# 自动创建数据库表 (注意：如果 Job 表结构变了，记得先手动删掉旧 db 文件)
models.Base.metadata.create_all(
    bind = engine,
)

async def run_scheduler_loop():
    """
    后台调度循环
    每 10 秒触发一次心跳
    """
    logger.info("🚀 Scheduler loop started.")
    while True:
        try:
            # scheduler.tick 是同步阻塞操作 (DB/Process)，
            # 必须放到线程池运行，否则会卡死 FastAPI 的主线程
            await asyncio.to_thread(scheduler.tick)
        except Exception as e:
            logger.error(f"Scheduler loop error: {e}")
        
        await asyncio.sleep(magnus_config["server"]["scheduler"]["heartbeat_interval"])

@asynccontextmanager
async def lifespan(app: FastAPI):
    # === 启动阶段 ===
    # 创建后台任务
    scheduler_task = asyncio.create_task(run_scheduler_loop())
    
    yield
    
    # === 关闭阶段 ===
    logger.info("Shutting down...")
    
    # 1. 停止调度器循环
    scheduler_task.cancel()
    try:
        await scheduler_task
    except asyncio.CancelledError:
        logger.info("Scheduler loop stopped.")
    
    # 2. 关闭 GitHub 客户端
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
    
    uvicorn.run(
        "server.main:app", 
        host = "0.0.0.0", 
        port = magnus_config["server"]["port"],
        reload = True,
    )