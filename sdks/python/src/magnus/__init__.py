# sdks/python/src/magnus/__init__.py
from importlib.metadata import version as _pkg_version
from typing import Optional, Dict, Any, Union, Literal, List
from enum import Enum

from .exceptions import MagnusError, AuthenticationError, ResourceNotFoundError, ExecutionError
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
    "get_job_action",
    "get_job_logs",
    "terminate_job",
    "terminate_job_async",
    "get_cluster_stats",
    "list_blueprints",
    "list_services",
    "get_blueprint_schema",
    "download_file",
    "download_file_async",
    "custody_file",
    "custody_file_async",
    "save_site",
    "remove_site",
    "set_current_site",
    "MagnusError",
    "AuthenticationError",
    "ResourceNotFoundError",
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

def submit_job(task_name: str, entry_command: str, repo_name: str, branch: Optional[str] = None, commit_sha: Optional[str] = None, gpu_type: str = "cpu", gpu_count: int = 0, namespace: str = "Rise-AGI", job_type: str = "A2", description: Optional[str] = None, container_image: Optional[str] = None, cpu_count: Optional[int] = None, memory_demand: Optional[str] = None, ephemeral_storage: Optional[str] = None, runner: Optional[str] = None, system_entry_command: Optional[str] = None, timeout: float = 10.0) -> str:
    return default_client.submit_job(task_name, entry_command, repo_name, branch, commit_sha, gpu_type, gpu_count, namespace, job_type, description, container_image, cpu_count, memory_demand, ephemeral_storage, runner, system_entry_command, timeout)

async def submit_job_async(task_name: str, entry_command: str, repo_name: str, branch: Optional[str] = None, commit_sha: Optional[str] = None, gpu_type: str = "cpu", gpu_count: int = 0, namespace: str = "Rise-AGI", job_type: str = "A2", description: Optional[str] = None, container_image: Optional[str] = None, cpu_count: Optional[int] = None, memory_demand: Optional[str] = None, ephemeral_storage: Optional[str] = None, runner: Optional[str] = None, system_entry_command: Optional[str] = None, timeout: float = 10.0) -> str:
    return await default_client.submit_job_async(task_name, entry_command, repo_name, branch, commit_sha, gpu_type, gpu_count, namespace, job_type, description, container_image, cpu_count, memory_demand, ephemeral_storage, runner, system_entry_command, timeout)

def execute_job(task_name: str, entry_command: str, repo_name: str, branch: Optional[str] = None, commit_sha: Optional[str] = None, gpu_type: str = "cpu", gpu_count: int = 0, namespace: str = "Rise-AGI", job_type: str = "A2", description: Optional[str] = None, container_image: Optional[str] = None, cpu_count: Optional[int] = None, memory_demand: Optional[str] = None, ephemeral_storage: Optional[str] = None, runner: Optional[str] = None, system_entry_command: Optional[str] = None, timeout: Optional[float] = None, poll_interval: float = 2.0, execute_action: bool = True) -> Optional[str]:
    return default_client.execute_job(task_name, entry_command, repo_name, branch, commit_sha, gpu_type, gpu_count, namespace, job_type, description, container_image, cpu_count, memory_demand, ephemeral_storage, runner, system_entry_command, timeout, poll_interval, execute_action)

async def execute_job_async(task_name: str, entry_command: str, repo_name: str, branch: Optional[str] = None, commit_sha: Optional[str] = None, gpu_type: str = "cpu", gpu_count: int = 0, namespace: str = "Rise-AGI", job_type: str = "A2", description: Optional[str] = None, container_image: Optional[str] = None, cpu_count: Optional[int] = None, memory_demand: Optional[str] = None, ephemeral_storage: Optional[str] = None, runner: Optional[str] = None, system_entry_command: Optional[str] = None, timeout: Optional[float] = None, poll_interval: float = 2.0, execute_action: bool = True) -> Optional[str]:
    return await default_client.execute_job_async(task_name, entry_command, repo_name, branch, commit_sha, gpu_type, gpu_count, namespace, job_type, description, container_image, cpu_count, memory_demand, ephemeral_storage, runner, system_entry_command, timeout, poll_interval, execute_action)

def call_service(service_id: str, payload: Union[Dict[str, Any], str, bytes, list], endpoint: Optional[str] = None, timeout: float = 60.0, protocol: Literal["http", "mcp"] = "http", **kwargs: Any) -> Any:
    return default_client.call_service(service_id, payload, endpoint, timeout, protocol, **kwargs)

async def call_service_async(service_id: str, payload: Union[Dict[str, Any], str, bytes, list], endpoint: Optional[str] = None, timeout: float = 60.0, protocol: Literal["http", "mcp"] = "http", **kwargs: Any) -> Any:
    return await default_client.call_service_async(service_id, payload, endpoint, timeout, protocol, **kwargs)

def custody_file(path: str, expire_minutes: int = 60, max_downloads: Optional[int] = None, timeout: float = 300.0) -> str:
    return default_client.custody_file(path, expire_minutes, max_downloads, timeout)

async def custody_file_async(path: str, expire_minutes: int = 60, max_downloads: Optional[int] = None, timeout: float = 300.0) -> str:
    return await default_client.custody_file_async(path, expire_minutes, max_downloads, timeout)

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

def get_job_action(job_id: str, timeout: float = 10.0) -> Optional[str]:
    return default_client.get_job_action(job_id, timeout)

def terminate_job(job_id: str, timeout: float = 10.0) -> Dict[str, Any]:
    return default_client.terminate_job(job_id, timeout)

async def terminate_job_async(job_id: str, timeout: float = 10.0) -> Dict[str, Any]:
    return await default_client.terminate_job_async(job_id, timeout)

def get_job_logs(job_id: str, page: int = -1, timeout: float = 10.0) -> Dict[str, Any]:
    return default_client.get_job_logs(job_id, page, timeout)

def get_cluster_stats(timeout: float = 10.0) -> Dict[str, Any]:
    return default_client.get_cluster_stats(timeout)

def list_blueprints(limit: int = 20, skip: int = 0, search: Optional[str] = None, timeout: float = 10.0) -> Dict[str, Any]:
    return default_client.list_blueprints(limit, skip, search, timeout)

def list_services(limit: int = 20, skip: int = 0, search: Optional[str] = None, active_only: bool = False, timeout: float = 10.0) -> Dict[str, Any]:
    return default_client.list_services(limit, skip, search, active_only, timeout)

def get_blueprint_schema(blueprint_id: str, timeout: float = 10.0) -> List[Dict[str, Any]]:
    return default_client.get_blueprint_schema(blueprint_id, timeout)
