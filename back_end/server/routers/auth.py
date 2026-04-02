# back_end/server/routers/auth.py
import secrets
import logging
import jwt
import asyncio
from typing import Optional, Dict, Any
from dataclasses import dataclass

from cachetools import TTLCache
from fastapi import APIRouter, Depends, HTTPException, status, Request
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session

from .. import database
from .. import models
from ..schemas import LoginResponse, FeishuLoginRequest, TokenLoginRequest, TokenResponse
from .._magnus_config import magnus_config, admin_open_ids, is_local_mode
from .._jwt_signer import jwt_signer
from .._feishu_client import feishu_client


logger = logging.getLogger(__name__)
router = APIRouter()

# auto_error=False allows us to handle missing headers manually (e.g. query params/cookies/proxy calls)
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/feishu/login", auto_error=False)


AUTH_CACHE_TTL = 60
AUTH_CACHE_MAX_SIZE = 1000
_auth_cache: TTLCache[str, str] = TTLCache(maxsize=AUTH_CACHE_MAX_SIZE, ttl=AUTH_CACHE_TTL)


def _get_from_cache(
    token: str
)-> Optional[str]:
    return _auth_cache.get(token)


def _add_to_cache(
    token: str,
    user: models.User,
)-> None:
    _auth_cache[token] = user.id


def generate_trust_token()-> str:
    return f"sk-{secrets.token_urlsafe(24)}"


def _upsert_feishu_user_sync(
    db: Session, 
    feishu_user: Dict[str, Any]
)-> models.User:
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
)-> models.User:
    # Local 模式不鉴权：127.0.0.1 only，直接返回唯一用户
    if is_local_mode:
        local_user = db.query(models.User).first()
        if local_user is None:
            raise HTTPException(status_code=500, detail="No local user found. Server may not have initialized properly.")
        return local_user

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
                magnus_config["server"]["auth"]["jwt_signer"]["secret_key"],
                algorithms = [magnus_config["server"]["auth"]["jwt_signer"]["algorithm"]],
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


@router.get("/auth/mode")
def auth_mode() -> Dict[str, str]:
    """返回当前认证模式，供前端判断。"""
    return {"provider": magnus_config["server"]["auth"]["provider"]}


@router.post(
    "/auth/local/login",
    response_model=LoginResponse,
)
def local_login(
    db: Session = Depends(database.get_db),
) -> Dict[str, Any]:
    """本地模式免登录：返回本地用户信息，供前端初始化。"""
    if not is_local_mode:
        raise HTTPException(status_code=501, detail="Local login is only available in local mode")

    local_user = db.query(models.User).first()
    if not local_user:
        raise HTTPException(status_code=500, detail="Local user not found. Server may not have initialized properly.")

    access_token = jwt_signer.create_access_token(payload={"sub": local_user.id})

    return {
        "access_token": access_token,
        "token_type": "bearer",
        "user": {
            "id": local_user.id,
            "name": local_user.name,
            "avatar_url": local_user.avatar_url,
            "email": local_user.email,
            "is_admin": True,
        },
    }


@router.post(
    "/auth/feishu/login",
    response_model = LoginResponse,
)
async def feishu_login(
    req: FeishuLoginRequest,
    db: Session = Depends(database.get_db)
)-> Dict[str, Any]:
    if is_local_mode:
        raise HTTPException(status_code=501, detail="Feishu login is not available in local mode")

    try:
        feishu_user = await feishu_client.get_feishu_user(req.code)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

    db_user = await asyncio.to_thread(_upsert_feishu_user_sync, db, feishu_user)

    access_token = jwt_signer.create_access_token(payload={"sub": db_user.id})

    return {
        "access_token": access_token,
        "token_type": "bearer",
        "user": {
            "id": db_user.id,
            "name": db_user.name,
            "avatar_url": db_user.avatar_url,
            "email": db_user.email,
            "is_admin": is_local_mode or db_user.feishu_open_id in admin_open_ids,
        },
    }


@router.post(
    "/auth/token/login",
    response_model=LoginResponse,
)
def token_login(
    req: TokenLoginRequest,
    db: Session = Depends(database.get_db),
) -> Dict[str, Any]:
    """通过 Magnus Token (sk-xxx) 登录，返回 JWT + 用户信息。"""
    user = db.query(models.User).filter(models.User.token == req.token).first()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid Magnus Token",
        )

    access_token = jwt_signer.create_access_token(payload={"sub": user.id})

    return {
        "access_token": access_token,
        "token_type": "bearer",
        "user": {
            "id": user.id,
            "name": user.name,
            "avatar_url": user.avatar_url,
            "email": user.email,
            "is_admin": is_local_mode or user.feishu_open_id in admin_open_ids,
        },
    }


@router.post(
    "/auth/token/refresh",
    response_model = TokenResponse,
)
def refresh_trust_token(
    db: Session = Depends(database.get_db),
    current_user: models.User = Depends(get_current_user)
)-> Dict[str, Any]:
    new_token = generate_trust_token()
    current_user.token = new_token

    db.commit()
    db.refresh(current_user)

    logger.info(f"User {current_user.id} ({current_user.name}) refreshed their trust token.")

    return {"magnus_token": new_token}


MAGNUS_TOKEN_LENGTH = 35


@router.post(
    "/auth/token/set",
    response_model = TokenResponse,
)
def set_custom_token(
    payload: Dict[str, Any],
    db: Session = Depends(database.get_db),
    current_user: models.User = Depends(get_current_user),
)-> Dict[str, Any]:
    token = payload.get("token", "")
    if not token.startswith("sk-") or len(token) != MAGNUS_TOKEN_LENGTH:
        raise HTTPException(
            status_code = 400,
            detail = f"Token must start with 'sk-' and be exactly {MAGNUS_TOKEN_LENGTH} characters.",
        )

    current_user.token = token
    db.commit()
    db.refresh(current_user)

    logger.info(f"User {current_user.id} ({current_user.name}) set a custom trust token.")

    return {"magnus_token": token}


@router.get(
    "/auth/my-token",
    response_model = TokenResponse,
)
def get_my_token(
    current_user: models.User = Depends(get_current_user),
)-> Dict[str, Any]:
    return {"magnus_token": current_user.token or ""}
