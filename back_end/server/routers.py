# back_end/server/routers.py
import jwt
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session
from sqlalchemy import or_
from typing import List, Optional
from library import *
from . import database
from . import models
from .models import JobStatus, JobType
from .schemas import *
from ._github_client import github_client
from ._jwt_signer import jwt_signer
from ._feishu_client import feishu_client
from ._magnus_config import magnus_config


__all__ = [
    "router",
]


router = APIRouter()

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/feishu/login")

async def get_current_user(
    token: str = Depends(oauth2_scheme), 
    db: Session = Depends(database.get_db)
) -> models.User:
    
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    
    try:
        payload = jwt.decode(
            token, 
            magnus_config["server"]["jwt_signer"]["secret_key"], 
            algorithms=[magnus_config["server"]["jwt_signer"]["algorithm"]]
        )
        user_id: str = payload.get("sub")
        if user_id is None:
            raise credentials_exception
    except jwt.PyJWTError:
        raise credentials_exception
        
    user = db.query(models.User).filter(models.User.id == user_id).first()
    if user is None:
        raise credentials_exception
        
    return user

# ================= GitHub Routes =================

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

# ================= Job Routes =================

@router.post(
    "/jobs/submit", 
    response_model = JobResponse,
)
async def submit_job(
    job_data: JobSubmission, 
    db: Session = Depends(database.get_db),
    current_user: models.User = Depends(get_current_user)
):
    """
    提交新任务。
    注意：此接口不再直接与 SLURM 交互。
    它只负责将任务写入数据库，状态设为 PENDING。
    后台的 Scheduler 会自动捡起它并处理排队/抢占逻辑。
    """
    job_dict = job_data.model_dump()
    
    # 创建任务对象
    db_job = models.Job(
        **job_dict, 
        user_id=current_user.id,
        status=JobStatus.PENDING # 显式设为排队状态
    )
    
    db.add(db_job)
    db.commit()
    db.refresh(db_job)
    
    return db_job

@router.get(
    "/jobs", 
    response_model = PagedJobResponse,
)
async def get_jobs(
    skip: int = 0, 
    limit: int = 100, 
    search: Optional[str] = None,
    creator_id: Optional[str] = None,
    db: Session = Depends(database.get_db),
):
    """
    获取任务列表（分页 + 搜索 + 筛选）
    """
    query = db.query(models.Job)

    # 1. 搜索逻辑 (支持搜 ID 或 任务名)
    if search:
        search_pattern = f"%{search}%"
        query = query.filter(
            or_(
                models.Job.task_name.ilike(search_pattern),
                models.Job.id.ilike(search_pattern)
            )
        )
    
    # 2. 用户筛选逻辑
    if creator_id and creator_id != "all":
        query = query.filter(models.Job.user_id == creator_id)

    # 3. 计算总数
    total = query.count()

    # 4. 分页查询
    # 按创建时间倒序
    jobs = query.order_by(models.Job.created_at.desc())\
            .offset(skip).limit(limit).all()
            
    return {"total": total, "items": jobs}

# ================= Auth Routes =================

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

@router.get(
    "/users",
    response_model=List[UserInfo],
)
async def get_users(
    db: Session = Depends(database.get_db),
):
    users = db.query(models.User).order_by(models.User.name).all()
    return users