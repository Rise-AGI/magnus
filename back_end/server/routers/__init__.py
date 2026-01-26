# back_end/server/routers/__init__.py
from fastapi import APIRouter

from . import auth
from . import github
from . import jobs
from . import cluster
from . import blueprints
from . import services
from . import sdk
from . import enchant


__all__ = [
    "router",
]


router = APIRouter()

router.include_router(auth.router, tags=["Auth"])
router.include_router(github.router, tags=["GitHub"])
router.include_router(jobs.router, tags=["Jobs"])
router.include_router(cluster.router, tags=["Cluster"])
router.include_router(blueprints.router, tags=["Blueprints"])
router.include_router(services.router, tags=["Services"])
router.include_router(sdk.router, tags=["sdk"])
router.include_router(enchant.router, tags=["Enchant"])