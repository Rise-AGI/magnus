# back_end/server/routers/users.py
import io
import secrets
import logging
from typing import List, Optional, Dict, Any

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, status
from sqlalchemy.orm import Session
from sqlalchemy import func

from .. import database
from .. import models
from ..schemas import (
    UserInfo,
    UserDetail,
    AgentCreate,
    HeadcountUpdate,
    PagedUserResponse,
    TokenResponse,
    BotCredentialResponse,
)
from .._magnus_config import admin_open_ids
from .._file_custody_manager import file_custody_manager
from .auth import get_current_user, generate_trust_token, MAGNUS_TOKEN_LENGTH


logger = logging.getLogger(__name__)
router = APIRouter()


def _is_admin(user: models.User) -> bool:
    return user.feishu_open_id in admin_open_ids


def _is_ancestor(db: Session, ancestor_id: str, descendant_id: str) -> bool:
    """ancestor_id 是否是 descendant_id 的上级（递归向上走 parent 链）。"""
    visited = set()
    current_id = descendant_id
    while current_id and current_id not in visited:
        user = db.query(models.User).filter(models.User.id == current_id).first()
        if not user or not user.parent_id:
            return False
        if user.parent_id == ancestor_id:
            return True
        visited.add(current_id)
        current_id = user.parent_id
    return False


def _get_all_subordinate_ids(db: Session, user_id: str) -> List[str]:
    """递归收集所有下属的 ID。"""
    result: List[str] = []
    queue = [user_id]
    while queue:
        pid = queue.pop()
        children = db.query(models.User.id).filter(models.User.parent_id == pid).all()
        for (cid,) in children:
            result.append(cid)
            queue.append(cid)
    return result


def _can_manage(actor: models.User, target: models.User, db: Session) -> bool:
    """actor 是否有权管理 target（递归上级 或 admin）。"""
    if _is_admin(actor):
        return True
    return _is_ancestor(db, actor.id, target.id)


def _get_occupied_headcount(db: Session, user_id: str) -> int:
    """已被下属占用的编制：count(children) + sum(children.headcount)。
    公式与 _build_roster 中的内存批量计算一致，此处为单用户点查。
    """
    child_count = (
        db.query(func.count())
        .filter(models.User.parent_id == user_id)
        .scalar()
    ) or 0
    child_hc_sum = (
        db.query(func.coalesce(func.sum(models.User.headcount), 0))
        .filter(models.User.parent_id == user_id)
        .scalar()
    ) or 0
    return int(child_count) + int(child_hc_sum)


def _build_roster(
    db: Session,
) -> List[UserDetail]:
    """构建带递归计数的用户花名册。"""
    all_users = db.query(models.User).all()

    bp_counts: Dict[str, int] = {
        uid: cnt for uid, cnt in
        db.query(models.Blueprint.user_id, func.count())
        .group_by(models.Blueprint.user_id)
        .all()
    }
    svc_counts: Dict[str, int] = {
        uid: cnt for uid, cnt in
        db.query(models.Service.owner_id, func.count())
        .group_by(models.Service.owner_id)
        .all()
    }

    user_map: Dict[str, models.User] = {u.id: u for u in all_users}
    children_map: Dict[str, List[str]] = {}
    for u in all_users:
        if u.parent_id:
            children_map.setdefault(u.parent_id, []).append(u.id)

    # 递归汇总 blueprint / service 计数
    agg_bp: Dict[str, int] = {}
    agg_svc: Dict[str, int] = {}

    def _aggregate(uid: str) -> None:
        if uid in agg_bp:
            return
        own_bp = bp_counts.get(uid, 0)
        own_svc = svc_counts.get(uid, 0)
        for cid in children_map.get(uid, []):
            _aggregate(cid)
            own_bp += agg_bp[cid]
            own_svc += agg_svc[cid]
        agg_bp[uid] = own_bp
        agg_svc[uid] = own_svc

    for u in all_users:
        _aggregate(u.id)

    # 计算 available_headcount: 每个下属占 1 席位 + 其被分配的 headcount
    child_count: Dict[str, int] = {}
    child_headcount_sum: Dict[str, int] = {}
    for u in all_users:
        if u.parent_id:
            child_count[u.parent_id] = child_count.get(u.parent_id, 0) + 1
            if u.headcount is not None:
                child_headcount_sum[u.parent_id] = child_headcount_sum.get(u.parent_id, 0) + u.headcount

    result: List[UserDetail] = []
    for u in all_users:
        parent_name: Optional[str] = None
        parent_avatar_url: Optional[str] = None
        if u.parent_id and u.parent_id in user_map:
            parent_name = user_map[u.parent_id].name
            parent_avatar_url = user_map[u.parent_id].avatar_url

        available: Optional[int] = None
        if u.headcount is not None:
            available = u.headcount - child_count.get(u.id, 0) - child_headcount_sum.get(u.id, 0)

        result.append(UserDetail(
            id=u.id,
            name=u.name,
            avatar_url=u.avatar_url,
            is_admin=_is_admin(u),
            user_type=u.user_type,
            parent_id=u.parent_id,
            parent_name=parent_name,
            parent_avatar_url=parent_avatar_url,
            headcount=u.headcount,
            available_headcount=available,
            blueprint_count=agg_bp.get(u.id, 0),
            service_count=agg_svc.get(u.id, 0),
            created_at=u.created_at,
        ))

    # 排序：先按层级深度（BFS），同层内 human 在 agent 前，再按 name
    depth_map: Dict[str, int] = {}

    def _depth(uid: str) -> int:
        if uid in depth_map:
            return depth_map[uid]
        u = user_map.get(uid)
        if not u or not u.parent_id or u.parent_id not in user_map:
            depth_map[uid] = 0
        else:
            depth_map[uid] = _depth(u.parent_id) + 1
        return depth_map[uid]

    for u in all_users:
        _depth(u.id)

    result.sort(key=lambda d: (
        0 if d.user_type == "human" else 1,
        depth_map.get(d.id, 0),
        d.name,
    ))
    return result


# ─── 列表 ───────────────────────────────────────────────────────────────

@router.get(
    "/users",
    response_model=List[UserInfo],
)
def get_users(
    db: Session = Depends(database.get_db),
    _: models.User = Depends(get_current_user),
) -> List[UserInfo]:
    """轻量用户列表，保持向后兼容（jobs/blueprints/skills 筛选器在用）。"""
    users = db.query(models.User).order_by(models.User.name).all()
    return [
        UserInfo(
            id=u.id,
            name=u.name,
            avatar_url=u.avatar_url,
            email=u.email,
            is_admin=_is_admin(u),
        )
        for u in users
    ]


@router.get("/users/transfer-candidates", response_model=List[UserInfo])
def get_transfer_candidates(
    db: Session = Depends(database.get_db),
    current_user: models.User = Depends(get_current_user),
) -> List[UserInfo]:
    if _is_admin(current_user):
        users = db.query(models.User).order_by(models.User.name).all()
    else:
        subordinate_ids = _get_all_subordinate_ids(db, current_user.id)
        candidate_ids = [current_user.id] + subordinate_ids
        users = db.query(models.User).filter(
            models.User.id.in_(candidate_ids)
        ).order_by(models.User.name).all()
    return [
        UserInfo(
            id=u.id,
            name=u.name,
            avatar_url=u.avatar_url,
            email=u.email,
            is_admin=_is_admin(u),
        )
        for u in users
    ]


@router.get(
    "/users/roster",
    response_model=PagedUserResponse,
)
def get_user_roster(
    page: int = 1,
    page_size: int = 10,
    search: str = "",
    db: Session = Depends(database.get_db),
    _: models.User = Depends(get_current_user),
) -> PagedUserResponse:
    """People 页面用的完整花名册，含递归计数。"""
    roster = _build_roster(db)

    if search:
        q = search.strip().lower()
        roster = [u for u in roster if q in u.name.lower()]

    total = len(roster)
    start = (page - 1) * page_size
    items = roster[start:start + page_size]

    return PagedUserResponse(total=total, items=items)


# ─── Agent 管理 ─────────────────────────────────────────────────────────

@router.post(
    "/users/agents",
    response_model=Dict[str, Any],
    status_code=status.HTTP_201_CREATED,
)
def create_agent(
    body: AgentCreate,
    db: Session = Depends(database.get_db),
    current_user: models.User = Depends(get_current_user),
) -> Dict[str, Any]:
    """招募 AI Agent（当前用户为 parent，消耗 1 席位，headcount=0）。"""
    # 排他锁防并发超发；人类编制无限（headcount=None）不受限
    parent = db.query(models.User).filter(models.User.id == current_user.id).with_for_update().first()
    assert parent is not None
    if parent.headcount is not None:
        occupied = _get_occupied_headcount(db, parent.id)
        available = parent.headcount - occupied
        if available < 1:
            raise HTTPException(
                status_code=400,
                detail=f"Insufficient headcount. Available: {available}",
            )

    token = generate_trust_token()
    app_id = f"bot_{secrets.token_hex(8)}"
    app_secret = secrets.token_urlsafe(32)
    agent = models.User(
        name=body.name,
        user_type="agent",
        parent_id=current_user.id,
        headcount=0,
        token=token,
        app_id=app_id,
        app_secret=app_secret,
    )
    db.add(agent)
    db.commit()
    db.refresh(agent)

    logger.info(f"User {current_user.id} ({current_user.name}) recruited agent {agent.id} ({agent.name})")

    return {
        "id": agent.id,
        "name": agent.name,
        "token": token,
        "app_id": app_id,
        "app_secret": app_secret,
    }


@router.delete(
    "/users/{user_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def delete_user(
    user_id: str,
    db: Session = Depends(database.get_db),
    current_user: models.User = Depends(get_current_user),
) -> None:
    """删除 agent 用户（仅 parent / admin）。人类用户不可删。"""
    target = db.query(models.User).filter(models.User.id == user_id).first()
    if not target:
        raise HTTPException(status_code=404, detail="User not found")

    if target.user_type != "agent":
        raise HTTPException(status_code=400, detail="Cannot delete human users")

    if not _can_manage(current_user, target, db):
        raise HTTPException(status_code=403, detail="Permission denied")

    # 检查是否有下属
    child_count = db.query(func.count()).filter(models.User.parent_id == user_id).scalar()
    if child_count and child_count > 0:
        raise HTTPException(status_code=400, detail="Cannot delete user with subordinates")

    # 清理头像永久文件
    avatar_url = target.avatar_url or ""
    if "/api/files/download/" in avatar_url:
        old_token = avatar_url.rsplit("/", 1)[-1]
        file_custody_manager.delete_entry(old_token)

    db.delete(target)
    db.commit()

    logger.info(f"User {current_user.id} ({current_user.name}) deleted agent {user_id}")


# ─── 编制管理 ───────────────────────────────────────────────────────────

@router.patch(
    "/users/{user_id}/headcount",
)
def update_headcount(
    user_id: str,
    body: HeadcountUpdate,
    db: Session = Depends(database.get_db),
    current_user: models.User = Depends(get_current_user),
) -> Dict[str, Any]:
    """调整下属编制。"""
    target = db.query(models.User).filter(models.User.id == user_id).with_for_update().first()
    if not target:
        raise HTTPException(status_code=404, detail="User not found")

    if not _can_manage(current_user, target, db):
        raise HTTPException(status_code=403, detail="Permission denied")

    if target.user_type != "agent":
        raise HTTPException(status_code=400, detail="Cannot modify headcount of human users")

    new_headcount = body.headcount

    # 检查 target 自身下属占用了多少编制
    min_required = _get_occupied_headcount(db, user_id)
    if new_headcount < min_required:
        raise HTTPException(
            status_code=400,
            detail=f"Cannot reduce below allocated. Children occupy: {min_required}",
        )

    # 检查 target 真实上级的可用编制是否足够（delta 增量）
    old_headcount = target.headcount or 0
    delta = new_headcount - old_headcount
    if delta > 0 and target.parent_id is not None:
        parent = db.query(models.User).filter(models.User.id == target.parent_id).with_for_update().first()
        if parent and parent.headcount is not None:
            parent_available = parent.headcount - _get_occupied_headcount(db, parent.id)
            if delta > parent_available:
                raise HTTPException(
                    status_code=400,
                    detail=f"Insufficient headcount. Parent available: {parent_available}",
                )

    target.headcount = new_headcount
    db.commit()

    logger.info(f"User {current_user.id} updated headcount of {user_id} to {new_headcount}")

    return {"id": user_id, "headcount": new_headcount}


# ─── Token 管理 ─────────────────────────────────────────────────────────

@router.get(
    "/users/{user_id}/token",
    response_model=TokenResponse,
)
def get_user_token(
    user_id: str,
    db: Session = Depends(database.get_db),
    current_user: models.User = Depends(get_current_user),
) -> Dict[str, Any]:
    """查看下属 token（仅 parent / admin）。"""
    target = db.query(models.User).filter(models.User.id == user_id).first()
    if not target:
        raise HTTPException(status_code=404, detail="User not found")

    if target.id != current_user.id and not _can_manage(current_user, target, db):
        raise HTTPException(status_code=403, detail="Permission denied")

    return {"magnus_token": target.token or ""}


@router.post(
    "/users/{user_id}/token/refresh",
    response_model=TokenResponse,
)
def refresh_user_token(
    user_id: str,
    db: Session = Depends(database.get_db),
    current_user: models.User = Depends(get_current_user),
) -> Dict[str, Any]:
    """刷新下属 token（仅 parent / admin）。"""
    target = db.query(models.User).filter(models.User.id == user_id).first()
    if not target:
        raise HTTPException(status_code=404, detail="User not found")

    if target.id != current_user.id and not _can_manage(current_user, target, db):
        raise HTTPException(status_code=403, detail="Permission denied")

    new_token = generate_trust_token()
    target.token = new_token
    db.commit()
    db.refresh(target)

    logger.info(f"User {current_user.id} refreshed token for user {user_id}")

    return {"magnus_token": new_token}


@router.post(
    "/users/{user_id}/token/set",
    response_model=TokenResponse,
)
def set_user_token(
    user_id: str,
    payload: Dict[str, Any],
    db: Session = Depends(database.get_db),
    current_user: models.User = Depends(get_current_user),
) -> Dict[str, Any]:
    """自定义下属 token（仅 self / parent / admin）。"""
    target = db.query(models.User).filter(models.User.id == user_id).first()
    if not target:
        raise HTTPException(status_code=404, detail="User not found")

    if target.id != current_user.id and not _can_manage(current_user, target, db):
        raise HTTPException(status_code=403, detail="Permission denied")

    token = payload.get("token", "")
    if not token.startswith("sk-") or len(token) != MAGNUS_TOKEN_LENGTH:
        raise HTTPException(
            status_code=400,
            detail=f"Token must start with 'sk-' and be exactly {MAGNUS_TOKEN_LENGTH} characters.",
        )

    target.token = token
    db.commit()

    logger.info(f"User {current_user.id} set custom token for user {user_id}")

    return {"magnus_token": token}


# ─── Bot 凭证 ─────────────────────────────────────────────────────────

@router.get(
    "/users/{user_id}/bot-credentials",
    response_model=BotCredentialResponse,
)
def get_bot_credentials(
    user_id: str,
    db: Session = Depends(database.get_db),
    current_user: models.User = Depends(get_current_user),
) -> BotCredentialResponse:
    target = db.query(models.User).filter(models.User.id == user_id).first()
    if not target:
        raise HTTPException(status_code=404, detail="User not found")
    if target.user_type != "agent":
        raise HTTPException(status_code=400, detail="Only agent users have bot credentials")
    if not _can_manage(current_user, target, db):
        raise HTTPException(status_code=403, detail="Permission denied")
    if not target.app_id or not target.app_secret:
        raise HTTPException(status_code=404, detail="Bot credentials not generated yet")
    return BotCredentialResponse(app_id=target.app_id, app_secret=target.app_secret)


@router.post(
    "/users/{user_id}/bot-credentials/refresh",
    response_model=BotCredentialResponse,
)
def refresh_bot_credentials(
    user_id: str,
    db: Session = Depends(database.get_db),
    current_user: models.User = Depends(get_current_user),
) -> BotCredentialResponse:
    target = db.query(models.User).filter(models.User.id == user_id).first()
    if not target:
        raise HTTPException(status_code=404, detail="User not found")
    if target.user_type != "agent":
        raise HTTPException(status_code=400, detail="Only agent users have bot credentials")
    if not _can_manage(current_user, target, db):
        raise HTTPException(status_code=403, detail="Permission denied")

    if not target.app_id:
        target.app_id = f"bot_{secrets.token_hex(8)}"
    target.app_secret = secrets.token_urlsafe(32)
    db.commit()
    db.refresh(target)

    logger.info(f"User {current_user.id} refreshed bot credentials for agent {user_id}")

    assert target.app_id is not None and target.app_secret is not None
    return BotCredentialResponse(app_id=target.app_id, app_secret=target.app_secret)


# ─── 头像管理 ───────────────────────────────────────────────────────────

_ALLOWED_AVATAR_TYPES = {"image/png", "image/jpeg", "image/webp", "image/gif"}
_MAX_AVATAR_SIZE = 2 * 1024 * 1024  # 2 MB


@router.post(
    "/users/{user_id}/avatar",
)
async def upload_avatar(
    user_id: str,
    file: UploadFile = File(...),
    db: Session = Depends(database.get_db),
    current_user: models.User = Depends(get_current_user),
) -> Dict[str, Any]:
    """上传/更换下属头像（仅 parent / admin）。"""
    target = db.query(models.User).filter(models.User.id == user_id).first()
    if not target:
        raise HTTPException(status_code=404, detail="User not found")

    if target.id != current_user.id and not _can_manage(current_user, target, db):
        raise HTTPException(status_code=403, detail="Permission denied")

    if file.content_type not in _ALLOWED_AVATAR_TYPES:
        raise HTTPException(status_code=400, detail=f"Unsupported image type: {file.content_type}")

    # 删除旧头像
    old_url = target.avatar_url or ""
    if "/api/files/download/" in old_url:
        old_token = old_url.rsplit("/", 1)[-1]
        file_custody_manager.delete_entry(old_token)

    content = await file.read()
    if len(content) > _MAX_AVATAR_SIZE:
        raise HTTPException(status_code=400, detail="Avatar file too large (max 2 MB)")
    assert file.filename is not None
    token = file_custody_manager.store_file(
        filename=file.filename,
        file_obj=io.BytesIO(content),
        permanent=True,
    )

    target.avatar_url = f"/api/files/download/{token}"
    db.commit()

    logger.info(f"User {current_user.id} updated avatar for user {user_id}")

    return {"avatar_url": target.avatar_url}
