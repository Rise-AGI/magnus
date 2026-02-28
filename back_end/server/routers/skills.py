# back_end/server/routers/skills.py
import logging
from typing import List, Optional
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload, subqueryload
from sqlalchemy import or_

from .. import database
from .. import models
from ..schemas import (
    SkillCreate,
    SkillFileCreate,
    SkillResponse,
    PagedSkillResponse,
)
from .._id_registry import assert_id_available
from .auth import get_current_user
from .._magnus_config import admin_open_ids


logger = logging.getLogger(__name__)
router = APIRouter()

SKILL_MAX_TOTAL_BYTES = 512 * 1024  # 512 KB


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
    if total_bytes > SKILL_MAX_TOTAL_BYTES:
        raise HTTPException(
            status_code=400,
            detail=f"Total file size ({total_bytes:,} bytes) exceeds {SKILL_MAX_TOTAL_BYTES:,} byte limit.",
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
        return existing

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
    return db_skill


@router.delete("/skills/{skill_id}")
def delete_skill(
    skill_id: str,
    db: Session = Depends(database.get_db),
    current_user: models.User = Depends(get_current_user),
):
    skill = db.query(models.Skill).filter(models.Skill.id == skill_id).first()
    if not skill:
        raise HTTPException(status_code=404, detail="Skill not found")

    if skill.user_id != current_user.id and current_user.feishu_open_id not in admin_open_ids:
        raise HTTPException(status_code=403, detail="You do not have permission to delete this skill")

    db.delete(skill)
    db.commit()
    return {"message": "Skill deleted successfully"}


@router.get("/skills", response_model=PagedSkillResponse)
def list_skills(
    skip: int = 0,
    limit: int = 20,
    search: Optional[str] = None,
    creator_id: Optional[str] = None,
    db: Session = Depends(database.get_db),
    _: models.User = Depends(get_current_user),
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

    items = query.options(joinedload(models.Skill.user), subqueryload(models.Skill.files))\
                 .order_by(models.Skill.updated_at.desc())\
                 .offset(skip)\
                 .limit(limit)\
                 .all()

    return {"total": total, "items": items}


@router.get("/skills/{skill_id}", response_model=SkillResponse)
def get_skill(
    skill_id: str,
    db: Session = Depends(database.get_db),
    _: models.User = Depends(get_current_user),
):
    skill = db.query(models.Skill)\
        .options(joinedload(models.Skill.user), joinedload(models.Skill.files))\
        .filter(models.Skill.id == skill_id)\
        .first()

    if not skill:
        raise HTTPException(status_code=404, detail="Skill not found")

    return skill


