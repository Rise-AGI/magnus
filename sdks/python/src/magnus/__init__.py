# sdks/python/src/magnus/__init__.py
import os
import time
import json
import logging
import asyncio
import httpx
from typing import Optional, Dict, Any, Union, Literal, List
from pathlib import Path
from importlib.metadata import version as _pkg_version

from .croc_tools import download_file, download_file_async
from .file_transfer import get_file_transfer_manager

__version__ = _pkg_version("magnus-sdk")


__all__ = [
    # Core Client
    "MagnusClient",
    "configure",

    # Functional API
    "submit_blueprint",
    "submit_blueprint_async",
    "run_blueprint",
    "run_blueprint_async",
    "call_service",
    "call_service_async",
    "list_jobs",
    "list_jobs_async",
    "get_job",
    "get_job_async",
    "get_job_logs",
    "terminate_job",
    "terminate_job_async",
    "get_cluster_stats",
    "list_blueprints",
    "list_services",
    "get_blueprint_schema",

    # File Transfer
    "download_file",
    "download_file_async",
    "custody_file",
    "custody_file_async",

    # Configuration
    "save_config_file",

    # Exceptions
    "MagnusError",
    "AuthenticationError",
    "ResourceNotFoundError",
    "ExecutionError",
]


# === Configuration ===

DEFAULT_ADDRESS = "http://127.0.0.1:8017"
ENV_MAGNUS_TOKEN = "MAGNUS_TOKEN"
ENV_MAGNUS_ADDRESS = "MAGNUS_ADDRESS"
CONFIG_DIR = Path.home() / ".magnus"
CONFIG_FILE = CONFIG_DIR / "config.json"

logger = logging.getLogger("magnus")


def _load_config_file() -> Dict[str, str]:
    """Load ~/.magnus/config.json. Returns empty dict on any failure."""
    try:
        if CONFIG_FILE.is_file():
            return json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}


def save_config_file(address: str, token: str) -> Path:
    """Write address and token to ~/.magnus/config.json. Returns the file path."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(
        json.dumps({"address": address, "token": token}, indent=2) + "\n",
        encoding="utf-8",
    )
    return CONFIG_FILE

# === Exceptions ===

class MagnusError(Exception):
    pass

class AuthenticationError(MagnusError):
    pass

class ResourceNotFoundError(MagnusError):
    pass

class ExecutionError(MagnusError):
    pass

# === Core Client ===

class MagnusClient:
    """
    Magnus SDK 核心客户端。
    负责维护连接状态、认证以及底层的 HTTP 交互。
    """

    def __init__(self, token: Optional[str] = None, address: Optional[str] = None):
        # Priority: explicit param > env var > config file > default
        file_config = _load_config_file()
        self.token = token or os.getenv(ENV_MAGNUS_TOKEN) or file_config.get("token")

        raw_address = (
            address
            or os.getenv(ENV_MAGNUS_ADDRESS)
            or file_config.get("address")
            or DEFAULT_ADDRESS
        )
        self.address = raw_address.rstrip("/")
        if self.address.endswith("/api"):
            self.address = self.address[:-4]
        
        self._client: Optional[httpx.Client] = None
        self._async_client: Optional[httpx.AsyncClient] = None

    @property
    def api_base(self) -> str:
        return f"{self.address}/api"

    @property
    def http(self) -> httpx.Client:
        if self._client is None:
            self._validate_config()
            self._client = httpx.Client(
                base_url=self.api_base,
                headers={"Authorization": f"Bearer {self.token}"},
                timeout=30.0
            )
        return self._client

    @property
    def ahttp(self) -> httpx.AsyncClient:
        if self._async_client is None:
            self._validate_config()
            self._async_client = httpx.AsyncClient(
                base_url=self.api_base,
                headers={"Authorization": f"Bearer {self.token}"},
                timeout=30.0
            )
        return self._async_client

    def _validate_config(self):
        if not self.token:
            raise AuthenticationError(
                f"Magnus API Token is missing. Set {ENV_MAGNUS_TOKEN} or init with token."
            )

    def _handle_error(self, response: httpx.Response):
        if response.is_success:
            return

        try:
            detail = response.json().get("detail", response.text)
        except Exception:
            detail = response.text

        if response.status_code == 401:
            raise AuthenticationError(f"Authentication failed: {detail}")
        elif response.status_code == 404:
            raise ResourceNotFoundError(f"Resource not found: {detail}")
        else:
            raise MagnusError(f"API Error ({response.status_code}): {detail}")

    def _join_url(self, base: str, part: Optional[str]) -> str:
        if not part:
            return base.rstrip("/")
        return f"{base.rstrip('/')}/{part.lstrip('/')}"

    def _parse_mcp_sse_response(self, text: str) -> str:
        results = []
        for line in text.splitlines():
            if line.startswith("data: "):
                try:
                    data = json.loads(line[6:])
                    if "result" in data and "content" in data["result"]:
                        for item in data["result"]["content"]:
                            if item.get("type") == "text":
                                inner_text = item.get("text", "")
                                try:
                                    inner_json = json.loads(inner_text)
                                    if isinstance(inner_json, dict) and "content" in inner_json:
                                        results.append(str(inner_json["content"]))
                                    else:
                                        results.append(str(inner_text))
                                except json.JSONDecodeError:
                                    results.append(inner_text)
                except json.JSONDecodeError:
                    continue
        return "".join(results) if results else text

    # === Blueprint Methods ===

    def _get_file_secret_keys(self, blueprint_id: str) -> List[str]:
        """获取蓝图中所有 FileSecret 类型的参数 key"""
        try:
            schema = self.get_blueprint_schema(blueprint_id)
            return [param["key"] for param in schema if param.get("type") == "file_secret"]
        except Exception:
            return []

    def submit_blueprint(
        self,
        blueprint_id: str,
        args: Optional[Dict[str, Any]] = None,
        use_preference: bool = True,
        save_preference: bool = True,
        timeout: float = 10.0,
    ) -> str:
        """
        提交蓝图任务，立即返回 Job ID (Fire & Forget)。
        :param args: 传递给蓝图的参数字典。
        :param use_preference: 是否合并已缓存的偏好参数。
        :param save_preference: 成功后是否保存参数为新偏好。
        :param timeout: HTTP 请求超时时间（非任务执行时间）。

        注意：对于 FileSecret 类型的参数，可以直接传文件路径，SDK 会自动启动 croc send。
        croc 进程会在后台运行，直到远程接收完成或进程退出时自动清理。
        """

        final_args = dict(args) if args else {}

        file_secret_keys = self._get_file_secret_keys(blueprint_id)
        if file_secret_keys and final_args:
            file_transfer_mgr = get_file_transfer_manager()
            final_args, errors = file_transfer_mgr.prepare_file_secrets(final_args, file_secret_keys)
            if errors:
                raise MagnusError(f"Failed to prepare file transfers: {'; '.join(errors)}")

        payload = {
            "parameters": final_args,
            "use_preference": use_preference,
            "save_preference": save_preference,
        }

        try:
            resp = self.http.post(
                f"/blueprints/{blueprint_id}/run",
                json=payload,
                timeout=timeout
            )
            self._handle_error(resp)
            return resp.json()["job_id"]
        except httpx.TimeoutException:
            raise MagnusError("Request timed out while submitting blueprint.")

    async def submit_blueprint_async(
        self,
        blueprint_id: str,
        args: Optional[Dict[str, Any]] = None,
        use_preference: bool = True,
        save_preference: bool = True,
        timeout: float = 10.0,
    ) -> str:
        """
        异步提交蓝图任务，立即返回 Job ID (Fire & Forget)。

        注意：对于 FileSecret 类型的参数，可以直接传文件路径，SDK 会自动启动 croc send。
        croc 进程会在后台运行，直到远程接收完成或进程退出时自动清理。
        """

        final_args = dict(args) if args else {}

        file_secret_keys = self._get_file_secret_keys(blueprint_id)
        if file_secret_keys and final_args:
            file_transfer_mgr = get_file_transfer_manager()
            final_args, errors = file_transfer_mgr.prepare_file_secrets(final_args, file_secret_keys)
            if errors:
                raise MagnusError(f"Failed to prepare file transfers: {'; '.join(errors)}")

        payload = {
            "parameters": final_args,
            "use_preference": use_preference,
            "save_preference": save_preference,
        }

        try:
            resp = await self.ahttp.post(
                f"/blueprints/{blueprint_id}/run",
                json=payload,
                timeout=timeout
            )
            self._handle_error(resp)
            return resp.json()["job_id"]
        except httpx.TimeoutException:
            raise MagnusError("Request timed out while submitting blueprint.")

    def run_blueprint(
        self,
        blueprint_id: str,
        args: Optional[Dict[str, Any]] = None,
        use_preference: bool = True,
        save_preference: bool = True,
        timeout: Optional[float] = None,
        poll_interval: float = 2.0,
    ) -> Optional[str]:
        """
        提交任务并轮询等待完成 (Submit & Wait)。
        :param timeout: 最大任务执行等待时间（秒）。默认为 None（无限等待）。

        注意：对于 FileSecret 类型的参数，可以直接传文件路径，SDK 会自动启动 croc send。
        Job 完成后会自动清理 croc 进程。
        """

        file_transfer_mgr = get_file_transfer_manager()
        try:
            job_id = self.submit_blueprint(
                blueprint_id=blueprint_id,
                args=args,
                use_preference=use_preference,
                save_preference=save_preference,
                timeout=10.0  # Network timeout
            )
            logger.info(f"Job {job_id} submitted. Waiting for completion...")

            start_time = time.time()
            while True:
                if timeout and (time.time() - start_time > timeout):
                    raise TimeoutError(f"Job {job_id} timed out after {timeout}s")

                resp = self.http.get(f"/jobs/{job_id}")
                self._handle_error(resp)
                data = resp.json()
                status = data["status"]

                if status == "Success":
                    return data.get("result", "")
                elif status in ["Failed", "Terminated"]:
                    raise ExecutionError(f"Job {job_id} ended with status: {status}")

                time.sleep(poll_interval)
        finally:
            file_transfer_mgr.cleanup()

    async def run_blueprint_async(
        self,
        blueprint_id: str,
        args: Optional[Dict[str, Any]] = None,
        use_preference: bool = True,
        save_preference: bool = True,
        timeout: Optional[float] = None,
        poll_interval: float = 2.0,
    ) -> Optional[str]:
        """
        异步提交任务并轮询等待完成 (Submit & Wait)。

        注意：对于 FileSecret 类型的参数，可以直接传文件路径，SDK 会自动启动 croc send。
        Job 完成后会自动清理 croc 进程。
        """

        file_transfer_mgr = get_file_transfer_manager()
        try:
            job_id = await self.submit_blueprint_async(
                blueprint_id=blueprint_id,
                args=args,
                use_preference=use_preference,
                save_preference=save_preference,
                timeout=10.0
            )
            logger.info(f"Job {job_id} submitted. Waiting for completion...")

            start_time = time.time()
            while True:
                if timeout and (time.time() - start_time > timeout):
                    raise TimeoutError(f"Job {job_id} timed out after {timeout}s")

                resp = await self.ahttp.get(f"/jobs/{job_id}")
                self._handle_error(resp)
                data = resp.json()
                status = data["status"]

                if status == "Success":
                    return data.get("result", "")
                elif status in ["Failed", "Terminated"]:
                    raise ExecutionError(f"Job {job_id} ended with status: {status}")

                await asyncio.sleep(poll_interval)
        finally:
            file_transfer_mgr.cleanup()

    # === Job Management Methods ===

    def list_jobs(
        self,
        limit: int = 20,
        skip: int = 0,
        search: Optional[str] = None,
        timeout: float = 10.0,
    ) -> Dict[str, Any]:
        """
        列出当前用户的任务。
        返回 {"total": int, "items": List[JobInfo]}
        """
        params: Dict[str, Any] = {"limit": limit, "skip": skip}
        if search:
            params["search"] = search

        try:
            resp = self.http.get("/jobs", params=params, timeout=timeout)
            self._handle_error(resp)
            return resp.json()
        except httpx.TimeoutException:
            raise MagnusError("Request timed out while listing jobs.")


    async def list_jobs_async(
        self,
        limit: int = 20,
        skip: int = 0,
        search: Optional[str] = None,
        timeout: float = 10.0,
    ) -> Dict[str, Any]:
        params: Dict[str, Any] = {"limit": limit, "skip": skip}
        if search:
            params["search"] = search

        try:
            resp = await self.ahttp.get("/jobs", params=params, timeout=timeout)
            self._handle_error(resp)
            return resp.json()
        except httpx.TimeoutException:
            raise MagnusError("Request timed out while listing jobs.")


    def get_job(
        self,
        job_id: str,
        timeout: float = 10.0,
    ) -> Dict[str, Any]:
        """
        获取单个任务的详细信息。
        """
        try:
            resp = self.http.get(f"/jobs/{job_id}", timeout=timeout)
            self._handle_error(resp)
            return resp.json()
        except httpx.TimeoutException:
            raise MagnusError("Request timed out while getting job info.")


    async def get_job_async(
        self,
        job_id: str,
        timeout: float = 10.0,
    ) -> Dict[str, Any]:
        try:
            resp = await self.ahttp.get(f"/jobs/{job_id}", timeout=timeout)
            self._handle_error(resp)
            return resp.json()
        except httpx.TimeoutException:
            raise MagnusError("Request timed out while getting job info.")


    def terminate_job(
        self,
        job_id: str,
        timeout: float = 10.0,
    ) -> Dict[str, Any]:
        """
        终止指定任务。
        """
        try:
            resp = self.http.post(f"/jobs/{job_id}/terminate", timeout=timeout)
            self._handle_error(resp)
            return resp.json()
        except httpx.TimeoutException:
            raise MagnusError("Request timed out while terminating job.")


    async def terminate_job_async(
        self,
        job_id: str,
        timeout: float = 10.0,
    ) -> Dict[str, Any]:
        try:
            resp = await self.ahttp.post(f"/jobs/{job_id}/terminate", timeout=timeout)
            self._handle_error(resp)
            return resp.json()
        except httpx.TimeoutException:
            raise MagnusError("Request timed out while terminating job.")


    def get_job_logs(
        self,
        job_id: str,
        page: int = -1,
        timeout: float = 10.0,
    ) -> Dict[str, Any]:
        """
        获取任务日志。
        :param page: 页码，-1 为最新页
        :return: {"logs": str, "page": int, "total_pages": int}
        """
        try:
            resp = self.http.get(f"/jobs/{job_id}/logs", params={"page": page}, timeout=timeout)
            self._handle_error(resp)
            return resp.json()
        except httpx.TimeoutException:
            raise MagnusError("Request timed out while getting job logs.")


    def get_cluster_stats(
        self,
        timeout: float = 10.0,
    ) -> Dict[str, Any]:
        """
        获取集群状态。
        :return: {"resources": {...}, "running_jobs": [...], "pending_jobs": [...]}
        """
        try:
            resp = self.http.get("/cluster/stats", timeout=timeout)
            self._handle_error(resp)
            return resp.json()
        except httpx.TimeoutException:
            raise MagnusError("Request timed out while getting cluster stats.")


    def list_blueprints(
        self,
        limit: int = 20,
        skip: int = 0,
        search: Optional[str] = None,
        timeout: float = 10.0,
    ) -> Dict[str, Any]:
        """
        列出蓝图。
        :return: {"total": int, "items": List[BlueprintInfo]}
        """
        params: Dict[str, Any] = {"limit": limit, "skip": skip}
        if search:
            params["search"] = search

        try:
            resp = self.http.get("/blueprints", params=params, timeout=timeout)
            self._handle_error(resp)
            return resp.json()
        except httpx.TimeoutException:
            raise MagnusError("Request timed out while listing blueprints.")


    def get_blueprint_schema(
        self,
        blueprint_id: str,
        timeout: float = 10.0,
    ) -> List[Dict[str, Any]]:
        """
        获取蓝图的参数 Schema。
        :return: List[BlueprintParamSchema]
        """
        try:
            resp = self.http.get(f"/blueprints/{blueprint_id}/schema", timeout=timeout)
            self._handle_error(resp)
            return resp.json()
        except httpx.TimeoutException:
            raise MagnusError("Request timed out while getting blueprint schema.")


    def list_services(
        self,
        limit: int = 20,
        skip: int = 0,
        search: Optional[str] = None,
        active_only: bool = False,
        timeout: float = 10.0,
    ) -> Dict[str, Any]:
        """
        列出服务。
        :return: {"total": int, "items": List[ServiceInfo]}
        """
        params: Dict[str, Any] = {"limit": limit, "skip": skip}
        if search:
            params["search"] = search
        if active_only:
            params["active_only"] = True

        try:
            resp = self.http.get("/services", params=params, timeout=timeout)
            self._handle_error(resp)
            return resp.json()
        except httpx.TimeoutException:
            raise MagnusError("Request timed out while listing services.")


    # === Service Methods ===

    def custody_file(
        self,
        path: str,
        expire_minutes: int = 60,
        timeout: float = 300.0,
    ) -> str:
        """
        代管文件/文件夹，返回可供 download_file() 使用的 file_secret。
        内部流程：本地 croc send → 后端 croc receive → 后端 croc send → 返回新 secret。
        """
        file_transfer_mgr = get_file_transfer_manager()
        try:
            from .file_transfer import CrocSender
            sender = CrocSender(path=path)
            if not sender.start():
                raise MagnusError(f"Failed to start croc send: {sender.error}")

            resp = self.http.post(
                "/files/custody",
                json={"file_secret": sender.file_secret, "expire_minutes": expire_minutes},
                timeout=timeout,
            )
            self._handle_error(resp)
            return resp.json()["file_secret"]
        finally:
            file_transfer_mgr.cleanup()

    async def custody_file_async(
        self,
        path: str,
        expire_minutes: int = 60,
        timeout: float = 300.0,
    ) -> str:
        """异步版本的 custody_file。"""
        file_transfer_mgr = get_file_transfer_manager()
        try:
            from .file_transfer import CrocSender
            sender = CrocSender(path=path)
            if not sender.start():
                raise MagnusError(f"Failed to start croc send: {sender.error}")

            resp = await self.ahttp.post(
                "/files/custody",
                json={"file_secret": sender.file_secret, "expire_minutes": expire_minutes},
                timeout=timeout,
            )
            self._handle_error(resp)
            return resp.json()["file_secret"]
        finally:
            file_transfer_mgr.cleanup()

    def call_service(
        self,
        service_id: str,
        payload: Union[Dict[str, Any], str, bytes, list],
        endpoint: Optional[str] = None,
        timeout: float = 60.0,
        protocol: Literal["http", "mcp"] = "http",
        **kwargs: Any,
    ) -> Any:
        """
        调用托管服务 (RPC)。
        """
        base_url = f"/services/{service_id}"
        if protocol == "mcp" and endpoint is None:
            endpoint = "mcp"
            
        url = self._join_url(base_url, endpoint)
        
        headers = {}
        request_kwargs = {}

        if protocol == "mcp":
            tool_name = kwargs.get("tool_name")
            if not tool_name:
                raise ValueError("Protocol 'mcp' requires 'tool_name' in kwargs")
            
            headers["Accept"] = "application/json, text/event-stream"
            rpc_payload = {
                "jsonrpc": "2.0",
                "method": "tools/call",
                "params": {
                    "name": tool_name,
                    "arguments": payload if isinstance(payload, dict) else {"data": payload}
                },
                "id": 1
            }
            request_kwargs["json"] = rpc_payload
        else:
            if isinstance(payload, (dict, list)):
                request_kwargs["json"] = payload
            else:
                request_kwargs["content"] = payload

        resp = self.http.post(
            url, 
            timeout=timeout,
            headers=headers,
            **request_kwargs
        )
        self._handle_error(resp)

        if protocol == "mcp":
            return self._parse_mcp_sse_response(resp.text)

        content_type = resp.headers.get("content-type", "")
        if "application/json" in content_type:
            return resp.json()
        return resp.content

    async def call_service_async(
        self,
        service_id: str,
        payload: Union[Dict[str, Any], str, bytes, list],
        endpoint: Optional[str] = None,
        timeout: float = 60.0,
        protocol: Literal["http", "mcp"] = "http",
        **kwargs: Any,
    ) -> Any:
        base_url = f"/services/{service_id}"
        if protocol == "mcp" and endpoint is None:
            endpoint = "mcp"
            
        url = self._join_url(base_url, endpoint)
        
        headers = {}
        request_kwargs = {}

        if protocol == "mcp":
            tool_name = kwargs.get("tool_name")
            if not tool_name:
                raise ValueError("Protocol 'mcp' requires 'tool_name' in kwargs")
            
            headers["Accept"] = "application/json, text/event-stream"
            rpc_payload = {
                "jsonrpc": "2.0",
                "method": "tools/call",
                "params": {
                    "name": tool_name,
                    "arguments": payload if isinstance(payload, dict) else {"data": payload}
                },
                "id": 1
            }
            request_kwargs["json"] = rpc_payload
        else:
            if isinstance(payload, (dict, list)):
                request_kwargs["json"] = payload
            else:
                request_kwargs["content"] = payload

        resp = await self.ahttp.post(
            url, 
            timeout=timeout,
            headers=headers,
            **request_kwargs
        )
        self._handle_error(resp)

        if protocol == "mcp":
            return self._parse_mcp_sse_response(resp.text)

        content_type = resp.headers.get("content-type", "")
        if "application/json" in content_type:
            return resp.json()
        return resp.content


# === Functional Interface ===

default_client = MagnusClient()

def configure(token: Optional[str] = None, address: Optional[str] = None):
    """全局配置默认客户端。"""
    global default_client
    default_client = MagnusClient(token=token, address=address)

def submit_blueprint(
    blueprint_id: str,
    args: Optional[Dict[str, Any]] = None,
    use_preference: bool = True,
    save_preference: bool = True,
    timeout: float = 10.0
) -> str:
    return default_client.submit_blueprint(blueprint_id, args, use_preference, save_preference, timeout)

async def submit_blueprint_async(
    blueprint_id: str,
    args: Optional[Dict[str, Any]] = None,
    use_preference: bool = True,
    save_preference: bool = True,
    timeout: float = 10.0
) -> str:
    return await default_client.submit_blueprint_async(blueprint_id, args, use_preference, save_preference, timeout)

def run_blueprint(
    blueprint_id: str,
    args: Optional[Dict[str, Any]] = None,
    use_preference: bool = True,
    save_preference: bool = True,
    timeout: Optional[float] = None,
    poll_interval: float = 2.0
) -> Optional[str]:
    return default_client.run_blueprint(blueprint_id, args, use_preference, save_preference, timeout, poll_interval)

async def run_blueprint_async(
    blueprint_id: str,
    args: Optional[Dict[str, Any]] = None,
    use_preference: bool = True,
    save_preference: bool = True,
    timeout: Optional[float] = None,
    poll_interval: float = 2.0
) -> Optional[str]:
    return await default_client.run_blueprint_async(blueprint_id, args, use_preference, save_preference, timeout, poll_interval)

def call_service(
    service_id: str, 
    payload: Union[Dict[str, Any], str, bytes, list], 
    endpoint: Optional[str] = None,
    timeout: float = 60.0,
    protocol: Literal["http", "mcp"] = "http",
    **kwargs: Any,
) -> Any:
    return default_client.call_service(service_id, payload, endpoint, timeout, protocol, **kwargs)

async def call_service_async(
    service_id: str,
    payload: Union[Dict[str, Any], str, bytes, list],
    endpoint: Optional[str] = None,
    timeout: float = 60.0,
    protocol: Literal["http", "mcp"] = "http",
    **kwargs: Any,
) -> Any:
    return await default_client.call_service_async(service_id, payload, endpoint, timeout, protocol, **kwargs)


def custody_file(
    path: str,
    expire_minutes: int = 60,
    timeout: float = 300.0,
) -> str:
    return default_client.custody_file(path, expire_minutes, timeout)


async def custody_file_async(
    path: str,
    expire_minutes: int = 60,
    timeout: float = 300.0,
) -> str:
    return await default_client.custody_file_async(path, expire_minutes, timeout)


def list_jobs(
    limit: int = 20,
    skip: int = 0,
    search: Optional[str] = None,
    timeout: float = 10.0,
) -> Dict[str, Any]:
    return default_client.list_jobs(limit, skip, search, timeout)


async def list_jobs_async(
    limit: int = 20,
    skip: int = 0,
    search: Optional[str] = None,
    timeout: float = 10.0,
) -> Dict[str, Any]:
    return await default_client.list_jobs_async(limit, skip, search, timeout)


def get_job(
    job_id: str,
    timeout: float = 10.0,
) -> Dict[str, Any]:
    return default_client.get_job(job_id, timeout)


async def get_job_async(
    job_id: str,
    timeout: float = 10.0,
) -> Dict[str, Any]:
    return await default_client.get_job_async(job_id, timeout)


def terminate_job(
    job_id: str,
    timeout: float = 10.0,
) -> Dict[str, Any]:
    return default_client.terminate_job(job_id, timeout)


async def terminate_job_async(
    job_id: str,
    timeout: float = 10.0,
) -> Dict[str, Any]:
    return await default_client.terminate_job_async(job_id, timeout)


def get_job_logs(
    job_id: str,
    page: int = -1,
    timeout: float = 10.0,
) -> Dict[str, Any]:
    return default_client.get_job_logs(job_id, page, timeout)


def get_cluster_stats(
    timeout: float = 10.0,
) -> Dict[str, Any]:
    return default_client.get_cluster_stats(timeout)


def list_blueprints(
    limit: int = 20,
    skip: int = 0,
    search: Optional[str] = None,
    timeout: float = 10.0,
) -> Dict[str, Any]:
    return default_client.list_blueprints(limit, skip, search, timeout)


def list_services(
    limit: int = 20,
    skip: int = 0,
    search: Optional[str] = None,
    active_only: bool = False,
    timeout: float = 10.0,
) -> Dict[str, Any]:
    return default_client.list_services(limit, skip, search, active_only, timeout)


def get_blueprint_schema(
    blueprint_id: str,
    timeout: float = 10.0,
) -> List[Dict[str, Any]]:
    return default_client.get_blueprint_schema(blueprint_id, timeout)