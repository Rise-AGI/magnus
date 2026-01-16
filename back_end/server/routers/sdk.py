# back_end/server/routers/sdk.py
import logging
from typing import Dict, Any
from datetime import datetime
from pydantic import BaseModel

from fastapi import APIRouter, Depends, HTTPException

from sqlalchemy.orm import Session
from .. import database
from .. import models
from .._blueprint_manager import blueprint_manager
from ..routers.auth import get_current_user
from ..routers.blueprints import _compute_signature_hash
from library.fundamental.json_tools import deserialize_json, serialize_json


logger = logging.getLogger(__name__)
router = APIRouter(prefix="/sdk")


class SDKBlueprintSubmitRequest(BaseModel):
    use_preference: bool = True
    parameters: Dict[str, Any] = {}


@router.post("/blueprints/{blueprint_id}/submit")
def submit_blueprint_sdk(
    blueprint_id: str,
    request: SDKBlueprintSubmitRequest,
    db: Session = Depends(database.get_db),
    current_user: models.User = Depends(get_current_user), 
):

    bp = db.query(models.Blueprint).filter(models.Blueprint.id == blueprint_id).first()
    if not bp:
        raise HTTPException(status_code=404, detail=f"Blueprint {blueprint_id} not found")

    final_params = request.parameters.copy()

    pref = None
    if request.use_preference:
        pref = db.query(models.BlueprintUserPreference).filter(
            models.BlueprintUserPreference.user_id == current_user.id,
            models.BlueprintUserPreference.blueprint_id == blueprint_id,
        ).first()
        
        if pref:
            try:
                cached = deserialize_json(pref.cached_params)
                if isinstance(cached, dict):
                    # 优先级：CLI 显式传入 > 数据库缓存
                    base_params = cached.copy()
                    base_params.update(final_params)
                    final_params = base_params
            except Exception as e:
                logger.warning(f"Failed to merge preferences for user {current_user.id}: {e}")

    try:
        job_submission = blueprint_manager.execute(
            bp.code,
            final_params,
        )
        
        job_dict = job_submission.model_dump()

        db_job = models.Job(
            **job_dict,
            user_id=current_user.id,
            status=models.JobStatus.PENDING,
        )

        db.add(db_job)
        
        # 任务提交成功后，自动保存/更新偏好
        # 即使 request.use_preference=False, 成功运行的参数也值得被记录
        if pref is None:
            # 如果之前没查过或者不存在，再查一次以确定是 update 还是 insert
            pref = db.query(models.BlueprintUserPreference).filter(
                models.BlueprintUserPreference.user_id == current_user.id,
                models.BlueprintUserPreference.blueprint_id == blueprint_id,
            ).first()

        current_hash = _compute_signature_hash(bp.code)
        serialized_params = serialize_json(final_params)

        if pref:
            pref.blueprint_hash = current_hash
            pref.cached_params = serialized_params
            pref.updated_at = datetime.utcnow()
        else:
            new_pref = models.BlueprintUserPreference(
                user_id=current_user.id,
                blueprint_id=blueprint_id,
                blueprint_hash=current_hash,
                cached_params=serialized_params,
            )
            db.add(new_pref)

        db.commit()
        db.refresh(db_job)
        
        logger.info(f"[SDK] User {current_user.name} submitted Job {db_job.id} via Blueprint {blueprint_id}")
        
        return {"job_id": db_job.id}

    except Exception as e:
        logger.error(f"[SDK] Submit failed for Blueprint {blueprint_id}: {e}")
        raise HTTPException(status_code=400, detail=str(e))