# back_end/server/routers/invite.py
import secrets
import logging
from typing import Dict, Any
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from passlib.hash import bcrypt

from .. import database
from .. import models
from ..schemas import (
    InviteCodeCreate,
    InviteCodeResponse,
    PagedInviteCodeResponse,
    RegisterRequest,
    PasswordLoginRequest,
    LoginResponse,
    UserInfo,
)
from .._magnus_config import is_admin_user, is_local_mode
from .._jwt_signer import jwt_signer
from .auth import get_current_user, generate_trust_token


logger = logging.getLogger(__name__)
router = APIRouter()


def _generate_invite_code() -> str:
    return f"MAGNUS-{secrets.token_hex(4).upper()}"


# ─── Invite Code Management (Admin Only) ─────────────────────────────


@router.post("/invite-codes", response_model=InviteCodeResponse)
def create_invite_code(
    body: InviteCodeCreate,
    db: Session = Depends(database.get_db),
    current_user: models.User = Depends(get_current_user),
) -> Any:
    if not is_admin_user(current_user):
        raise HTTPException(status_code=403, detail="Only admins can create invite codes")

    invite = models.InviteCode(
        code=_generate_invite_code(),
        created_by=current_user.id,
        max_uses=body.max_uses,
        expires_at=body.expires_at,
    )
    db.add(invite)
    db.commit()
    db.refresh(invite)

    logger.info(f"Admin {current_user.name} created invite code {invite.code}")

    return InviteCodeResponse(
        id=invite.id,
        code=invite.code,
        created_by=invite.created_by,
        creator=UserInfo(id=current_user.id, name=current_user.name, avatar_url=current_user.avatar_url),
        max_uses=invite.max_uses,
        use_count=invite.use_count,
        expires_at=invite.expires_at,
        is_active=invite.is_active,
        created_at=invite.created_at,
    )


@router.get("/invite-codes", response_model=PagedInviteCodeResponse)
def list_invite_codes(
    page: int = 1,
    page_size: int = 20,
    db: Session = Depends(database.get_db),
    current_user: models.User = Depends(get_current_user),
) -> Any:
    if not is_admin_user(current_user):
        raise HTTPException(status_code=403, detail="Only admins can view invite codes")

    query = db.query(models.InviteCode).order_by(models.InviteCode.created_at.desc())
    total = query.count()
    items = query.offset((page - 1) * page_size).limit(page_size).all()

    return PagedInviteCodeResponse(
        total=total,
        items=[InviteCodeResponse.model_validate(item) for item in items],
    )


@router.delete("/invite-codes/{code_id}", status_code=204)
def deactivate_invite_code(
    code_id: str,
    db: Session = Depends(database.get_db),
    current_user: models.User = Depends(get_current_user),
) -> None:
    if not is_admin_user(current_user):
        raise HTTPException(status_code=403, detail="Only admins can deactivate invite codes")

    invite = db.query(models.InviteCode).filter(models.InviteCode.id == code_id).first()
    if not invite:
        raise HTTPException(status_code=404, detail="Invite code not found")

    invite.is_active = False
    db.commit()

    logger.info(f"Admin {current_user.name} deactivated invite code {invite.code}")


# ─── Public Auth Endpoints (No Auth Required) ────────────────────────


@router.post("/auth/register", response_model=LoginResponse)
def register(
    body: RegisterRequest,
    db: Session = Depends(database.get_db),
) -> Dict[str, Any]:
    if is_local_mode:
        raise HTTPException(status_code=501, detail="Registration is not available in local mode")

    invite = db.query(models.InviteCode).filter(
        models.InviteCode.code == body.invite_code,
        models.InviteCode.is_active.is_(True),
    ).first()

    if not invite:
        raise HTTPException(status_code=400, detail="Invalid invite code")

    now = datetime.now(timezone.utc)

    if invite.expires_at and invite.expires_at < now:
        raise HTTPException(status_code=400, detail="Invite code has expired")

    if invite.max_uses is not None and invite.use_count >= invite.max_uses:
        raise HTTPException(status_code=400, detail="Invite code has reached its usage limit")

    existing = db.query(models.User).filter(
        models.User.name == body.name,
        models.User.password_hash.isnot(None),
    ).first()
    if existing:
        raise HTTPException(status_code=409, detail="Username already taken")

    password_hash = bcrypt.hash(body.password)
    user = models.User(
        name=body.name,
        password_hash=password_hash,
        user_type="human",
        token=generate_trust_token(),
    )
    db.add(user)

    invite.use_count += 1
    db.commit()
    db.refresh(user)

    access_token = jwt_signer.create_access_token(payload={"sub": user.id})

    logger.info(f"New user registered: {user.name} (id={user.id}) via invite code {invite.code}")

    return {
        "access_token": access_token,
        "token_type": "bearer",
        "user": {
            "id": user.id,
            "name": user.name,
            "avatar_url": user.avatar_url,
            "email": user.email,
            "is_admin": False,
        },
    }


@router.post("/auth/password/login", response_model=LoginResponse)
def password_login(
    body: PasswordLoginRequest,
    db: Session = Depends(database.get_db),
) -> Dict[str, Any]:
    if is_local_mode:
        raise HTTPException(status_code=501, detail="Password login is not available in local mode")

    user = db.query(models.User).filter(
        models.User.name == body.name,
        models.User.password_hash.isnot(None),
    ).first()

    if not user or not bcrypt.verify(body.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid credentials")

    access_token = jwt_signer.create_access_token(payload={"sub": user.id})

    return {
        "access_token": access_token,
        "token_type": "bearer",
        "user": {
            "id": user.id,
            "name": user.name,
            "avatar_url": user.avatar_url,
            "email": user.email,
            "is_admin": False,
        },
    }
