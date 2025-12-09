# back_end/server/routers.py
from library import *
from . import database
from . import models
from ._github_client import *
from .schemas import *
from .database import *
from ._jwt_signer import *
from ._feishu_client import *


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
        
        
@router.post(
    "/auth/feishu/login",
    response_model=LoginResponse,
)
async def feishu_login(
    req: FeishuLoginRequest, 
    db: Session = Depends(database.get_db),
):

    try:
        feishu_user = await feishu_client.get_feishu_user(req.code)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

    open_id = feishu_user.get("open_id") or feishu_user.get("union_id")
    if not open_id:
        raise HTTPException(status_code=400, detail="Missing OpenID")

    db_user = db.query(models.User).filter(models.User.feishu_open_id == open_id).first()

    if not db_user:
        db_user = models.User(
            feishu_open_id=open_id,
            name=feishu_user.get("name", "Unknown"),
            avatar_url=feishu_user.get("avatar_url"),
            email=feishu_user.get("email")
        )
        db.add(db_user)
        db.commit()
        db.refresh(db_user)
    else:
        db_user.name = feishu_user.get("name", db_user.name)
        db_user.avatar_url = feishu_user.get("avatar_url", db_user.avatar_url)
        db.commit()
        db.refresh(db_user)

    access_token = jwt_signer.create_access_token(payload={"sub": db_user.id})

    return {
        "access_token": access_token,
        "token_type": "bearer",
        "user": db_user,
    }