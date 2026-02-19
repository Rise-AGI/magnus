# back_end/server/routers/blueprints.py
import logging
from typing import List, Optional, Dict, Any
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session, joinedload
from sqlalchemy import or_

from .. import database
from .. import models
from pydantic import BaseModel

from ..schemas import (
    BlueprintResponse,
    BlueprintCreate,
    PagedBlueprintResponse,
    BlueprintParamSchema,
    BlueprintPreferenceUpdate,
    BlueprintPreferenceResponse,
)
from .._blueprint_manager import blueprint_manager
from .._magnus_config import magnus_config
from .auth import get_current_user
from library import *


class BlueprintRunRequest(BaseModel):
    """统一的 Blueprint 运行请求模型"""
    parameters: Dict[str, Any] = {}
    use_preference: bool = False   # 是否合并已缓存的偏好
    save_preference: bool = False  # 成功后是否保存参数为新偏好


def _normalize_obj(
    obj: Any
)-> Any:
    
    if isinstance(obj, dict):
        return {k: _normalize_obj(v) for k, v in obj.items()}
    elif isinstance(obj, list) or isinstance(obj, tuple):
        return [_normalize_obj(x) for x in obj]
    elif isinstance(obj, set):
        try:
            return sorted([_normalize_obj(x) for x in obj], key=lambda x: str(x))
        except:
            return sorted([str(x) for x in obj])
    elif isinstance(obj, float):
        if obj.is_integer():
            return int(obj)
        return obj
    return obj


def _compute_signature_hash(
    code: str,
)-> str:
    
    try:
        schema_objs = blueprint_manager.analyze_signature(code)
        schema_dicts = []
        for s in schema_objs:
            if hasattr(s, "model_dump"):
                schema_dicts.append(s.model_dump())
            elif hasattr(s, "dict"):
                schema_dicts.append(s.dict()) # 兼容旧版 Pydantic
            elif isinstance(s, dict):
                schema_dicts.append(s)
            else:
                schema_dicts.append(str(s)) # 兜底
                
        normalized_schema = _normalize_obj(schema_dicts)
        
        canonical_json = json.dumps(
            normalized_schema, 
            sort_keys = True, 
            separators = (',', ':'),
            ensure_ascii = False,
        )
        
        final_hash = hashlib.sha256(canonical_json.encode("utf-8")).hexdigest()
        
        return final_hash
        
    except Exception as e:
        logger.error(f"❌ Hash computation failed: {e}")
        logger.error(traceback.format_exc())
        return "invalid_signature"


logger = logging.getLogger(__name__)
router = APIRouter()


@router.post(
    "/blueprints",
    response_model =BlueprintResponse,
)
def create_blueprint(
    bp: BlueprintCreate,
    db: Session = Depends(database.get_db),
    current_user: models.User = Depends(get_current_user),
):
    # 1. 验证代码签名
    try:
        blueprint_manager.analyze_signature(bp.code)
    except (ValueError, NameError, TypeError) as e:
        raise HTTPException(status_code=400, detail=str(e))

    # 2. 检查 ID 是否冲突（如果是新建）
    # 注意：这里允许 overwrite (Update)，但如果是别人的蓝图则禁止覆盖
    existing = db.query(models.Blueprint).filter(models.Blueprint.id == bp.id).first()

    if existing:
        if existing.user_id != current_user.id:
            raise HTTPException(
                status_code = 403,
                detail = "You cannot modify a blueprint created by another user. Please verify the Blueprint ID.",
            )

        # Update existing
        existing.title = bp.title
        existing.description = bp.description
        existing.code = bp.code
        existing.updated_at = datetime.now(timezone.utc)
        db.commit()
        db.refresh(existing)
        return existing

    # Create new
    db_bp = models.Blueprint(
        **bp.model_dump(),
        user_id = current_user.id,
    )
    db.add(db_bp)
    db.commit()
    db.refresh(db_bp)
    return db_bp


@router.delete("/blueprints/{blueprint_id}")
def delete_blueprint(
    blueprint_id: str,
    db: Session = Depends(database.get_db),
    current_user: models.User = Depends(get_current_user),
):
    """
    删除蓝图。仅拥有者可操作。
    """
    bp = db.query(models.Blueprint).filter(models.Blueprint.id == blueprint_id).first()

    if not bp:
        raise HTTPException(status_code=404, detail="Blueprint not found")

    if bp.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="You do not have permission to delete this blueprint")

    db.delete(bp)
    db.commit()

    return {"message": "Blueprint deleted successfully"}


@router.get(
    "/blueprints",
    response_model =PagedBlueprintResponse,
)
def list_blueprints(
    skip: int = 0,
    limit: int = 20,
    search: Optional[str] = None,
    creator_id: Optional[str] = None,
    db: Session = Depends(database.get_db),
    _: models.User = Depends(get_current_user),
):
    """
    获取蓝图列表（支持分页、搜索、筛选）
    """
    query = db.query(models.Blueprint)

    # 1. 搜索逻辑 (Title, ID, Description)
    if search:
        search_pattern = f"%{search}%"
        query = query.filter(
            or_(
                models.Blueprint.title.ilike(search_pattern),
                models.Blueprint.id.ilike(search_pattern),
                models.Blueprint.description.ilike(search_pattern),
            )
        )

    # 2. 用户筛选
    if creator_id and creator_id != "all":
        query = query.filter(models.Blueprint.user_id == creator_id)

    total = query.count()

    # 3. 分页查询
    # 按更新时间倒序排列（最近更新的在前面）
    items = query.options(joinedload(models.Blueprint.user))\
                 .order_by(models.Blueprint.updated_at.desc())\
                 .offset(skip)\
                 .limit(limit)\
                 .all()

    return {"total": total, "items": items}


@router.get(
    "/blueprints/{blueprint_id}",
    response_model =BlueprintResponse,
)
def get_blueprint(
    blueprint_id: str,
    db: Session = Depends(database.get_db),
    _: models.User = Depends(get_current_user),
):
    blueprint = db.query(models.Blueprint)\
        .options(joinedload(models.Blueprint.user))\
        .filter(models.Blueprint.id == blueprint_id)\
        .first()
    
    if not blueprint:
        raise HTTPException(status_code=404, detail="Blueprint not found")
        
    return blueprint


@router.get(
    "/blueprints/{blueprint_id}/schema",
    response_model =List[BlueprintParamSchema],
)
def get_blueprint_schema(
    blueprint_id: str,
    db: Session = Depends(database.get_db),
    _: models.User = Depends(get_current_user),
):
    bp = db.query(models.Blueprint).filter(models.Blueprint.id == blueprint_id).first()
    if not bp:
        raise HTTPException(status_code=404, detail="Blueprint not found")

    try:
        return blueprint_manager.analyze_signature(bp.code)
    except (ValueError, NameError, TypeError) as e:
        raise HTTPException(status_code=400, detail=f"Invalid blueprint code: {e}")


@router.post("/blueprints/{blueprint_id}/run")
def run_blueprint(
    blueprint_id: str,
    request: BlueprintRunRequest,
    db: Session = Depends(database.get_db),
    current_user: models.User = Depends(get_current_user),
):
    """
    统一的 Blueprint 运行端点。
    - use_preference=True: 合并用户缓存的偏好参数（显式传入 > 缓存）
    - save_preference=True: 成功运行后保存参数为新偏好
    """
    bp = db.query(models.Blueprint).filter(models.Blueprint.id == blueprint_id).first()
    if not bp:
        raise HTTPException(status_code=404, detail="Blueprint not found")


    # 1. 构建最终参数
    final_params = request.parameters.copy()

    if request.use_preference:
        pref = db.query(models.BlueprintUserPreference).filter(
            models.BlueprintUserPreference.user_id == current_user.id,
            models.BlueprintUserPreference.blueprint_id == blueprint_id,
        ).first()

        if pref:
            current_hash = _compute_signature_hash(bp.code)
            # 仅当 Blueprint 签名未变化时才使用缓存
            if pref.blueprint_hash == current_hash:
                try:
                    cached = deserialize_json(pref.cached_params)
                    if isinstance(cached, dict):
                        # 优先级：显式传入 > 缓存
                        base_params = dict(cached)
                        base_params.update(final_params)
                        final_params = base_params
                except Exception as e:
                    logger.warning(f"Failed to merge preferences: {e}")


    # 2. 执行 Blueprint
    try:
        job_submission = blueprint_manager.execute(bp.code, final_params)
        job_dict = job_submission.model_dump()

        # 所有 Optional 字段填入集群默认值，确保 DB 完整记录实验配置
        cluster = magnus_config["cluster"]
        if job_dict.get("cpu_count") is None or job_dict["cpu_count"] == 0:
            job_dict["cpu_count"] = cluster["default_cpu_count"]
        if job_dict.get("memory_demand") is None:
            job_dict["memory_demand"] = cluster["default_memory_demand"]
        if job_dict.get("ephemeral_storage") is None:
            job_dict["ephemeral_storage"] = cluster["default_ephemeral_storage"]
        if job_dict.get("runner") is None:
            job_dict["runner"] = cluster["default_runner"]
        if job_dict.get("container_image") is None:
            job_dict["container_image"] = cluster["default_container_image"]
        if job_dict.get("system_entry_command") is None:
            job_dict["system_entry_command"] = cluster["default_system_entry_command"]

        db_job = models.Job(
            **job_dict,
            user_id = current_user.id,
            status = models.JobStatus.PREPARING,
        )
        db.add(db_job)


        # 3. 保存偏好（仅在成功执行后）
        if request.save_preference:
            pref = db.query(models.BlueprintUserPreference).filter(
                models.BlueprintUserPreference.user_id == current_user.id,
                models.BlueprintUserPreference.blueprint_id == blueprint_id,
            ).first()

            current_hash = _compute_signature_hash(bp.code)
            serialized_params = serialize_json(final_params)

            if pref:
                pref.blueprint_hash = current_hash
                pref.cached_params = serialized_params
                pref.updated_at = datetime.now(timezone.utc)
            else:
                new_pref = models.BlueprintUserPreference(
                    user_id = current_user.id,
                    blueprint_id = blueprint_id,
                    blueprint_hash = current_hash,
                    cached_params = serialized_params,
                )
                db.add(new_pref)


        db.commit()
        db.refresh(db_job)

        logger.info(f"Blueprint {blueprint_id} launched job {db_job.id}")
        return {"job_id": db_job.id}

    except Exception as e:
        logger.error(f"Blueprint run failed: {e}")
        raise HTTPException(status_code=400, detail=str(e))
    
    
@router.get(
    "/blueprints/{blueprint_id}/preference",
    response_model =BlueprintPreferenceResponse,
)
def get_blueprint_preference(
    blueprint_id: str,
    db: Session = Depends(database.get_db),
    current_user: models.User = Depends(get_current_user),
):
    pref = db.query(models.BlueprintUserPreference).filter(
        models.BlueprintUserPreference.user_id == current_user.id,
        models.BlueprintUserPreference.blueprint_id == blueprint_id,
    ).first()
    
    if not pref:
        raise HTTPException(status_code=404, detail="No preference found")
        
    return BlueprintPreferenceResponse(
        blueprint_id = pref.blueprint_id,
        blueprint_hash = pref.blueprint_hash,
        cached_params = deserialize_json(pref.cached_params),
        updated_at = pref.updated_at,
    )


@router.put(
    "/blueprints/{blueprint_id}/preference",
    response_model =BlueprintPreferenceResponse,
)
def save_blueprint_preference(
    blueprint_id: str,
    pref_update: BlueprintPreferenceUpdate,
    db: Session = Depends(database.get_db),
    current_user: models.User = Depends(get_current_user),
):
    bp = db.query(models.Blueprint).filter(models.Blueprint.id == blueprint_id).first()
    if not bp:
        raise HTTPException(status_code=404, detail="Blueprint not found")
        
    if pref_update.blueprint_id != blueprint_id:
         raise HTTPException(status_code=400, detail="Blueprint ID mismatch")

    current_hash = _compute_signature_hash(bp.code)

    pref = db.query(models.BlueprintUserPreference).filter(
        models.BlueprintUserPreference.user_id == current_user.id,
        models.BlueprintUserPreference.blueprint_id == blueprint_id,
    ).first()
    
    serialized_params = serialize_json(pref_update.cached_params)
    
    if pref: # Update
        pref.blueprint_hash = current_hash
        pref.cached_params = serialized_params
        pref.updated_at = datetime.now(timezone.utc)
    else: # Create
        pref = models.BlueprintUserPreference(
            user_id = current_user.id,
            blueprint_id = blueprint_id,
            blueprint_hash = current_hash,
            cached_params = serialized_params,
        )
        db.add(pref)
    
    db.commit()
    db.refresh(pref)
    
    return BlueprintPreferenceResponse(
        blueprint_id = pref.blueprint_id,
        blueprint_hash = pref.blueprint_hash,
        cached_params = deserialize_json(pref.cached_params),
        updated_at = pref.updated_at,
    )