# back_end/server/main.py
import asyncio
import logging
import uvicorn
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


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


models.Base.metadata.create_all(
    bind = engine,
)


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
    scheduler_task = asyncio.create_task(run_scheduler_loop())
    
    yield
    
    logger.info("Shutting down...")
    
    scheduler_task.cancel()
    try:
        await scheduler_task
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
    
    uvicorn.run(
        "server.main:app", 
        host = "0.0.0.0", 
        port = magnus_config["server"]["port"],
        reload = True,
    )