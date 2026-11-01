# back_end/server/routers/blueprints.py
from dataclasses import replace
from datetime import datetime, timezone
from typing import Annotated, Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Response, status
from library import deserialize_json, escape_like, serialize_json
from pydantic import BaseModel, Field
from sqlalchemy import case, or_
from sqlalchemy.orm import Session, joinedload

from .. import database, models
from .._blueprint_manager import BlueprintUnavailable, blueprint_manager
from .._blueprint_signature import code_revision, signature_hash
from .._id_registry import assert_id_available
from .._magnus_config import is_admin_user
from .._resource_aliases import exclude_shadowed_resources, resolve_resource_id
from .._submission import BlueprintRun, SubmissionContext, save_preference
from .._user_lifecycle import lock_user_for_write
from ..schemas import (
    BlueprintCreate,
    BlueprintListItem,
    BlueprintParamSchema,
    BlueprintPreferenceResponse,
    BlueprintPreferenceUpdate,
    BlueprintResponse,
    PagedBlueprintResponse,
    TransferRequest,
)
from ._authz import (
    assert_can_manage_resource,
    assert_valid_transfer_target,
    compute_can_manage,
)
from .auth import get_current_user
from .jobs import create_job, enforce_field_size_limits
from .users import _get_all_subordinate_ids


class BlueprintRunRequest(BaseModel):
    """统一的 Blueprint 运行请求模型"""

    parameters: Dict[str, Any] = {}
    use_preference: bool = False  # 是否合并已缓存的偏好
    save_preference: bool = False  # 成功后是否保存参数为新偏好
    blueprint_revision: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
    )


def _analyze_schema(
    code: str,
) -> list[BlueprintParamSchema]:
    try:
        return blueprint_manager.analyze_signature(
            code,
        )
    except BlueprintUnavailable as error:
        raise HTTPException(
            status_code=503,
            detail=str(
                error,
            ),
        ) from error
    except (ValueError, NameError, TypeError) as error:
        raise HTTPException(
            status_code=400,
            detail=str(
                error,
            ),
        ) from error


def _compute_signature_hash(
    code: str,
) -> str:
    return signature_hash(
        [
            item.model_dump(
                mode="json",
            )
            for item in _analyze_schema(
                code,
            )
        ],
    )


router = APIRouter()


@router.post(
    "/blueprints",
    response_model=BlueprintResponse,
)
def create_blueprint(
    bp: BlueprintCreate,
    db: Session = Depends(
        database.get_db,
    ),
    current_user: models.User = Depends(
        get_current_user,
    ),
    default_target: Annotated[
        str | None,
        Header(
            alias="X-Magnus-Default-Execution-Target",
        ),
    ] = None,
):
    bp = bp.model_copy(
        update={
            "id": resolve_resource_id(
                db,
                "blueprint",
                bp.id,
                default_target,
            )
        },
    )
    enforce_field_size_limits(
        bp.model_dump(),
        "blueprint",
    )

    existing = (
        db.query(
            models.Blueprint,
        )
        .filter(
            models.Blueprint.id == bp.id,
        )
        .first()
    )
    if existing is not None:
        assert_can_manage_resource(
            db,
            current_user,
            existing.user_id,
            resource_label="blueprint",
        )
    else:
        assert_id_available(
            db,
            bp.id,
        )
    _analyze_schema(
        bp.code,
    )

    lock_user_for_write(
        db,
        current_user.id,
    )
    existing = (
        db.query(
            models.Blueprint,
        )
        .filter(
            models.Blueprint.id == bp.id,
        )
        .populate_existing()
        .first()
    )
    if existing is not None:
        assert_can_manage_resource(
            db,
            current_user,
            existing.user_id,
            resource_label="blueprint",
        )
        existing.title = bp.title
        existing.description = bp.description
        existing.code = bp.code
        existing.updated_at = datetime.now(
            timezone.utc,
        )
        result = existing
    else:
        assert_id_available(
            db,
            bp.id,
        )
        result = models.Blueprint(
            **bp.model_dump(),
            user_id=current_user.id,
        )
        db.add(
            result,
        )
    db.commit()
    db.refresh(
        result,
    )
    return result


@router.delete(
    "/blueprints/{blueprint_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def delete_blueprint(
    blueprint_id: str,
    db: Session = Depends(
        database.get_db,
    ),
    current_user: models.User = Depends(
        get_current_user,
    ),
    default_target: Annotated[
        str | None,
        Header(
            alias="X-Magnus-Default-Execution-Target",
        ),
    ] = None,
) -> None:
    """
    删除蓝图。拥有者、管理员、上司可操作。
    """
    blueprint_id = resolve_resource_id(
        db,
        "blueprint",
        blueprint_id,
        default_target,
    )
    lock_user_for_write(
        db,
        current_user.id,
    )
    bp = (
        db.query(
            models.Blueprint,
        )
        .filter(
            models.Blueprint.id == blueprint_id,
        )
        .first()
    )

    if not bp:
        raise HTTPException(
            status_code=404,
            detail="Blueprint not found",
        )

    assert_can_manage_resource(
        db,
        current_user,
        bp.user_id,
        resource_label="blueprint",
    )

    db.query(
        models.BlueprintUserPreference,
    ).filter(
        models.BlueprintUserPreference.blueprint_id == blueprint_id,
    ).delete(
        synchronize_session=False,
    )
    db.delete(
        bp,
    )
    db.commit()


@router.post(
    "/blueprints/{blueprint_id}/transfer",
    response_model=BlueprintResponse,
)
def transfer_blueprint(
    blueprint_id: str,
    body: TransferRequest,
    db: Session = Depends(
        database.get_db,
    ),
    current_user: models.User = Depends(
        get_current_user,
    ),
    default_target: Annotated[
        str | None,
        Header(
            alias="X-Magnus-Default-Execution-Target",
        ),
    ] = None,
) -> models.Blueprint:
    blueprint_id = resolve_resource_id(
        db,
        "blueprint",
        blueprint_id,
        default_target,
    )
    lock_user_for_write(
        db,
        current_user.id,
    )
    bp = (
        db.query(
            models.Blueprint,
        )
        .options(
            joinedload(
                models.Blueprint.user,
            ),
        )
        .filter(
            models.Blueprint.id == blueprint_id,
        )
        .first()
    )
    if not bp:
        raise HTTPException(
            status_code=404,
            detail="Blueprint not found",
        )

    assert_can_manage_resource(
        db,
        current_user,
        bp.user_id,
        resource_label="blueprint",
    )

    assert_valid_transfer_target(
        db,
        current_user,
        body.new_owner_id,
    )

    bp.user_id = body.new_owner_id
    bp.updated_at = datetime.now(
        timezone.utc,
    )
    db.commit()
    db.refresh(
        bp,
    )
    return bp


@router.get(
    "/blueprints",
    response_model=PagedBlueprintResponse,
)
def list_blueprints(
    skip: int = 0,
    limit: int = 20,
    search: Optional[str] = None,
    creator_id: Optional[str] = None,
    db: Session = Depends(
        database.get_db,
    ),
    current_user: models.User = Depends(
        get_current_user,
    ),
    default_target: Annotated[
        str | None,
        Header(
            alias="X-Magnus-Default-Execution-Target",
        ),
    ] = None,
):
    """
    获取蓝图列表（支持分页、搜索、筛选）
    """
    query = db.query(
        models.Blueprint,
    )
    query = exclude_shadowed_resources(
        query,
        models.Blueprint,
        "blueprint",
        default_target,
    )

    # 1. 搜索逻辑 (Title, ID, Description)
    if search:
        safe = escape_like(
            search,
        )
        search_pattern = f"%{safe}%"
        query = query.filter(
            or_(
                models.Blueprint.title.ilike(
                    search_pattern,
                    escape="\\",
                ),
                models.Blueprint.id.ilike(
                    search_pattern,
                    escape="\\",
                ),
                models.Blueprint.description.ilike(
                    search_pattern,
                    escape="\\",
                ),
            ),
        )

    # 2. 用户筛选
    if creator_id and creator_id != "all":
        query = query.filter(
            models.Blueprint.user_id == creator_id,
        )

    total = query.count()

    human_first = case(
        (models.User.user_type == "human", 0),
        else_=1,
    )
    items = (
        query.join(
            models.User,
            models.Blueprint.user_id == models.User.id,
        )
        .options(
            *models.blueprint_list_load_options(),
        )
        .order_by(
            human_first,
            models.Blueprint.updated_at.desc(),
        )
        .offset(
            skip,
        )
        .limit(
            limit,
        )
        .all()
    )

    is_admin = is_admin_user(
        current_user,
    )
    subordinate_ids = (
        set(
            _get_all_subordinate_ids(
                db,
                current_user.id,
            ),
        )
        if not is_admin
        else set()
    )
    result = []
    for bp in items:
        resp = BlueprintListItem.model_validate(
            bp,
        )
        resp.can_manage = compute_can_manage(
            db,
            current_user,
            bp.user_id,
            subordinate_ids=subordinate_ids,
        )
        result.append(
            resp,
        )

    return {"total": total, "items": result}


@router.get(
    "/blueprints/{blueprint_id}",
    response_model=BlueprintResponse,
)
def get_blueprint(
    blueprint_id: str,
    db: Session = Depends(
        database.get_db,
    ),
    current_user: models.User = Depends(
        get_current_user,
    ),
    default_target: Annotated[
        str | None,
        Header(
            alias="X-Magnus-Default-Execution-Target",
        ),
    ] = None,
):
    blueprint_id = resolve_resource_id(
        db,
        "blueprint",
        blueprint_id,
        default_target,
    )
    blueprint = (
        db.query(
            models.Blueprint,
        )
        .options(
            joinedload(
                models.Blueprint.user,
            ),
        )
        .filter(
            models.Blueprint.id == blueprint_id,
        )
        .first()
    )

    if not blueprint:
        raise HTTPException(
            status_code=404,
            detail="Blueprint not found",
        )

    resp = BlueprintResponse.model_validate(
        blueprint,
    )
    resp.can_manage = compute_can_manage(
        db,
        current_user,
        blueprint.user_id,
    )
    return resp


@router.get(
    "/blueprints/{blueprint_id}/schema",
    response_model=List[BlueprintParamSchema],
)
def get_blueprint_schema(
    blueprint_id: str,
    response: Response,
    db: Session = Depends(
        database.get_db,
    ),
    _: models.User = Depends(
        get_current_user,
    ),
    default_target: Annotated[
        str | None,
        Header(
            alias="X-Magnus-Default-Execution-Target",
        ),
    ] = None,
):
    blueprint_id = resolve_resource_id(
        db,
        "blueprint",
        blueprint_id,
        default_target,
    )
    bp = (
        db.query(
            models.Blueprint,
        )
        .filter(
            models.Blueprint.id == blueprint_id,
        )
        .first()
    )
    if not bp:
        raise HTTPException(
            status_code=404,
            detail="Blueprint not found",
        )

    schema = _analyze_schema(
        bp.code,
    )
    response.headers["X-Magnus-Blueprint-Revision"] = code_revision(
        bp.code,
    )
    response.headers["X-Magnus-Blueprint-Signature"] = signature_hash(
        [
            item.model_dump(
                mode="json",
            )
            for item in schema
        ],
    )
    response.headers["X-Magnus-Submission-Idempotency"] = "1"
    return schema


@router.post(
    "/blueprints/{blueprint_id}/run",
)
def run_blueprint(
    blueprint_id: str,
    request: BlueprintRunRequest,
    db: Session = Depends(
        database.get_db,
    ),
    current_user: models.User = Depends(
        get_current_user,
    ),
    default_target: Annotated[
        str | None,
        Header(
            alias="X-Magnus-Default-Execution-Target",
        ),
    ] = None,
    idempotency_key: Annotated[
        str | None,
        Header(
            alias="Idempotency-Key",
        ),
    ] = None,
):
    submission = SubmissionContext.from_request(
        idempotency_key,
        f"blueprint:{blueprint_id}",
        request.model_dump(
            mode="json",
        ),
        default_target,
    )
    try:
        existing = submission.replay(
            db,
            current_user.id,
        )
        if existing is not None:
            return {"job_id": existing.id}
        blueprint_id = resolve_resource_id(
            db,
            "blueprint",
            blueprint_id,
            default_target,
        )
        bp = (
            db.query(
                models.Blueprint,
            )
            .filter(
                models.Blueprint.id == blueprint_id,
            )
            .first()
        )
        if bp is None:
            raise HTTPException(
                status_code=404,
                detail="Blueprint not found",
            )
        revision = code_revision(
            bp.code,
        )
        if request.blueprint_revision is not None and request.blueprint_revision != revision:
            raise HTTPException(
                status_code=409,
                detail="Blueprint changed; reload its parameters before running",
            )
        schema_hash = _compute_signature_hash(
            bp.code,
        )
        parameters = dict(
            request.parameters,
        )
        if request.use_preference:
            preference = (
                db.query(
                    models.BlueprintUserPreference,
                )
                .filter(
                    models.BlueprintUserPreference.user_id == current_user.id,
                    models.BlueprintUserPreference.blueprint_id == blueprint_id,
                )
                .first()
            )
            if preference is not None and preference.blueprint_hash == schema_hash:
                try:
                    cached = deserialize_json(
                        preference.cached_params,
                    )
                except ValueError:
                    cached = None
                if isinstance(
                    cached,
                    dict,
                ):
                    parameters = {**cached, **parameters}
        serialized = (
            serialize_json(
                parameters,
            )
            if request.save_preference
            else None
        )
        if serialized is not None:
            enforce_field_size_limits(
                {"cached_params": serialized},
                "blueprint preference",
            )
        submission = replace(
            submission,
            blueprint=BlueprintRun(
                blueprint_id,
                revision,
                schema_hash,
                serialized,
            ),
        )
        try:
            job_submission = blueprint_manager.execute(
                bp.code,
                parameters,
            )
            job = create_job(
                job_submission.model_dump(),
                current_user.id,
                db,
                default_target=bp.default_execution_target
                if bp.default_execution_target is not None
                else default_target,
                submission=submission,
            )
            return {"job_id": job.id}
        except BlueprintUnavailable as error:
            raise HTTPException(
                status_code=503,
                detail=str(
                    error,
                ),
            ) from error
        except (ValueError, NameError, TypeError) as error:
            raise HTTPException(
                status_code=400,
                detail=str(
                    error,
                ),
            ) from error
    except Exception:
        existing = submission.replay_after_failure(
            db,
            current_user.id,
        )
        if existing is not None:
            return {"job_id": existing.id}
        raise


@router.get(
    "/blueprints/{blueprint_id}/preference",
    response_model=BlueprintPreferenceResponse,
)
def get_blueprint_preference(
    blueprint_id: str,
    db: Session = Depends(
        database.get_db,
    ),
    current_user: models.User = Depends(
        get_current_user,
    ),
    default_target: Annotated[
        str | None,
        Header(
            alias="X-Magnus-Default-Execution-Target",
        ),
    ] = None,
):
    blueprint_id = resolve_resource_id(
        db,
        "blueprint",
        blueprint_id,
        default_target,
    )
    pref = (
        db.query(
            models.BlueprintUserPreference,
        )
        .filter(
            models.BlueprintUserPreference.user_id == current_user.id,
            models.BlueprintUserPreference.blueprint_id == blueprint_id,
        )
        .first()
    )

    if not pref:
        raise HTTPException(
            status_code=404,
            detail="No preference found",
        )

    return BlueprintPreferenceResponse(
        blueprint_id=pref.blueprint_id,
        blueprint_hash=pref.blueprint_hash,
        cached_params=deserialize_json(
            pref.cached_params,
        ),
        updated_at=pref.updated_at,
    )


@router.put(
    "/blueprints/{blueprint_id}/preference",
    response_model=BlueprintPreferenceResponse,
)
def save_blueprint_preference(
    blueprint_id: str,
    pref_update: BlueprintPreferenceUpdate,
    db: Session = Depends(
        database.get_db,
    ),
    current_user: models.User = Depends(
        get_current_user,
    ),
    default_target: Annotated[
        str | None,
        Header(
            alias="X-Magnus-Default-Execution-Target",
        ),
    ] = None,
):
    blueprint_id = resolve_resource_id(
        db,
        "blueprint",
        blueprint_id,
        default_target,
    )
    bp = (
        db.query(
            models.Blueprint,
        )
        .filter(
            models.Blueprint.id == blueprint_id,
        )
        .first()
    )
    if not bp:
        raise HTTPException(
            status_code=404,
            detail="Blueprint not found",
        )

    pref_update = pref_update.model_copy(
        update={
            "blueprint_id": resolve_resource_id(
                db,
                "blueprint",
                pref_update.blueprint_id,
                default_target,
            )
        },
    )
    if pref_update.blueprint_id != blueprint_id:
        raise HTTPException(
            status_code=400,
            detail="Blueprint ID mismatch",
        )

    revision = code_revision(
        bp.code,
    )
    current_hash = _compute_signature_hash(
        bp.code,
    )
    serialized_params = serialize_json(
        pref_update.cached_params,
    )
    enforce_field_size_limits(
        {"cached_params": serialized_params},
        "blueprint preference",
    )
    lock_user_for_write(
        db,
        current_user.id,
    )
    current = (
        db.query(
            models.Blueprint.code,
        )
        .filter(
            models.Blueprint.id == blueprint_id,
        )
        .first()
    )
    if (
        current is None
        or code_revision(
            current[0],
        )
        != revision
    ):
        raise HTTPException(
            status_code=409,
            detail="Blueprint changed; reload its parameters",
        )
    pref = save_preference(
        db,
        current_user.id,
        blueprint_id,
        current_hash,
        serialized_params,
    )
    db.commit()
    db.refresh(
        pref,
    )

    return BlueprintPreferenceResponse(
        blueprint_id=pref.blueprint_id,
        blueprint_hash=pref.blueprint_hash,
        cached_params=deserialize_json(
            pref.cached_params,
        ),
        updated_at=pref.updated_at,
    )
