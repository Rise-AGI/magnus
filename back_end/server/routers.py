# back_end/server/routers.py
from library import *
from ._github_client import *
from .schemas import *
from .database import *
from . import database
from . import models


__all__ = [
    "router",
]


router = APIRouter()


@router.get("/github/{ns}/{repo}/branches")
async def get_branches(ns: str, repo: str):
    branches = await github_client.fetch_branches(ns, repo)
    if not branches:
        raise HTTPException(
            status_code=404, 
            detail = "Repo not found or empty",
        )
    return branches


@router.get("/github/{ns}/{repo}/commits")
async def get_commits(
    ns: str, 
    repo: str, 
    branch: str,
):
    
    return await github_client.fetch_commits(ns, repo, branch)


@router.post(
    "/jobs/submit", 
    response_model = JobResponse,
)
async def submit_job(
    job_data: JobSubmission, 
    db: Session = Depends(database.get_db)
):

    db_job = models.Job(**job_data.model_dump())
    db_job.status = "Pending" 
    
    db.add(db_job)
    db.commit()
    db.refresh(db_job)
    
    return db_job


@router.get(
    "/jobs", 
    response_model = List[JobResponse],
)
async def get_jobs(
    skip: int = 0, 
    limit: int = 100, 
    db: Session = Depends(database.get_db),
):
    jobs = db.query(models.Job).order_by(models.Job.created_at.desc())\
            .offset(skip).limit(limit).all()
    return jobs