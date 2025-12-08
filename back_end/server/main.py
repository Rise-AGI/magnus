# back_end/server/main.py
from library import *
from .routers import *
from ._github_client import *
from ._magnus_config import *
from . import models
from .database import *


models.Base.metadata.create_all(
    bind = engine,
)


@asynccontextmanager
async def lifespan(
    app: FastAPI
):
    
    yield
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