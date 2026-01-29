# sdks/python/src/magnus/__init__.py
import os
import time
import logging
import asyncio
import httpx
from typing import Optional, Dict, Any, Union

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

logger = logging.getLogger("magnus")

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
        self.token = token or os.getenv(ENV_MAGNUS_TOKEN)
        
        # 处理地址格式，移除末尾斜杠及 /api 后缀以保证拼接正确
        raw_address = address or os.getenv(ENV_MAGNUS_ADDRESS, DEFAULT_ADDRESS)
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

    # === Blueprint Methods ===

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
        """
        payload = {
            "parameters": args or {},
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
        payload = {
            "parameters": args or {},
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
        """
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

    async def run_blueprint_async(
        self,
        blueprint_id: str,
        args: Optional[Dict[str, Any]] = None,
        use_preference: bool = True,
        save_preference: bool = True,
        timeout: Optional[float] = None,
        poll_interval: float = 2.0,
    ) -> Optional[str]:
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

    def call_service(
        self,
        service_id: str,
        payload: Union[Dict[str, Any], str, bytes],
        timeout: float = 60.0,
    ) -> Any:
        """
        调用托管服务 (RPC)。
        """
        request_kwargs = {}
        if isinstance(payload, dict):
            request_kwargs["json"] = payload
        else:
            request_kwargs["content"] = payload

        resp = self.http.post(
            f"/services/{service_id}/", 
            timeout=timeout,
            **request_kwargs
        )
        self._handle_error(resp)

        content_type = resp.headers.get("content-type", "")
        if "application/json" in content_type:
            return resp.json()
        return resp.content

    async def call_service_async(
        self,
        service_id: str,
        payload: Union[Dict[str, Any], str, bytes],
        timeout: float = 60.0,
    ) -> Any:
        request_kwargs = {}
        if isinstance(payload, dict):
            request_kwargs["json"] = payload
        else:
            request_kwargs["content"] = payload

        resp = await self.ahttp.post(
            f"/services/{service_id}/", 
            timeout=timeout,
            **request_kwargs
        )
        self._handle_error(resp)

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
    payload: Union[Dict[str, Any], str, bytes], 
    timeout: float = 60.0
) -> Any:
    return default_client.call_service(service_id, payload, timeout)

async def call_service_async(
    service_id: str,
    payload: Union[Dict[str, Any], str, bytes],
    timeout: float = 60.0
) -> Any:
    return await default_client.call_service_async(service_id, payload, timeout)


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