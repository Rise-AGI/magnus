# back_end/server/routers/images.py
import os
import asyncio
import logging
import threading
from typing import List, Optional
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload

from .. import database
from .. import models
from ..schemas import (
    CachedImageCreate,
    CachedImageResponse,
    PagedCachedImageResponse,
)
from .auth import get_current_user
from .._magnus_config import magnus_config, admin_open_ids
from .._resource_manager import resource_manager, _image_to_sif_filename


logger = logging.getLogger(__name__)
router = APIRouter()

magnus_root = magnus_config['server']['root']
container_cache_path = f"{magnus_root}/container_cache"

_recovered = False
_recover_lock = threading.Lock()


def _recover_stuck_images(db: Session) -> None:
    global _recovered
    if _recovered:
        return
    with _recover_lock:
        if _recovered:
            return
        _recovered = True

    # 清理残留的 .tmp 文件（进程异常退出时遗留）
    if os.path.isdir(container_cache_path):
        for fname in os.listdir(container_cache_path):
            if fname.endswith(".sif.tmp"):
                tmp_path = os.path.join(container_cache_path, fname)
                try:
                    os.remove(tmp_path)
                    logger.info(f"Cleaned up stale tmp file: {fname}")
                except OSError:
                    pass

    stuck = db.query(models.CachedImage).filter(
        models.CachedImage.status.in_(["pulling", "refreshing"]),
    ).all()
    if not stuck:
        return
    for img in stuck:
        sif_path = os.path.join(container_cache_path, img.filename)
        if os.path.exists(sif_path):
            img.status = "cached"
            try:
                img.size_bytes = os.stat(sif_path).st_size
            except OSError:
                pass
            logger.info(f"Recovered stuck image → cached: {img.uri}")
        else:
            db.delete(img)
            logger.info(f"Removed orphan image record: {img.uri}")
    db.commit()


def _is_admin(current_user: models.User) -> bool:
    return current_user.feishu_open_id in admin_open_ids


def _is_admin_or_owner(current_user: models.User, owner_id: str) -> bool:
    return current_user.id == owner_id or _is_admin(current_user)


# ─── 后台拉取任务 ───────────────────────────────────────────────

async def _do_pull(image_id: int, uri: str, is_refresh: bool) -> None:
    """
    后台拉取镜像。
    - is_refresh=True: force pull 到 .tmp 再 rename（旧文件在 rename 前保持可用）
    - is_refresh=False: 正常 pull（首次拉取）
    失败时：refresh 恢复 "cached"/"missing"，new pull 删除 DB 记录。
    """
    db = database.SessionLocal()
    try:
        success, error_msg = await resource_manager.ensure_image(uri, force=is_refresh)

        img = db.query(models.CachedImage).filter(models.CachedImage.id == image_id).first()
        if not img:
            return

        sif_path = os.path.join(container_cache_path, img.filename)

        if success:
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
                img.status = "cached" if os.path.exists(sif_path) else "missing"
                img.updated_at = datetime.now(timezone.utc)
                db.commit()
            else:
                db.delete(img)
                db.commit()
    except Exception:
        db.rollback()
        logger.exception(f"Background pull task crashed for image {image_id}")
    finally:
        db.close()


# ─── 路由 ───────────────────────────────────────────────────────

@router.get("/images", response_model=PagedCachedImageResponse)
def list_images(
    skip: int = 0,
    limit: int = 50,
    search: Optional[str] = None,
    db: Session = Depends(database.get_db),
    _: models.User = Depends(get_current_user),
):
    _recover_stuck_images(db)

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
    fs_items: List[CachedImageResponse] = []
    if os.path.isdir(container_cache_path):
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

    # 3. Mark DB records with missing files
    combined: List[CachedImageResponse] = []
    for img in db_images:
        sif_path = os.path.join(container_cache_path, img.filename)
        resp = CachedImageResponse.model_validate(img)
        if not os.path.exists(sif_path) and img.status not in ("refreshing", "pulling"):
            resp.status = "missing"
        combined.append(resp)

    combined.extend(fs_items)
    combined.sort(key=lambda x: x.updated_at or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
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
    filename = _image_to_sif_filename(uri)

    existing = db.query(models.CachedImage).filter(models.CachedImage.uri == uri).first()

    if existing and existing.status in ("pulling", "refreshing"):
        raise HTTPException(status_code=409, detail="Image is currently being pulled/refreshed.")

    # 已存在的镜像只有 owner 或 admin 可以重新拉取
    if existing and not _is_admin_or_owner(current_user, existing.user_id):
        raise HTTPException(status_code=403, detail="Only the owner or admin can re-pull this image.")

    is_refresh = existing is not None
    if not is_refresh:
        existing = models.CachedImage(
            uri=uri,
            filename=filename,
            user_id=current_user.id,
            status="pulling",
        )
        db.add(existing)
    else:
        existing.status = "pulling"
        existing.updated_at = datetime.now(timezone.utc)

    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="Image is already being pulled.")

    db.refresh(existing)

    image_id = existing.id
    resp = CachedImageResponse.model_validate(existing)

    asyncio.create_task(_do_pull(image_id, uri, is_refresh=is_refresh))

    return resp


@router.post("/images/{image_id}/refresh", response_model=CachedImageResponse, status_code=202)
async def refresh_image(
    image_id: int,
    db: Session = Depends(database.get_db),
    current_user: models.User = Depends(get_current_user),
):
    img = db.query(models.CachedImage).options(
        joinedload(models.CachedImage.user),
    ).filter(models.CachedImage.id == image_id).first()

    if not img:
        raise HTTPException(status_code=404, detail="Image not found")

    if not _is_admin_or_owner(current_user, img.user_id):
        raise HTTPException(status_code=403, detail="Only the owner or admin can refresh this image")

    if img.status in ("pulling", "refreshing"):
        raise HTTPException(status_code=409, detail="Image is already being pulled/refreshed.")

    img.status = "refreshing"
    img.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(img)

    resp = CachedImageResponse.model_validate(img)

    asyncio.create_task(_do_pull(img.id, img.uri, is_refresh=True))

    return resp


@router.delete("/images/{image_id}")
def delete_image(
    image_id: int,
    db: Session = Depends(database.get_db),
    current_user: models.User = Depends(get_current_user),
):
    img = db.query(models.CachedImage).filter(models.CachedImage.id == image_id).first()

    if not img:
        raise HTTPException(status_code=404, detail="Image not found")

    if not _is_admin_or_owner(current_user, img.user_id):
        raise HTTPException(status_code=403, detail="Only the owner or admin can delete this image")

    if img.status in ("pulling", "refreshing"):
        raise HTTPException(status_code=409, detail="Cannot delete an image that is being pulled/refreshed.")

    sif_path = os.path.join(container_cache_path, img.filename)
    if os.path.exists(sif_path):
        try:
            os.remove(sif_path)
        except OSError as e:
            logger.warning(f"Failed to delete SIF file {sif_path}: {e}")

    db.delete(img)
    db.commit()
    return {"message": "Image deleted successfully"}
