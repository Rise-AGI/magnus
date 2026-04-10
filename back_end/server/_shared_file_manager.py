import asyncio
import json
import logging
import os
import re
import secrets
import shutil
import tarfile
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from ._magnus_config import magnus_config
from .database import SessionLocal
from .models import Job, JobStatus


logger = logging.getLogger(__name__)

SHARED_FILE_ROOT = Path("/data/sharedfile")
ARCHIVED_SHARED_FILE_ROOT = Path("/data/archived_sharedfile")
PROPERTIES_FILENAME = ".properties"
MOUNT_NAME_PATTERN = re.compile(r"^[A-Za-z0-9_-]+$")


class SharedFileError(Exception):
    pass


class SharedFileRateLimitError(SharedFileError):
    def __init__(self, wait_seconds: int):
        self.wait_seconds = wait_seconds
        super().__init__(f"等待{wait_seconds}秒重试")


class SharedFileValidationError(SharedFileError):
    pass


class SharedFileNotFoundError(SharedFileError):
    pass


class SharedFileInvalidatedError(SharedFileError):
    pass


def _utc_now()-> datetime:
    return datetime.now(timezone.utc)


def _parse_iso_datetime(value: str)-> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _get_dir_size(path: Path)-> int:
    total = 0
    for dir_path, _, filenames in os.walk(path):
        for name in filenames:
            file_path = os.path.join(dir_path, name)
            try:
                total += os.path.getsize(file_path)
            except OSError:
                pass
    return total


class SharedFileManager:
    def __init__(self)-> None:
        global SHARED_FILE_ROOT
        global ARCHIVED_SHARED_FILE_ROOT

        shared_cfg = magnus_config["server"]["sharedfile"]
        SHARED_FILE_ROOT = Path(shared_cfg["root_path"])
        ARCHIVED_SHARED_FILE_ROOT = Path(shared_cfg["archived_root_path"])

        try:
            SHARED_FILE_ROOT.mkdir(parents=True, exist_ok=True)
            ARCHIVED_SHARED_FILE_ROOT.mkdir(parents=True, exist_ok=True)
        except PermissionError:
            fallback_root = Path(magnus_config["server"]["root"]) / "sharedfile"
            fallback_archived_root = Path(magnus_config["server"]["root"]) / "archived_sharedfile"
            fallback_root.mkdir(parents=True, exist_ok=True)
            fallback_archived_root.mkdir(parents=True, exist_ok=True)
            SHARED_FILE_ROOT = fallback_root
            ARCHIVED_SHARED_FILE_ROOT = fallback_archived_root
            logger.warning(f"No permission for /data sharedfile dirs, fallback to {SHARED_FILE_ROOT} and {ARCHIVED_SHARED_FILE_ROOT}")
        self._lock = threading.Lock()
        self._last_create_at: Dict[str, float] = {}
        self.invalidation_retention_period = 14

    def set_invalidation_retention_period(self, days: int)-> None:
        self.invalidation_retention_period = days

    def _generate_token(self)-> str:
        for _ in range(64):
            token = secrets.token_urlsafe(18).replace("_", "-")
            if not (SHARED_FILE_ROOT / token).exists():
                return token
        raise RuntimeError("Failed to generate shared file token")

    def _token_is_archived(self, token: str)-> bool:
        if (ARCHIVED_SHARED_FILE_ROOT / f"{token}.tar.gz").exists():
            return True
        return any(ARCHIVED_SHARED_FILE_ROOT.glob(f"{token}-*.tar.gz"))

    def normalize_shared_files(self, shared_files: Any)-> Dict[str, str]:
        if shared_files is None:
            return {}
        if isinstance(shared_files, str):
            if not shared_files.strip():
                return {}
            try:
                shared_files = json.loads(shared_files)
            except Exception as error:
                raise SharedFileValidationError("shared_files must be a valid JSON mapping") from error
        if not isinstance(shared_files, dict):
            raise SharedFileValidationError("shared_files must be a mapping of mount_name -> token")

        normalized: Dict[str, str] = {}
        for raw_name, raw_token in shared_files.items():
            if not isinstance(raw_name, str):
                raise SharedFileValidationError("shared_files mount name must be a string")
            if not isinstance(raw_token, str):
                raise SharedFileValidationError(f"shared_files '{raw_name}': token must be a string")
            name = raw_name.strip()
            token = raw_token.strip()
            if not name:
                raise SharedFileValidationError("shared_files mount name cannot be empty")
            if not MOUNT_NAME_PATTERN.fullmatch(name):
                raise SharedFileValidationError(f"shared_files '{name}': invalid mount name. Allowed: letters, numbers, '_' and '-'" )
            if not token:
                raise SharedFileValidationError(f"shared_files '{name}': token cannot be empty")

            token_dir = SHARED_FILE_ROOT / token
            if not token_dir.exists() or not token_dir.is_dir():
                if self._token_is_archived(token):
                    raise SharedFileInvalidatedError(f"shared_files '{name}': token is invalidated and no longer mountable")
                raise SharedFileNotFoundError(f"shared_files '{name}': token not found")
            if not (token_dir / PROPERTIES_FILENAME).exists():
                raise SharedFileValidationError(f"shared_files '{name}': missing properties.json for token")

            normalized[name] = token
        return normalized

    def build_mount_specs(self, shared_files: Any)-> List[Tuple[str, Path, Path]]:
        normalized = self.normalize_shared_files(shared_files)
        return [(name, SHARED_FILE_ROOT / token, SHARED_FILE_ROOT / token / PROPERTIES_FILENAME) for name, token in normalized.items()]

    def create_shared_folder(self, user_id: str, expire_days: int, expected_size_gb: int)-> Dict[str, Any]:
        if expire_days < 7 or expire_days > 90:
            raise SharedFileValidationError("expire_days must be between 7 and 90")
        if expected_size_gb < 1 or expected_size_gb > 800:
            raise SharedFileValidationError("expected_size_gb must be between 1 and 800")

        now_ts = time.time()
        with self._lock:
            last_ts = self._last_create_at.get(user_id)
            if last_ts is not None and now_ts - last_ts < 60:
                wait_seconds = int(60 - (now_ts - last_ts))
                if 60 - (now_ts - last_ts) > wait_seconds:
                    wait_seconds += 1
                raise SharedFileRateLimitError(wait_seconds)
            token = self._generate_token()
            self._last_create_at[user_id] = now_ts

        token_dir = SHARED_FILE_ROOT / token
        token_dir.mkdir(parents=True, exist_ok=False)

        created_at = _utc_now()
        expire_at = created_at + timedelta(days=expire_days)
        properties = {
            "token": token,
            "created_by": user_id,
            "created_at": created_at.isoformat(),
            "expire_at": expire_at.isoformat(),
            "expire_days": expire_days,
            "expected_size_gb": expected_size_gb,
            "expected_size_bytes": expected_size_gb * 1024 * 1024 * 1024,
        }
        with open(token_dir / PROPERTIES_FILENAME, "w", encoding="utf-8") as f:
            json.dump(properties, f, indent=2, ensure_ascii=True)
        return properties

    def get_shared_folder_info(self, token: str)-> Dict[str, Any]:
        """获取共享文件夹信息"""
        token_dir = SHARED_FILE_ROOT / token
        if token_dir.exists() and token_dir.is_dir():
            properties_path = token_dir / PROPERTIES_FILENAME
            if properties_path.exists():
                with open(properties_path, "r", encoding="utf-8") as f:
                    props = json.load(f)
                props["status"] = "active"
                props["actual_size_bytes"] = _get_dir_size(token_dir)
                return props
        
        # 检查是否已归档
        if self._token_is_archived(token):
            # 查找归档文件
            archive_path = ARCHIVED_SHARED_FILE_ROOT / f"{token}.tar.gz"
            if not archive_path.exists():
                for p in ARCHIVED_SHARED_FILE_ROOT.glob(f"{token}-*.tar.gz"):
                    archive_path = p
                    break
            if archive_path.exists():
                return {
                    "token": token,
                    "status": "archived",
                    "archive_path": str(archive_path),
                }
        
        raise SharedFileNotFoundError(f"Token '{token}' not found")

    def list_files(self, token: str, subpath: str = "")-> List[Dict[str, Any]]:
        """列出共享文件夹中的文件"""
        token_dir = SHARED_FILE_ROOT / token
        if not token_dir.exists() or not token_dir.is_dir():
            if self._token_is_archived(token):
                raise SharedFileInvalidatedError("Token is archived")
            raise SharedFileNotFoundError(f"Token '{token}' not found")
        
        target_dir = token_dir / subpath if subpath else token_dir
        if not target_dir.exists() or not target_dir.is_dir():
            raise SharedFileValidationError(f"Path '{subpath}' not found or not a directory")
        
        # 安全检查：确保路径在 token_dir 内
        try:
            target_dir.resolve().relative_to(token_dir.resolve())
        except ValueError:
            raise SharedFileValidationError("Invalid path")
        
        files = []
        for item in sorted(target_dir.iterdir()):
            # 跳过隐藏文件（包括 .properties）
            if item.name.startswith("."):
                continue
            rel_path = str(item.relative_to(token_dir))
            if item.is_dir():
                files.append({
                    "name": item.name,
                    "path": rel_path,
                    "type": "directory",
                })
            else:
                files.append({
                    "name": item.name,
                    "path": rel_path,
                    "type": "file",
                    "size": item.stat().st_size,
                })
        return files

    def get_file_path(self, token: str, file_path: str)-> Path:
        """获取文件路径用于下载"""
        token_dir = SHARED_FILE_ROOT / token
        if not token_dir.exists() or not token_dir.is_dir():
            if self._token_is_archived(token):
                raise SharedFileInvalidatedError("Token is archived")
            raise SharedFileNotFoundError(f"Token '{token}' not found")
        
        target = token_dir / file_path
        if not target.exists():
            raise SharedFileNotFoundError(f"File '{file_path}' not found")
        
        # 安全检查
        try:
            target.resolve().relative_to(token_dir.resolve())
        except ValueError:
            raise SharedFileValidationError("Invalid path")
        
        return target

    def update_properties(
        self,
        token: str,
        user_id: str,
        is_admin: bool,
        expected_size_gb: Optional[int] = None,
        extend_days: Optional[int] = None,
    )-> Dict[str, Any]:
        """更新共享文件夹属性（仅创建者或管理员）"""
        token_dir = SHARED_FILE_ROOT / token
        if not token_dir.exists() or not token_dir.is_dir():
            raise SharedFileNotFoundError(f"Token '{token}' not found")
        
        properties_path = token_dir / PROPERTIES_FILENAME
        if not properties_path.exists():
            raise SharedFileValidationError("Properties file not found")
        
        with open(properties_path, "r", encoding="utf-8") as f:
            props = json.load(f)
        
        # 权限检查
        if props.get("created_by") != user_id and not is_admin:
            raise SharedFileValidationError("Only creator or admin can update properties")
        
        now = _utc_now()
        
        if expected_size_gb is not None:
            if expected_size_gb < 1 or expected_size_gb > 800:
                raise SharedFileValidationError("expected_size_gb must be between 1 and 800")
            props["expected_size_gb"] = expected_size_gb
            props["expected_size_bytes"] = expected_size_gb * 1024 * 1024 * 1024
            props["updated_at"] = now.isoformat()
        
        if extend_days is not None:
            if extend_days < 1 or extend_days > 90:
                raise SharedFileValidationError("extend_days must be between 1 and 90")
            current_expire = _parse_iso_datetime(props["expire_at"])
            new_expire = current_expire + timedelta(days=extend_days)
            props["expire_at"] = new_expire.isoformat()
            props["expire_days"] = (new_expire - _parse_iso_datetime(props["created_at"])).days
            props["updated_at"] = now.isoformat()
        
        with open(properties_path, "w", encoding="utf-8") as f:
            json.dump(props, f, indent=2, ensure_ascii=True)
        
        props["status"] = "active"
        props["actual_size_bytes"] = _get_dir_size(token_dir)
        return props

    def restore_archived(self, token: str, user_id: str, is_admin: bool, new_expire_days: Optional[int] = None)-> Dict[str, Any]:
        """从归档恢复共享文件夹（仅创建者或管理员）"""
        # 检查是否已存在活跃版本
        token_dir = SHARED_FILE_ROOT / token
        if token_dir.exists():
            raise SharedFileValidationError("Token already exists in active state")
        
        # 查找归档文件
        archive_path = ARCHIVED_SHARED_FILE_ROOT / f"{token}.tar.gz"
        if not archive_path.exists():
            for p in ARCHIVED_SHARED_FILE_ROOT.glob(f"{token}-*.tar.gz"):
                archive_path = p
                break
        
        if not archive_path.exists():
            raise SharedFileNotFoundError(f"Archived token '{token}' not found")
        
        # 先解压到临时位置，读取 properties 检查权限
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            with tarfile.open(archive_path, "r:gz") as tar:
                tar.extractall(tmpdir)
            
            extracted_dir = Path(tmpdir) / token
            props_path = extracted_dir / PROPERTIES_FILENAME
            if not props_path.exists():
                raise SharedFileValidationError("Properties file not found in archive")
            
            with open(props_path, "r", encoding="utf-8") as f:
                props = json.load(f)
            
            # 权限检查
            if props.get("created_by") != user_id and not is_admin:
                raise SharedFileValidationError("Only creator or admin can restore archived folder")
            
            # 更新过期时间
            now = _utc_now()
            if new_expire_days is not None:
                if new_expire_days < 7 or new_expire_days > 90:
                    raise SharedFileValidationError("new_expire_days must be between 7 and 90")
                props["expire_at"] = (now + timedelta(days=new_expire_days)).isoformat()
                props["expire_days"] = new_expire_days
            else:
                # 默认延长 7 天
                props["expire_at"] = (now + timedelta(days=7)).isoformat()
                props["expire_days"] = 7
            
            props["restored_at"] = now.isoformat()
            
            with open(props_path, "w", encoding="utf-8") as f:
                json.dump(props, f, indent=2, ensure_ascii=True)
            
            # 移动到活跃目录
            shutil.move(str(extracted_dir), str(token_dir))
        
        # 删除归档文件
        archive_path.unlink(missing_ok=True)
        
        props["status"] = "active"
        props["actual_size_bytes"] = _get_dir_size(token_dir)
        return props

    def _active_shared_tokens(self)-> Set[str]:
        active_statuses = {
            JobStatus.PREPARING,
            JobStatus.PENDING,
            JobStatus.QUEUED,
            JobStatus.RUNNING,
        }
        with SessionLocal() as db:
            jobs = db.query(Job).filter(Job.status.in_(active_statuses)).all()
        tokens: Set[str] = set()
        for job in jobs:
            if not job.shared_files:
                continue
            try:
                payload = json.loads(job.shared_files)
            except Exception:
                continue
            if isinstance(payload, dict):
                for token in payload.values():
                    if isinstance(token, str) and token:
                        tokens.add(token)
        return tokens

    def _archive_token_dir(self, token_dir: Path)-> None:
        token = token_dir.name
        archive_path = ARCHIVED_SHARED_FILE_ROOT / f"{token}.tar.gz"
        if archive_path.exists():
            archive_path = ARCHIVED_SHARED_FILE_ROOT / f"{token}-{int(time.time())}.tar.gz"
        with tarfile.open(archive_path, "w:gz") as tar:
            tar.add(token_dir, arcname=token)
        shutil.rmtree(token_dir, ignore_errors=True)

    def _cleanup_once(self)-> None:
        active_tokens = self._active_shared_tokens()
        now = _utc_now()
        for child in SHARED_FILE_ROOT.iterdir():
            if not child.is_dir():
                continue
            if child.name in active_tokens:
                continue
            properties_path = child / PROPERTIES_FILENAME
            if not properties_path.exists():
                continue
            try:
                with open(properties_path, "r", encoding="utf-8") as f:
                    props = json.load(f)
            except Exception:
                continue
            expire_at_raw = props.get("expire_at")
            expected_size_bytes = props.get("expected_size_bytes")
            if not isinstance(expire_at_raw, str) or not isinstance(expected_size_bytes, int):
                continue
            try:
                expire_at = _parse_iso_datetime(expire_at_raw)
            except Exception:
                continue
            if _get_dir_size(child) > expected_size_bytes or now >= expire_at:
                self._archive_token_dir(child)

        retention_seconds = (self.invalidation_retention_period + 1) * 24 * 3600
        cutoff = time.time() - retention_seconds
        for archived in ARCHIVED_SHARED_FILE_ROOT.glob("*.tar.gz"):
            try:
                if archived.stat().st_mtime < cutoff:
                    archived.unlink(missing_ok=True)
            except Exception:
                pass

    async def cleanup_loop(self)-> None:
        while True:
            await asyncio.sleep(60)
            try:
                await asyncio.to_thread(self._cleanup_once)
            except Exception as error:
                logger.error(f"Shared file cleanup failed: {error}")


shared_file_manager = SharedFileManager()
