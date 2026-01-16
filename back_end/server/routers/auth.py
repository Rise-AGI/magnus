# back_end/server/routers/auth.py
import secrets
import logging
import jwt
import asyncio
import time
from typing import List, Optional, Dict, Any
from dataclasses import dataclass

from fastapi import APIRouter, Depends, HTTPException, status, Request
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session

from .. import database
from .. import models
from ..schemas import UserInfo, LoginResponse, FeishuLoginRequest
from .._magnus_config import magnus_config
from .._jwt_signer import jwt_signer
from .._feishu_client import feishu_client


logger = logging.getLogger(__name__)
router = APIRouter()

# auto_error=False allows us to handle missing headers manually (e.g. query params/cookies/proxy calls)
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/feishu/login", auto_error=False)


@dataclass
class CachedUser:
    id: str
    name: str
    token: str
    expires_at: float


_auth_cache: Dict[str, CachedUser] = {}
AUTH_CACHE_TTL = 60.0


def _get_from_cache(
    token: str
) -> Optional[str]:
    if token in _auth_cache:
        cached = _auth_cache[token]
        if time.time() < cached.expires_at:
            return cached.id
        else:
            del _auth_cache[token]
    return None


def _add_to_cache(
    token: str, 
    user: models.User
) -> None:
    _auth_cache[token] = CachedUser(
        id = user.id,
        name = user.name,
        token = token,
        expires_at = time.time() + AUTH_CACHE_TTL
    )


def generate_trust_token() -> str:
    return f"sk-{secrets.token_urlsafe(24)}"


def _upsert_feishu_user_sync(
    db: Session, 
    feishu_user: Dict[str, Any]
) -> models.User:
    open_id = feishu_user.get("open_id") or feishu_user.get("union_id")
    if not open_id:
        raise HTTPException(status_code=400, detail="Missing OpenID")

    db_user = db.query(models.User).filter(models.User.feishu_open_id == open_id).first()

    if not db_user:
        db_user = models.User(
            feishu_open_id = open_id,
            name = feishu_user.get("name", "Unknown"),
            avatar_url = feishu_user.get("avatar_url"),
            email = feishu_user.get("email"),
            token = generate_trust_token(),
        )
        db.add(db_user)
        db.commit()
        db.refresh(db_user)
    else:
        db_user.name = feishu_user.get("name", db_user.name)
        db_user.avatar_url = feishu_user.get("avatar_url", db_user.avatar_url)
        if not db_user.token:
            db_user.token = generate_trust_token()
        db.commit()
        db.refresh(db_user)
    
    return db_user


def get_current_user(
    request: Request,
    token: Optional[str] = Depends(oauth2_scheme),
    db: Session = Depends(database.get_db)
) -> models.User:
    final_token = token

    # Manual header check for Proxy scenarios where Depends logic is bypassed (token passed as None)
    if not final_token:
        auth_header = request.headers.get("Authorization")
        if auth_header:
            scheme, _, param = auth_header.partition(" ")
            if scheme.lower() == "bearer":
                final_token = param

    if not final_token:
        final_token = request.query_params.get("token")

    if not final_token:
        final_token = request.cookies.get("access_token") or request.cookies.get("token")

    if not final_token:
        raise HTTPException(
            status_code = status.HTTP_401_UNAUTHORIZED,
            detail = "Not authenticated",
            headers = {"WWW-Authenticate": "Bearer"},
        )

    user_id_found: Optional[str] = None
    
    # 1. Check Cache
    cached_id = _get_from_cache(final_token)
    if cached_id:
        user_id_found = cached_id
    
    # 2. Check DB Token (SDK Trust Token)
    if not user_id_found:
        user_by_token = db.query(models.User).filter(models.User.token == final_token).first()
        if user_by_token:
            user_id_found = user_by_token.id
            _add_to_cache(final_token, user_by_token)

    # 3. Check JWT (Web Session)
    if not user_id_found:
        try:
            payload = jwt.decode(
                final_token,
                magnus_config["server"]["jwt_signer"]["secret_key"],
                algorithms = [magnus_config["server"]["jwt_signer"]["algorithm"]],
            )
            user_id = payload.get("sub")
            if user_id:
                user_id_found = str(user_id)
        except jwt.PyJWTError:
            pass

    if not user_id_found:
        raise HTTPException(
            status_code = status.HTTP_401_UNAUTHORIZED,
            detail = "Invalid authentication credentials",
            headers = {"WWW-Authenticate": "Bearer"},
        )

    # Fetch User by ID to ensure it is attached to the current DB Session.
    # Prevents 'DetachedInstanceError' when accessing lazy-loaded relationships.
    user = db.query(models.User).filter(models.User.id == user_id_found).first()
    
    if user is None:
        raise HTTPException(
            status_code = status.HTTP_401_UNAUTHORIZED,
            detail = "User not found",
        )

    if not cached_id:
        _add_to_cache(final_token, user)

    return user


@router.post(
    "/auth/feishu/login",
    response_model = LoginResponse,
)
async def feishu_login(
    req: FeishuLoginRequest,
    db: Session = Depends(database.get_db)
) -> Dict[str, Any]:
    try:
        feishu_user = await feishu_client.get_feishu_user(req.code)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

    db_user = await asyncio.to_thread(_upsert_feishu_user_sync, db, feishu_user)

    access_token = jwt_signer.create_access_token(payload={"sub": db_user.id})

    return {
        "access_token": access_token,
        "token_type": "bearer",
        "user": db_user,
    }


@router.post(
    "/auth/token/refresh",
    response_model = UserInfo,
)
def refresh_trust_token(
    db: Session = Depends(database.get_db),
    current_user: models.User = Depends(get_current_user)
) -> models.User:
    new_token = generate_trust_token()
    current_user.token = new_token

    db.commit()
    db.refresh(current_user)

    logger.info(f"User {current_user.id} ({current_user.name}) refreshed their trust token.")

    return current_user


@router.get(
    "/users",
    response_model = List[UserInfo],
)
def get_users(
    db: Session = Depends(database.get_db)
) -> List[models.User]:
    users = db.query(models.User).order_by(models.User.name).all()
    return users