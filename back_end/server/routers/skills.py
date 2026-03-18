# back_end/server/routers/skills.py
import logging
import shutil
import mimetypes
from pathlib import Path
from typing import List, Optional
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, UploadFile, status
from fastapi.responses import FileResponse, PlainTextResponse
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload, subqueryload
from sqlalchemy import or_, case

from .. import database
from .. import models
from ..schemas import (
    SkillCreate,
    SkillFileCreate,
    SkillFileResponse,
    SkillResponse,
    PagedSkillResponse,
    TransferRequest,
)
from .._id_registry import assert_id_available
from .auth import get_current_user
from .users import _is_ancestor, _get_all_subordinate_ids
from .._magnus_config import magnus_config, admin_open_ids


logger = logging.getLogger(__name__)
router = APIRouter()

SKILL_TEXT_MAX_TOTAL_BYTES = 512 * 1024  # 512 KB for text files
SKILL_RESOURCE_MAX_BYTES = 32 * 1024 * 1024  # 32 MB per resource file
ALLOWED_RESOURCE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".gif"}
RESOURCE_MIME_MAP = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".gif": "image/gif",
}


def _skill_resources_dir(skill_id: str) -> Path:
    return Path(magnus_config["server"]["root"]) / "skill_resources" / skill_id


def _discover_resource_files(skill_id: str) -> List[SkillFileResponse]:
    """扫描文件系统，发现 skill 的二进制资源文件。"""
    resources_dir = _skill_resources_dir(skill_id)
    if not resources_dir.exists():
        return []
    results: List[SkillFileResponse] = []
    for p in sorted(resources_dir.rglob("*")):
        if not p.is_file():
            continue
        rel = str(p.relative_to(resources_dir))
        results.append(SkillFileResponse(
            path=rel,
            content="",
            is_binary=True,
            updated_at=datetime.fromtimestamp(p.stat().st_mtime, tz=timezone.utc),
        ))
    return results


def _sync_files(
    db: Session,
    skill: models.Skill,
    file_inputs: List[SkillFileCreate],
) -> None:
    db.query(models.SkillFile).filter(models.SkillFile.skill_id == skill.id).delete()
    now = datetime.now(timezone.utc)
    for f in file_inputs:
        db.add(models.SkillFile(
            skill_id=skill.id,
            path=f.path,
            content=f.content,
            updated_at=now,
        ))


def _cleanup_skill_resources(skill_id: str) -> None:
    resources_dir = _skill_resources_dir(skill_id)
    if resources_dir.exists():
        shutil.rmtree(resources_dir, ignore_errors=True)


def _assert_can_manage(
    db: Session,
    skill: models.Skill,
    current_user: models.User,
) -> None:
    is_admin = current_user.feishu_open_id in admin_open_ids
    is_owner = skill.user_id == current_user.id
    is_superior = not is_owner and _is_ancestor(db, current_user.id, skill.user_id)
    if not (is_admin or is_owner or is_superior):
        raise HTTPException(status_code=403, detail="Permission denied")


def _enrich_response(skill: models.Skill, can_manage: bool) -> SkillResponse:
    """将 ORM 对象转为 response，附带文件系统上的 resource 文件。"""
    resp = SkillResponse.model_validate(skill)
    resp.can_manage = can_manage
    resp.files.extend(_discover_resource_files(skill.id))
    return resp


# ── CRUD ──────────────────────────────────────────────────────────────────────


@router.post("/skills", response_model=SkillResponse)
def create_skill(
    skill: SkillCreate,
    db: Session = Depends(database.get_db),
    current_user: models.User = Depends(get_current_user),
):
    has_skill_md = any(f.path == "SKILL.md" for f in skill.files)
    if not has_skill_md:
        raise HTTPException(status_code=400, detail="SKILL.md is required")

    total_bytes = sum(len(f.content.encode("utf-8")) for f in skill.files)
    if total_bytes > SKILL_TEXT_MAX_TOTAL_BYTES:
        raise HTTPException(
            status_code=400,
            detail=f"Total file size ({total_bytes:,} bytes) exceeds {SKILL_TEXT_MAX_TOTAL_BYTES:,} byte limit.",
        )

    existing = db.query(models.Skill).filter(models.Skill.id == skill.id).first()

    if not existing:
        assert_id_available(db, skill.id)

    if existing:
        if existing.user_id != current_user.id and current_user.feishu_open_id not in admin_open_ids:
            raise HTTPException(
                status_code=403,
                detail="You cannot modify a skill created by another user.",
            )
        existing.title = skill.title
        existing.description = skill.description
        existing.updated_at = datetime.now(timezone.utc)
        _sync_files(db, existing, skill.files)
        db.commit()
        db.refresh(existing)
        return _enrich_response(existing, can_manage=True)

    db_skill = models.Skill(
        id=skill.id,
        title=skill.title,
        description=skill.description,
        user_id=current_user.id,
    )
    db.add(db_skill)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail=f"ID '{skill.id}' is already in use.")
    _sync_files(db, db_skill, skill.files)
    db.commit()
    db.refresh(db_skill)
    return _enrich_response(db_skill, can_manage=True)


@router.delete("/skills/{skill_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_skill(
    skill_id: str,
    db: Session = Depends(database.get_db),
    current_user: models.User = Depends(get_current_user),
) -> None:
    skill = db.query(models.Skill).filter(models.Skill.id == skill_id).first()
    if not skill:
        raise HTTPException(status_code=404, detail="Skill not found")

    _assert_can_manage(db, skill, current_user)

    db.delete(skill)
    db.commit()
    _cleanup_skill_resources(skill_id)


@router.post("/skills/{skill_id}/transfer", response_model=SkillResponse)
def transfer_skill(
    skill_id: str,
    body: TransferRequest,
    db: Session = Depends(database.get_db),
    current_user: models.User = Depends(get_current_user),
) -> models.Skill:
    skill = db.query(models.Skill).options(joinedload(models.Skill.user))\
              .filter(models.Skill.id == skill_id).first()
    if not skill:
        raise HTTPException(status_code=404, detail="Skill not found")

    _assert_can_manage(db, skill, current_user)

    new_owner = db.query(models.User).filter(models.User.id == body.new_owner_id).first()
    if not new_owner:
        raise HTTPException(status_code=404, detail="Target user not found")
    is_admin = current_user.feishu_open_id in admin_open_ids
    if not is_admin and body.new_owner_id != current_user.id and not _is_ancestor(db, current_user.id, body.new_owner_id):
        raise HTTPException(status_code=403, detail="Target must be yourself or your subordinate")

    skill.user_id = body.new_owner_id
    skill.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(skill)
    return skill


@router.get("/skills", response_model=PagedSkillResponse)
def list_skills(
    skip: int = 0,
    limit: int = 20,
    search: Optional[str] = None,
    creator_id: Optional[str] = None,
    db: Session = Depends(database.get_db),
    current_user: models.User = Depends(get_current_user),
):
    query = db.query(models.Skill)

    if search:
        safe = search.replace("%", r"\%").replace("_", r"\_")
        search_pattern = f"%{safe}%"
        query = query.filter(
            or_(
                models.Skill.title.ilike(search_pattern, escape="\\"),
                models.Skill.id.ilike(search_pattern, escape="\\"),
                models.Skill.description.ilike(search_pattern, escape="\\"),
            )
        )

    if creator_id and creator_id != "all":
        query = query.filter(models.Skill.user_id == creator_id)

    total = query.count()

    human_first = case((models.User.user_type == "human", 0), else_=1)
    items = query.join(models.User, models.Skill.user_id == models.User.id)\
                 .options(joinedload(models.Skill.user), subqueryload(models.Skill.files))\
                 .order_by(human_first, models.Skill.updated_at.desc())\
                 .offset(skip).limit(limit).all()

    is_admin = current_user.feishu_open_id in admin_open_ids
    subordinate_ids = set(_get_all_subordinate_ids(db, current_user.id)) if not is_admin else set()
    result = []
    for skill in items:
        can_manage = is_admin or skill.user_id == current_user.id or skill.user_id in subordinate_ids
        resp = SkillResponse.model_validate(skill)
        resp.can_manage = can_manage
        result.append(resp)

    return {"total": total, "items": result}


@router.get("/skills/{skill_id}", response_model=SkillResponse)
def get_skill(
    skill_id: str,
    db: Session = Depends(database.get_db),
    current_user: models.User = Depends(get_current_user),
):
    skill = db.query(models.Skill)\
        .options(joinedload(models.Skill.user), joinedload(models.Skill.files))\
        .filter(models.Skill.id == skill_id)\
        .first()

    if not skill:
        raise HTTPException(status_code=404, detail="Skill not found")

    is_admin = current_user.feishu_open_id in admin_open_ids
    can_manage = is_admin or skill.user_id == current_user.id or _is_ancestor(db, current_user.id, skill.user_id)
    return _enrich_response(skill, can_manage)


# ── Resource (binary) file management ─────────────────────────────────────────


@router.post("/skills/{skill_id}/resources")
def upload_skill_resource(
    skill_id: str,
    file: UploadFile,
    db: Session = Depends(database.get_db),
    current_user: models.User = Depends(get_current_user),
):
    skill = db.query(models.Skill).filter(models.Skill.id == skill_id).first()
    if not skill:
        raise HTTPException(status_code=404, detail="Skill not found")

    _assert_can_manage(db, skill, current_user)

    filename = file.filename or ""
    ext = Path(filename).suffix.lower()
    if ext not in ALLOWED_RESOURCE_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file type '{ext}'. Allowed: {', '.join(sorted(ALLOWED_RESOURCE_EXTENSIONS))}",
        )

    content = file.file.read()
    if len(content) > SKILL_RESOURCE_MAX_BYTES:
        raise HTTPException(
            status_code=400,
            detail=f"File size ({len(content):,} bytes) exceeds {SKILL_RESOURCE_MAX_BYTES:,} byte limit.",
        )

    rel_path = filename.strip().replace("\\", "/")
    if not rel_path or rel_path.startswith("/") or ".." in rel_path.split("/"):
        raise HTTPException(status_code=400, detail="Invalid file path")

    resources_dir = _skill_resources_dir(skill_id)
    dest = resources_dir / rel_path
    if not dest.resolve().is_relative_to(resources_dir.resolve()):
        raise HTTPException(status_code=400, detail="Invalid file path")
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(content)

    skill.updated_at = datetime.now(timezone.utc)
    db.commit()

    return {"path": rel_path, "size": len(content)}


@router.delete("/skills/{skill_id}/resources/{path:path}", status_code=status.HTTP_204_NO_CONTENT)
def delete_skill_resource(
    skill_id: str,
    path: str,
    db: Session = Depends(database.get_db),
    current_user: models.User = Depends(get_current_user),
) -> None:
    skill = db.query(models.Skill).filter(models.Skill.id == skill_id).first()
    if not skill:
        raise HTTPException(status_code=404, detail="Skill not found")

    _assert_can_manage(db, skill, current_user)

    resources_dir = _skill_resources_dir(skill_id)
    file_path = resources_dir / path
    if not file_path.resolve().is_relative_to(resources_dir.resolve()):
        raise HTTPException(status_code=400, detail="Invalid file path")
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="Resource not found")
    file_path.unlink()

    skill.updated_at = datetime.now(timezone.utc)
    db.commit()


@router.get("/skills/{skill_id}/files/{path:path}")
def serve_skill_file(
    skill_id: str,
    path: str,
    db: Session = Depends(database.get_db),
    current_user: models.User = Depends(get_current_user),
):
    # 先查文件系统（二进制资源）
    resources_dir = _skill_resources_dir(skill_id)
    resource_path = resources_dir / path
    if not resource_path.resolve().is_relative_to(resources_dir.resolve()):
        raise HTTPException(status_code=400, detail="Invalid file path")
    if resource_path.exists() and resource_path.is_file():
        ext = Path(path).suffix.lower()
        media_type = RESOURCE_MIME_MAP.get(ext) or mimetypes.guess_type(path)[0] or "application/octet-stream"
        return FileResponse(resource_path, media_type=media_type, filename=Path(path).name)

    # 再查数据库（文本文件）
    skill_file = db.query(models.SkillFile).filter(
        models.SkillFile.skill_id == skill_id,
        models.SkillFile.path == path,
    ).first()

    if not skill_file:
        raise HTTPException(status_code=404, detail="File not found")

    return PlainTextResponse(skill_file.content, media_type="text/plain; charset=utf-8")
