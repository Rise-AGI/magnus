# sdks/python/src/magnus/__init__.py
from importlib.metadata import version as _pkg_version
from typing import Optional, Dict, Any, Union, Literal, List
from enum import Enum

from .exceptions import (
    MagnusError, APIError,
    AuthenticationError, ForbiddenError, ResourceNotFoundError, ConflictError,
    ExecutionError,
)
from .config import save_site, remove_site, set_current_site
from .client import MagnusClient
from .http_download import download_file, download_file_async


class JobType(str, Enum):
    A1 = "A1"
    A2 = "A2"
    B1 = "B1"
    B2 = "B2"


class FileSecret(str):
    """文件传输凭证，SDK 端用于标记 file_secret 参数"""
    MAGIC_PREFIX = "magnus-secret:"

    def __new__(cls, value: str) -> "FileSecret":
        if value.startswith(cls.MAGIC_PREFIX):
            token = value[len(cls.MAGIC_PREFIX):]
        else:
            token = value
        cls._validate_token(token)
        return super().__new__(cls, cls.MAGIC_PREFIX + token)

    @staticmethod
    def _validate_token(token: str) -> None:
        parts = token.split("-")
        if len(parts) != 4:
            raise ValueError(f"FileSecret token must have 4 parts (prime-word-word-word), got {len(parts)}: '{token}'")
        num_str, w1, w2, w3 = parts
        if not num_str.isdigit() or not (1000 <= int(num_str) <= 99999):
            raise ValueError(f"FileSecret prime part must be a 4-5 digit number, got '{num_str}'")
        n = int(num_str)
        if n < 2 or any(n % i == 0 for i in range(2, int(n**0.5) + 1)):
            raise ValueError(f"FileSecret prime part must be a prime number, got {n}")
        for w in (w1, w2, w3):
            if not w.isalpha() or not w.islower() or not (4 <= len(w) <= 5):
                raise ValueError(f"FileSecret word must be 4-5 lowercase letters, got '{w}'")

__version__ = _pkg_version("magnus-sdk")

__all__ = [
    "MagnusClient",
    "JobType",
    "FileSecret",
    "configure",
    "launch_blueprint",
    "launch_blueprint_async",
    "run_blueprint",
    "run_blueprint_async",
    "submit_job",
    "submit_job_async",
    "execute_job",
    "execute_job_async",
    "call_service",
    "call_service_async",
    "list_jobs",
    "list_jobs_async",
    "get_job",
    "get_job_async",
    "get_job_result",
    "get_job_result_async",
    "get_job_action",
    "get_job_action_async",
    "get_job_logs",
    "get_job_logs_async",
    "terminate_job",
    "terminate_job_async",
    "get_cluster_stats",
    "get_cluster_stats_async",
    "list_blueprints",
    "list_blueprints_async",
    "list_services",
    "list_services_async",
    "get_blueprint",
    "get_blueprint_async",
    "save_blueprint",
    "save_blueprint_async",
    "delete_blueprint",
    "delete_blueprint_async",
    "get_blueprint_schema",
    "get_blueprint_schema_async",
    "list_skills",
    "list_skills_async",
    "get_skill",
    "get_skill_async",
    "save_skill",
    "save_skill_async",
    "delete_skill",
    "delete_skill_async",
    "list_images",
    "list_images_async",
    "pull_image",
    "pull_image_async",
    "refresh_image",
    "refresh_image_async",
    "remove_image",
    "remove_image_async",
    "download_file",
    "download_file_async",
    "custody_file",
    "custody_file_async",
    "create_shared_folder",
    "create_shared_folder_async",
    "get_shared_folder_info",
    "get_shared_folder_info_async",
    "list_shared_files",
    "list_shared_files_async",
    "download_shared_file",
    "download_shared_file_async",
    "update_shared_folder",
    "update_shared_folder_async",
    "restore_shared_folder",
    "restore_shared_folder_async",
    "save_site",
    "remove_site",
    "set_current_site",
    "MagnusError",
    "APIError",
    "AuthenticationError",
    "ForbiddenError",
    "ResourceNotFoundError",
    "ConflictError",
    "ExecutionError",
]


# === Functional Interface ===

default_client = MagnusClient()


def configure(token: Optional[str] = None, address: Optional[str] = None) -> None:
    global default_client
    default_client = MagnusClient(token=token, address=address)


def launch_blueprint(blueprint_id: str, args: Optional[Dict[str, Any]] = None, use_preference: bool = False, save_preference: bool = True, expire_minutes: int = 60, max_downloads: Optional[int] = 1, timeout: float = 10.0) -> str:
    return default_client.launch_blueprint(blueprint_id, args, use_preference, save_preference, expire_minutes, max_downloads, timeout)

async def launch_blueprint_async(blueprint_id: str, args: Optional[Dict[str, Any]] = None, use_preference: bool = False, save_preference: bool = True, expire_minutes: int = 60, max_downloads: Optional[int] = 1, timeout: float = 10.0) -> str:
    return await default_client.launch_blueprint_async(blueprint_id, args, use_preference, save_preference, expire_minutes, max_downloads, timeout)

def run_blueprint(blueprint_id: str, args: Optional[Dict[str, Any]] = None, use_preference: bool = False, save_preference: bool = True, expire_minutes: int = 60, max_downloads: Optional[int] = 1, timeout: Optional[float] = None, poll_interval: float = 2.0, execute_action: bool = True) -> Optional[str]:
    return default_client.run_blueprint(blueprint_id, args, use_preference, save_preference, expire_minutes, max_downloads, timeout, poll_interval, execute_action)

async def run_blueprint_async(blueprint_id: str, args: Optional[Dict[str, Any]] = None, use_preference: bool = False, save_preference: bool = True, expire_minutes: int = 60, max_downloads: Optional[int] = 1, timeout: Optional[float] = None, poll_interval: float = 2.0, execute_action: bool = True) -> Optional[str]:
    return await default_client.run_blueprint_async(blueprint_id, args, use_preference, save_preference, expire_minutes, max_downloads, timeout, poll_interval, execute_action)

def submit_job(task_name: str, entry_command: str, repo_name: str, branch: Optional[str] = None, commit_sha: Optional[str] = None, gpu_type: str = "cpu", gpu_count: int = 0, namespace: str = "Rise-AGI", job_type: str = "A2", description: Optional[str] = None, container_image: Optional[str] = None, cpu_count: Optional[int] = None, memory_demand: Optional[str] = None, ephemeral_storage: Optional[str] = None, runner: Optional[str] = None, system_entry_command: Optional[str] = None, shared_files: Optional[Dict[str, str]] = None, timeout: float = 10.0) -> str:
    return default_client.submit_job(task_name=task_name, entry_command=entry_command, repo_name=repo_name, branch=branch, commit_sha=commit_sha, gpu_type=gpu_type, gpu_count=gpu_count, namespace=namespace, job_type=job_type, description=description, container_image=container_image, cpu_count=cpu_count, memory_demand=memory_demand, ephemeral_storage=ephemeral_storage, runner=runner, system_entry_command=system_entry_command, shared_files=shared_files, timeout=timeout)

async def submit_job_async(task_name: str, entry_command: str, repo_name: str, branch: Optional[str] = None, commit_sha: Optional[str] = None, gpu_type: str = "cpu", gpu_count: int = 0, namespace: str = "Rise-AGI", job_type: str = "A2", description: Optional[str] = None, container_image: Optional[str] = None, cpu_count: Optional[int] = None, memory_demand: Optional[str] = None, ephemeral_storage: Optional[str] = None, runner: Optional[str] = None, system_entry_command: Optional[str] = None, shared_files: Optional[Dict[str, str]] = None, timeout: float = 10.0) -> str:
    return await default_client.submit_job_async(task_name=task_name, entry_command=entry_command, repo_name=repo_name, branch=branch, commit_sha=commit_sha, gpu_type=gpu_type, gpu_count=gpu_count, namespace=namespace, job_type=job_type, description=description, container_image=container_image, cpu_count=cpu_count, memory_demand=memory_demand, ephemeral_storage=ephemeral_storage, runner=runner, system_entry_command=system_entry_command, shared_files=shared_files, timeout=timeout)

def execute_job(task_name: str, entry_command: str, repo_name: str, branch: Optional[str] = None, commit_sha: Optional[str] = None, gpu_type: str = "cpu", gpu_count: int = 0, namespace: str = "Rise-AGI", job_type: str = "A2", description: Optional[str] = None, container_image: Optional[str] = None, cpu_count: Optional[int] = None, memory_demand: Optional[str] = None, ephemeral_storage: Optional[str] = None, runner: Optional[str] = None, system_entry_command: Optional[str] = None, shared_files: Optional[Dict[str, str]] = None, timeout: Optional[float] = None, poll_interval: float = 2.0, execute_action: bool = True) -> Optional[str]:
    return default_client.execute_job(task_name=task_name, entry_command=entry_command, repo_name=repo_name, branch=branch, commit_sha=commit_sha, gpu_type=gpu_type, gpu_count=gpu_count, namespace=namespace, job_type=job_type, description=description, container_image=container_image, cpu_count=cpu_count, memory_demand=memory_demand, ephemeral_storage=ephemeral_storage, runner=runner, system_entry_command=system_entry_command, shared_files=shared_files, timeout=timeout, poll_interval=poll_interval, execute_action=execute_action)

async def execute_job_async(task_name: str, entry_command: str, repo_name: str, branch: Optional[str] = None, commit_sha: Optional[str] = None, gpu_type: str = "cpu", gpu_count: int = 0, namespace: str = "Rise-AGI", job_type: str = "A2", description: Optional[str] = None, container_image: Optional[str] = None, cpu_count: Optional[int] = None, memory_demand: Optional[str] = None, ephemeral_storage: Optional[str] = None, runner: Optional[str] = None, system_entry_command: Optional[str] = None, shared_files: Optional[Dict[str, str]] = None, timeout: Optional[float] = None, poll_interval: float = 2.0, execute_action: bool = True) -> Optional[str]:
    return await default_client.execute_job_async(task_name=task_name, entry_command=entry_command, repo_name=repo_name, branch=branch, commit_sha=commit_sha, gpu_type=gpu_type, gpu_count=gpu_count, namespace=namespace, job_type=job_type, description=description, container_image=container_image, cpu_count=cpu_count, memory_demand=memory_demand, ephemeral_storage=ephemeral_storage, runner=runner, system_entry_command=system_entry_command, shared_files=shared_files, timeout=timeout, poll_interval=poll_interval, execute_action=execute_action)

def call_service(service_id: str, payload: Union[Dict[str, Any], str, bytes, list], endpoint: Optional[str] = None, timeout: float = 60.0, protocol: Literal["http", "mcp"] = "http", **kwargs: Any) -> Any:
    return default_client.call_service(service_id, payload, endpoint, timeout, protocol, **kwargs)

async def call_service_async(service_id: str, payload: Union[Dict[str, Any], str, bytes, list], endpoint: Optional[str] = None, timeout: float = 60.0, protocol: Literal["http", "mcp"] = "http", **kwargs: Any) -> Any:
    return await default_client.call_service_async(service_id, payload, endpoint, timeout, protocol, **kwargs)

def custody_file(path: str, expire_minutes: int = 60, max_downloads: Optional[int] = None, timeout: float = 300.0) -> str:
    return default_client.custody_file(path, expire_minutes, max_downloads, timeout)

async def custody_file_async(path: str, expire_minutes: int = 60, max_downloads: Optional[int] = None, timeout: float = 300.0) -> str:
    return await default_client.custody_file_async(path, expire_minutes, max_downloads, timeout)

def create_shared_folder(expire_days: int, expected_size_gb: int, timeout: float = 10.0) -> Dict[str, Any]:
    return default_client.create_shared_folder(expire_days, expected_size_gb, timeout)

async def create_shared_folder_async(expire_days: int, expected_size_gb: int, timeout: float = 10.0) -> Dict[str, Any]:
    return await default_client.create_shared_folder_async(expire_days, expected_size_gb, timeout)

def get_shared_folder_info(token: str, timeout: float = 10.0) -> Dict[str, Any]:
    return default_client.get_shared_folder_info(token, timeout)

async def get_shared_folder_info_async(token: str, timeout: float = 10.0) -> Dict[str, Any]:
    return await default_client.get_shared_folder_info_async(token, timeout)

def list_shared_files(token: str, path: str = "", timeout: float = 10.0) -> List[Dict[str, Any]]:
    return default_client.list_shared_files(token, path, timeout)

async def list_shared_files_async(token: str, path: str = "", timeout: float = 10.0) -> List[Dict[str, Any]]:
    return await default_client.list_shared_files_async(token, path, timeout)

def download_shared_file(token: str, file_path: str, dest = None, timeout: float = 60.0):
    return default_client.download_shared_file(token, file_path, dest, timeout)

async def download_shared_file_async(token: str, file_path: str, dest = None, timeout: float = 60.0):
    return await default_client.download_shared_file_async(token, file_path, dest, timeout)

def update_shared_folder(token: str, expected_size_gb: Optional[int] = None, extend_days: Optional[int] = None, timeout: float = 10.0) -> Dict[str, Any]:
    return default_client.update_shared_folder(token, expected_size_gb, extend_days, timeout)

async def update_shared_folder_async(token: str, expected_size_gb: Optional[int] = None, extend_days: Optional[int] = None, timeout: float = 10.0) -> Dict[str, Any]:
    return await default_client.update_shared_folder_async(token, expected_size_gb, extend_days, timeout)

def restore_shared_folder(token: str, new_expire_days: Optional[int] = None, timeout: float = 10.0) -> Dict[str, Any]:
    return default_client.restore_shared_folder(token, new_expire_days, timeout)

async def restore_shared_folder_async(token: str, new_expire_days: Optional[int] = None, timeout: float = 10.0) -> Dict[str, Any]:
    return await default_client.restore_shared_folder_async(token, new_expire_days, timeout)

def list_jobs(limit: int = 20, skip: int = 0, search: Optional[str] = None, timeout: float = 10.0) -> Dict[str, Any]:
    return default_client.list_jobs(limit, skip, search, timeout)

async def list_jobs_async(limit: int = 20, skip: int = 0, search: Optional[str] = None, timeout: float = 10.0) -> Dict[str, Any]:
    return await default_client.list_jobs_async(limit, skip, search, timeout)

def get_job(job_id: str, timeout: float = 10.0) -> Dict[str, Any]:
    return default_client.get_job(job_id, timeout)

async def get_job_async(job_id: str, timeout: float = 10.0) -> Dict[str, Any]:
    return await default_client.get_job_async(job_id, timeout)

def get_job_result(job_id: str, timeout: float = 10.0) -> Optional[str]:
    return default_client.get_job_result(job_id, timeout)

async def get_job_result_async(job_id: str, timeout: float = 10.0) -> Optional[str]:
    return await default_client.get_job_result_async(job_id, timeout)

def get_job_action(job_id: str, timeout: float = 10.0) -> Optional[str]:
    return default_client.get_job_action(job_id, timeout)

async def get_job_action_async(job_id: str, timeout: float = 10.0) -> Optional[str]:
    return await default_client.get_job_action_async(job_id, timeout)

def terminate_job(job_id: str, timeout: float = 10.0) -> Dict[str, Any]:
    return default_client.terminate_job(job_id, timeout)

async def terminate_job_async(job_id: str, timeout: float = 10.0) -> Dict[str, Any]:
    return await default_client.terminate_job_async(job_id, timeout)

def get_job_logs(job_id: str, page: int = -1, timeout: float = 10.0) -> Dict[str, Any]:
    return default_client.get_job_logs(job_id, page, timeout)

async def get_job_logs_async(job_id: str, page: int = -1, timeout: float = 10.0) -> Dict[str, Any]:
    return await default_client.get_job_logs_async(job_id, page, timeout)

def get_cluster_stats(timeout: float = 10.0) -> Dict[str, Any]:
    return default_client.get_cluster_stats(timeout)

async def get_cluster_stats_async(timeout: float = 10.0) -> Dict[str, Any]:
    return await default_client.get_cluster_stats_async(timeout)

def list_blueprints(limit: int = 20, skip: int = 0, search: Optional[str] = None, timeout: float = 10.0) -> Dict[str, Any]:
    return default_client.list_blueprints(limit, skip, search, timeout)

async def list_blueprints_async(limit: int = 20, skip: int = 0, search: Optional[str] = None, timeout: float = 10.0) -> Dict[str, Any]:
    return await default_client.list_blueprints_async(limit, skip, search, timeout)

def get_blueprint(blueprint_id: str, timeout: float = 10.0) -> Dict[str, Any]:
    return default_client.get_blueprint(blueprint_id, timeout)

async def get_blueprint_async(blueprint_id: str, timeout: float = 10.0) -> Dict[str, Any]:
    return await default_client.get_blueprint_async(blueprint_id, timeout)

def save_blueprint(blueprint_id: str, title: str, description: str, code: str, timeout: float = 10.0) -> Dict[str, Any]:
    return default_client.save_blueprint(blueprint_id, title, description, code, timeout)

async def save_blueprint_async(blueprint_id: str, title: str, description: str, code: str, timeout: float = 10.0) -> Dict[str, Any]:
    return await default_client.save_blueprint_async(blueprint_id, title, description, code, timeout)

def delete_blueprint(blueprint_id: str, timeout: float = 10.0) -> None:
    default_client.delete_blueprint(blueprint_id, timeout)

async def delete_blueprint_async(blueprint_id: str, timeout: float = 10.0) -> None:
    await default_client.delete_blueprint_async(blueprint_id, timeout)

def list_services(limit: int = 20, skip: int = 0, search: Optional[str] = None, active_only: bool = False, timeout: float = 10.0) -> Dict[str, Any]:
    return default_client.list_services(limit, skip, search, active_only, timeout)

async def list_services_async(limit: int = 20, skip: int = 0, search: Optional[str] = None, active_only: bool = False, timeout: float = 10.0) -> Dict[str, Any]:
    return await default_client.list_services_async(limit, skip, search, active_only, timeout)

def get_blueprint_schema(blueprint_id: str, timeout: float = 10.0) -> List[Dict[str, Any]]:
    return default_client.get_blueprint_schema(blueprint_id, timeout)

async def get_blueprint_schema_async(blueprint_id: str, timeout: float = 10.0) -> List[Dict[str, Any]]:
    return await default_client.get_blueprint_schema_async(blueprint_id, timeout)

def list_skills(limit: int = 20, skip: int = 0, search: Optional[str] = None, timeout: float = 10.0) -> Dict[str, Any]:
    return default_client.list_skills(limit, skip, search, timeout)

async def list_skills_async(limit: int = 20, skip: int = 0, search: Optional[str] = None, timeout: float = 10.0) -> Dict[str, Any]:
    return await default_client.list_skills_async(limit, skip, search, timeout)

def get_skill(skill_id: str, timeout: float = 10.0) -> Dict[str, Any]:
    return default_client.get_skill(skill_id, timeout)

async def get_skill_async(skill_id: str, timeout: float = 10.0) -> Dict[str, Any]:
    return await default_client.get_skill_async(skill_id, timeout)

def save_skill(skill_id: str, title: str, description: str, files: List[Dict[str, str]], timeout: float = 10.0) -> Dict[str, Any]:
    return default_client.save_skill(skill_id, title, description, files, timeout)

async def save_skill_async(skill_id: str, title: str, description: str, files: List[Dict[str, str]], timeout: float = 10.0) -> Dict[str, Any]:
    return await default_client.save_skill_async(skill_id, title, description, files, timeout)

def delete_skill(skill_id: str, timeout: float = 10.0) -> None:
    default_client.delete_skill(skill_id, timeout)

async def delete_skill_async(skill_id: str, timeout: float = 10.0) -> None:
    await default_client.delete_skill_async(skill_id, timeout)

def list_images(search: Optional[str] = None, timeout: float = 10.0) -> Dict[str, Any]:
    return default_client.list_images(search, timeout)

async def list_images_async(search: Optional[str] = None, timeout: float = 10.0) -> Dict[str, Any]:
    return await default_client.list_images_async(search, timeout)

def pull_image(uri: str, timeout: float = 30.0) -> Dict[str, Any]:
    return default_client.pull_image(uri, timeout)

async def pull_image_async(uri: str, timeout: float = 30.0) -> Dict[str, Any]:
    return await default_client.pull_image_async(uri, timeout)

def refresh_image(image_id: int, timeout: float = 30.0) -> Dict[str, Any]:
    return default_client.refresh_image(image_id, timeout)

async def refresh_image_async(image_id: int, timeout: float = 30.0) -> Dict[str, Any]:
    return await default_client.refresh_image_async(image_id, timeout)

def remove_image(image_id: int, timeout: float = 10.0) -> None:
    default_client.remove_image(image_id, timeout)

async def remove_image_async(image_id: int, timeout: float = 10.0) -> None:
    await default_client.remove_image_async(image_id, timeout)
