# back_end/server/routers/__init__.py
from fastapi import APIRouter

from . import auth
from . import github
from . import jobs
from . import cluster
from . import blueprints
from . import services
from . import explore
from . import files
from . import skills
from . import images
from . import users
from . import chat
from . import invite
from .ws_chat import ws_router


__all__ = [
    "router",
    "ws_router",
]


router = APIRouter()

router.include_router(auth.router, tags=["Auth"])
router.include_router(users.router, tags=["Users"])
router.include_router(github.router, tags=["GitHub"])
router.include_router(jobs.router, tags=["Jobs"])
router.include_router(cluster.router, tags=["Cluster"])
router.include_router(blueprints.router, tags=["Blueprints"])
router.include_router(services.router, tags=["Services"])
router.include_router(explore.router, tags=["Explore"])
router.include_router(files.router, tags=["Files"])
router.include_router(skills.router, tags=["Skills"])
router.include_router(images.router, tags=["Images"])
router.include_router(chat.router, tags=["Chat"])
router.include_router(invite.router, tags=["Invite"])
