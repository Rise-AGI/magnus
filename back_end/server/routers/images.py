# back_end/server/routers/images.py
import os
import re
import asyncio
import logging
import subprocess
import threading
from typing import Dict, Optional
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload

from .. import database
from .. import models
from ..schemas import (
    CachedImageCreate,
    CachedImageResponse,
    PagedCachedImageResponse,
    TransferRequest,
)
from .auth import get_current_user
from .users import _is_ancestor, _get_all_subordinate_ids
from .._magnus_config import magnus_config, is_local_mode, is_admin_user
from .._resource_manager import resource_manager, _image_to_sif_filename


logger = logging.getLogger(__name__)
router = APIRouter()

magnus_root = magnus_config['server']['root']
container_cache_path = f"{magnus_root}/container_cache"


def _docker_image_cached(uri: str) -> bool:
    """local 模式专用（同步）：通过 docker image inspect 判断镜像是否已拉取。
    仅用于启动恢复 (recover_stuck_images)，热路径禁用。"""
    docker_image = re.sub(r'^[a-z]+://', '', uri)
    result = subprocess.run(
        ["docker", "image", "inspect", docker_image],
        capture_output=True,
    )
    return result.returncode == 0


async def _docker_image_cached_async(uri: str) -> bool:
    """local 模式专用（异步）：不阻塞事件循环的版本"""
    docker_image = re.sub(r'^[a-z]+://', '', uri)
    proc = await asyncio.create_subprocess_exec(
        "docker", "image", "inspect", docker_image,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    await proc.communicate()
    return proc.returncode == 0


# ─── 进程级状态 ──────────────────────────────────────────────────

# 启动恢复：只在首次 list 时执行一次，清理上次进程异常退出遗留的中间态
_recovered = False
_recover_lock = threading.Lock()

# 请求级互斥：同一 URI 的 API 操作（预热/刷新）不可并发穿透状态检查
# ensure_image 内部也有 asyncio.Lock 防止文件系统并发，这里防的是 DB 层面的 TOCTOU
_uri_locks: Dict[str, asyncio.Lock] = {}


def _get_uri_lock(uri: str) -> asyncio.Lock:
    if uri not in _uri_locks:
        _uri_locks[uri] = asyncio.Lock()
    return _uri_locks[uri]


def recover_stuck_images() -> None:
    """
    启动时调用：清理上次进程异常退出遗留的中间态。
    - .sif.tmp 文件：一律删除（不完整或孤儿进程遗留）
    - DB 中 pulling/refreshing 状态的记录：有 .sif 则恢复为 cached，否则删除记录
    必须在 lifespan 中调用，而不是懒加载——否则重启后 POST 请求会被 409 卡住。
    """
    global _recovered
    if _recovered:
        return
    with _recover_lock:
        if _recovered:
            return
        _recovered = True

    # 清理残留的 .tmp 文件（进程异常退出时遗留，或孤儿 apptainer 进程写的）
    # local 模式无 .sif 文件，跳过磁盘扫描
    if not is_local_mode and os.path.isdir(container_cache_path):
        for fname in os.listdir(container_cache_path):
            if fname.endswith(".sif.tmp"):
                tmp_path = os.path.join(container_cache_path, fname)
                try:
                    os.remove(tmp_path)
                    logger.info(f"Cleaned up stale tmp file: {fname}")
                except OSError:
                    pass

    db = database.SessionLocal()
    try:
        stuck = db.query(models.CachedImage).filter(
            models.CachedImage.status.in_(["pulling", "refreshing"]),
        ).all()
        if not stuck:
            return
        for img in stuck:
            if is_local_mode:
                if _docker_image_cached(img.uri):
                    img.status = "cached"
                    try:
                        docker_image = re.sub(r'^[a-z]+://', '', img.uri)
                        result = subprocess.run(
                            ["docker", "image", "inspect", "--format", "{{.Size}}", docker_image],
                            capture_output=True, text=True, timeout=10,
                        )
                        if result.returncode == 0:
                            img.size_bytes = int(result.stdout.strip())
                    except (OSError, ValueError, subprocess.TimeoutExpired):
                        pass
                    logger.info(f"Recovered stuck image → cached: {img.uri}")
                else:
                    img.status = "failed"
                    logger.info(f"Recovered stuck image → failed: {img.uri}")
            else:
                sif_path = os.path.join(container_cache_path, img.filename)
                if os.path.exists(sif_path):
                    img.status = "cached"
                    try:
                        img.size_bytes = os.stat(sif_path).st_size
                    except OSError:
                        pass
                    logger.info(f"Recovered stuck image → cached: {img.uri}")
                else:
                    img.status = "failed"
                    logger.info(f"Recovered stuck image → failed: {img.uri}")
        db.commit()
    finally:
        db.close()


def _is_admin(current_user: models.User) -> bool:
    return is_admin_user(current_user)


def _is_admin_or_owner(current_user: models.User, owner_id: str, db: Session) -> bool:
    if current_user.id == owner_id or _is_admin(current_user):
        return True
    return _is_ancestor(db, current_user.id, owner_id)


# ─── 后台拉取任务 ───────────────────────────────────────────────

async def _do_pull(image_id: int, uri: str, is_refresh: bool) -> None:
    """
    后台拉取镜像，由路由 handler 通过 asyncio.create_task 调度。
    分三阶段：防御性校验 → 拉取 → 加锁结算，每阶段独立 DB session。
    """

    # Phase 1: 防御性校验，确认记录仍处于预期的进行态
    db = database.SessionLocal()
    try:
        img = db.query(models.CachedImage).filter(models.CachedImage.id == image_id).first()
        if not img:
            return
        expected = "refreshing" if is_refresh else "pulling"
        if img.status != expected:
            logger.warning(f"Skipping _do_pull for image {image_id}: expected status '{expected}', got '{img.status}'")
            return
    finally:
        db.close()

    # Phase 2: 拉取镜像（耗时操作，不持有任何锁和 DB session）
    success, error_msg = await resource_manager.ensure_image(uri, force=is_refresh)

    # Phase 3: 状态结算（加锁防止和 delete_image / 新 pull 并发竞态）
    async with _get_uri_lock(uri):
        db = database.SessionLocal()
        try:
            img = db.query(models.CachedImage).filter(models.CachedImage.id == image_id).first()
            if not img:
                # 记录已被删除；仅当该 URI 无任何记录时才清理文件，避免误杀并发请求的成果
                any_exist = db.query(models.CachedImage).filter(models.CachedImage.uri == uri).first()
                if not any_exist and not is_local_mode:
                    sif_path = os.path.join(container_cache_path, _image_to_sif_filename(uri))
                    if os.path.exists(sif_path):
                        try:
                            os.remove(sif_path)
                            logger.info(f"Cleaned up orphan .sif after deleted record: {uri}")
                        except OSError:
                            pass
                return

            if success:
                if is_local_mode:
                    try:
                        docker_image = re.sub(r'^[a-z]+://', '', uri)
                        proc = await asyncio.create_subprocess_exec(
                            "docker", "image", "inspect", "--format", "{{.Size}}", docker_image,
                            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
                        )
                        stdout, _ = await proc.communicate()
                        if proc.returncode == 0:
                            img.size_bytes = int(stdout.strip())
                    except (OSError, ValueError):
                        pass
                else:
                    sif_path = os.path.join(container_cache_path, img.filename)
                    try:
                        img.size_bytes = os.stat(sif_path).st_size
                    except OSError:
                        img.size_bytes = 0
                img.status = "cached"
                img.updated_at = datetime.now(timezone.utc)
                db.commit()
            else:
                logger.error(f"Image {'refresh' if is_refresh else 'pull'} failed for {uri}: {error_msg}")
                if is_refresh:
                    # 刷新失败：判断旧镜像是否仍可用
                    if is_local_mode:
                        old_available = await _docker_image_cached_async(uri)
                    else:
                        sif_path = os.path.join(container_cache_path, img.filename)
                        old_available = os.path.exists(sif_path)
                    img.status = "cached" if old_available else "missing"
                    img.updated_at = datetime.now(timezone.utc)
                    db.commit()
                else:
                    # 首次拉取失败：保留记录，标记 failed，用户可重试或删除
                    img.status = "failed"
                    img.updated_at = datetime.now(timezone.utc)
                    db.commit()
        except Exception:
            db.rollback()
            logger.exception(f"Background pull task crashed for image {image_id}")
        finally:
            db.close()


# ─── 共享逻辑 ────────────────────────────────────────────────────

async def _begin_pull(
    db: Session,
    uri: str,
    current_user: models.User,
    is_refresh: bool,
    image_id: Optional[int] = None,
) -> CachedImageResponse:
    """
    预热和刷新共用的 check-set-commit-fire 流程。
    在调用方持有 _uri_locks[uri] 的前提下执行，保证同一 URI 不会并发穿透。

    is_refresh=False: 预热（可能是新镜像，也可能已存在则视为重新拉取）
    is_refresh=True:  刷新已知镜像（必须传 image_id）
    """
    filename = _image_to_sif_filename(uri)

    # force_pull: 已有文件时用 .tmp + rename 确保原子替换
    # 仅首次拉取全新镜像时 force=False，其余都 force=True
    force_pull = True

    if is_refresh:
        assert image_id is not None
        img = db.query(models.CachedImage).options(
            joinedload(models.CachedImage.user),
        ).filter(models.CachedImage.id == image_id).first()
        if not img:
            raise HTTPException(status_code=404, detail="Image not found")
        if not _is_admin_or_owner(current_user, img.user_id, db):
            raise HTTPException(status_code=403, detail="Only the owner or admin can refresh this image")
        if img.status in ("pulling", "refreshing"):
            raise HTTPException(status_code=409, detail="Image is already being pulled/refreshed.")
        img.status = "refreshing"
        img.updated_at = datetime.now(timezone.utc)
        db.commit()
        db.refresh(img)
    else:
        existing = db.query(models.CachedImage).filter(models.CachedImage.uri == uri).first()

        if existing and existing.status in ("pulling", "refreshing"):
            raise HTTPException(status_code=409, detail="Image is currently being pulled/refreshed.")
        if existing and not _is_admin_or_owner(current_user, existing.user_id, db):
            raise HTTPException(status_code=403, detail="Only the owner or admin can re-pull this image.")
        if existing and existing.status == "failed":
            existing.status = "pulling"
            existing.updated_at = datetime.now(timezone.utc)
            db.commit()
            db.refresh(existing)
            resp = CachedImageResponse.model_validate(existing)
            asyncio.create_task(_do_pull(existing.id, uri, is_refresh=False))
            return resp

        if existing:
            existing.status = "refreshing"
            existing.updated_at = datetime.now(timezone.utc)
            img = existing
        else:
            force_pull = False
            img = models.CachedImage(
                uri=uri,
                filename=filename,
                user_id=current_user.id,
                status="pulling",
            )
            db.add(img)

        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            raise HTTPException(status_code=409, detail="Image is already being pulled.")

        db.refresh(img)

    resp = CachedImageResponse.model_validate(img)
    asyncio.create_task(_do_pull(img.id, uri, is_refresh=force_pull))
    return resp


# ─── 路由 ───────────────────────────────────────────────────────

@router.get("/images", response_model=PagedCachedImageResponse)
def list_images(
    skip: int = 0,
    limit: int = 50,
    search: Optional[str] = None,
    db: Session = Depends(database.get_db),
    current_user: models.User = Depends(get_current_user),
):
    # 1. DB records
    query = db.query(models.CachedImage)
    if search:
        safe = search.replace("%", r"\%").replace("_", r"\_")
        query = query.filter(models.CachedImage.uri.ilike(f"%{safe}%", escape="\\"))

    db_images = query.options(joinedload(models.CachedImage.user)).all()
    db_filenames = {img.filename for img in db_images}

    # 2. 防御性扫描：磁盘上有 DB 里没有的 .sif → 标记 "unregistered"
    #    正常情况下不应出现（scheduler 和 API 都会自动注册）。
    #    如果运维看到 unregistered 镜像，说明有异常的镜像落盘路径，应排查。
    #    local 模式下不扫描 .sif 文件（Docker 自行管理镜像存储）。
    fs_items: list[CachedImageResponse] = []
    if not is_local_mode and os.path.isdir(container_cache_path):
        for fname in os.listdir(container_cache_path):
            if not fname.endswith(".sif"):
                continue
            if fname in db_filenames:
                continue
            if search and search.lower() not in fname.lower():
                continue
            fpath = os.path.join(container_cache_path, fname)
            try:
                stat = os.stat(fpath)
                fs_items.append(CachedImageResponse(
                    uri=fname.removesuffix(".sif"),
                    filename=fname,
                    status="unregistered",
                    size_bytes=stat.st_size,
                    updated_at=datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc),
                ))
            except OSError:
                continue

    # 3. 标记文件缺失的 DB 记录
    combined: list[CachedImageResponse] = []
    if is_local_mode:
        # local 模式信赖 DB 状态，不逐条 subprocess 验证 Docker 镜像
        # （Docker daemon 拉大镜像时会串行化 inspect 调用，导致线程池饱和、整个后端卡死）
        # DB 状态由 _do_pull 生命周期 + recover_stuck_images 启动恢复维护
        for img in db_images:
            combined.append(CachedImageResponse.model_validate(img))
    else:
        for img in db_images:
            sif_path = os.path.join(container_cache_path, img.filename)
            resp = CachedImageResponse.model_validate(img)
            if not os.path.exists(sif_path) and img.status not in ("refreshing", "pulling", "failed"):
                resp.status = "missing"
            combined.append(resp)

    combined.extend(fs_items)

    # human-first sort: 人类用户的镜像排前面，agent 排后面；同类按更新时间倒序
    user_type_map: dict[str, str] = {}
    for img in db_images:
        if img.user and img.user_id and img.user_id not in user_type_map:
            user_type_map[img.user_id] = img.user.user_type

    def _sort_key(x: CachedImageResponse) -> tuple:
        user_type_weight = 1  # default: agent / no owner → sort later
        if x.user_id and x.user_id in user_type_map:
            user_type_weight = 0 if user_type_map[x.user_id] == "human" else 1
        updated = x.updated_at or datetime.min.replace(tzinfo=timezone.utc)
        return (user_type_weight, -updated.timestamp())

    combined.sort(key=_sort_key)

    # 4. 计算 can_manage
    is_admin = _is_admin(current_user)
    subordinate_ids = set(_get_all_subordinate_ids(db, current_user.id)) if not is_admin else set()
    for resp in combined:
        if resp.user_id:
            resp.can_manage = is_admin or resp.user_id == current_user.id or resp.user_id in subordinate_ids

    total = len(combined)
    page = combined[skip:skip + limit]
    return {"total": total, "items": page}


@router.post("/images", response_model=CachedImageResponse, status_code=202)
async def pull_image(
    body: CachedImageCreate,
    db: Session = Depends(database.get_db),
    current_user: models.User = Depends(get_current_user),
):
    uri = body.uri.strip()
    async with _get_uri_lock(uri):
        return await _begin_pull(db, uri, current_user, is_refresh=False)


@router.post("/images/{image_id}/refresh", response_model=CachedImageResponse, status_code=202)
async def refresh_image(
    image_id: int,
    db: Session = Depends(database.get_db),
    current_user: models.User = Depends(get_current_user),
):
    # 先查出 URI 以获取正确的锁
    img = db.query(models.CachedImage).filter(models.CachedImage.id == image_id).first()
    if not img:
        raise HTTPException(status_code=404, detail="Image not found")
    async with _get_uri_lock(img.uri):
        return await _begin_pull(db, img.uri, current_user, is_refresh=True, image_id=image_id)


@router.delete("/images/{image_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_image(
    image_id: int,
    db: Session = Depends(database.get_db),
    current_user: models.User = Depends(get_current_user),
) -> None:
    img = db.query(models.CachedImage).filter(models.CachedImage.id == image_id).first()
    if not img:
        raise HTTPException(status_code=404, detail="Image not found")

    # 删除也要走锁：防止 delete 和 pull/refresh 并发冲突
    async with _get_uri_lock(img.uri):
        # 锁内重新读取状态
        db.expire(img)
        img = db.query(models.CachedImage).filter(models.CachedImage.id == image_id).first()
        if not img:
            raise HTTPException(status_code=404, detail="Image not found")

        if not _is_admin_or_owner(current_user, img.user_id, db):
            raise HTTPException(status_code=403, detail="Only the owner or admin can delete this image")

        if img.status in ("pulling", "refreshing"):
            raise HTTPException(status_code=409, detail="Cannot delete an image that is being pulled/refreshed.")

        if is_local_mode:
            docker_image = re.sub(r'^[a-z]+://', '', img.uri)
            proc = await asyncio.create_subprocess_exec(
                "docker", "rmi", docker_image,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await proc.communicate()
        else:
            sif_path = os.path.join(container_cache_path, img.filename)
            if os.path.exists(sif_path):
                try:
                    os.remove(sif_path)
                except OSError as e:
                    logger.warning(f"Failed to delete SIF file {sif_path}: {e}")

        db.delete(img)
        db.commit()


@router.post("/images/{image_id}/transfer", response_model=CachedImageResponse)
def transfer_image(
    image_id: int,
    body: TransferRequest,
    db: Session = Depends(database.get_db),
    current_user: models.User = Depends(get_current_user),
) -> models.CachedImage:
    img = db.query(models.CachedImage).options(
        joinedload(models.CachedImage.user),
    ).filter(models.CachedImage.id == image_id).first()
    if not img:
        raise HTTPException(status_code=404, detail="Image not found")

    is_admin = _is_admin(current_user)
    is_owner = img.user_id == current_user.id
    is_superior = not is_owner and _is_ancestor(db, current_user.id, img.user_id)

    if not (is_admin or is_owner or is_superior):
        raise HTTPException(status_code=403, detail="Permission denied")

    new_owner = db.query(models.User).filter(models.User.id == body.new_owner_id).first()
    if not new_owner:
        raise HTTPException(status_code=404, detail="Target user not found")
    if not is_admin and body.new_owner_id != current_user.id and not _is_ancestor(db, current_user.id, body.new_owner_id):
        raise HTTPException(status_code=403, detail="Target must be yourself or your subordinate")

    img.user_id = body.new_owner_id
    img.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(img)
    return img
