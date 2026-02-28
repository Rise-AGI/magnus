# back_end/server/_id_registry.py
from typing import List, Tuple, Type

from fastapi import HTTPException
from sqlalchemy.orm import Session

from . import models

PUBLISHABLE_MODELS: List[Tuple[Type, str]] = [
    (models.Blueprint, "blueprint"),
    (models.Service, "service"),
    (models.Skill, "skill"),
]


def assert_id_available(db: Session, proposed_id: str) -> None:
    for model, label in PUBLISHABLE_MODELS:
        if db.query(model).filter(model.id == proposed_id).first():
            raise HTTPException(
                status_code=409,
                detail=f"ID '{proposed_id}' is already in use by a {label}.",
            )
