# sdks/python/src/magnus/client.py
import os
import time
import json
import asyncio
import logging
import tempfile as _tempfile
import tarfile as _tarfile
import httpx
from typing import Optional, Dict, Any, Union, Literal, List
from pathlib import Path

from .exceptions import MagnusError, AuthenticationError, ResourceNotFoundError, ExecutionError
from .config import (
    DEFAULT_ADDRESS, DEFAULT_TOKEN, ENV_MAGNUS_TOKEN, ENV_MAGNUS_ADDRESS,
    _get_current_site,
)
from .actions import execute_action
from .file_transfer import is_file_secret

logger = logging.getLogger("magnus")


class MagnusClient:

    def __init__(self, token: Optional[str] = None, address: Optional[str] = None):
        site = _get_current_site()
        self.token = token or os.getenv(ENV_MAGNUS_TOKEN) or site.get("token") or DEFAULT_TOKEN

        raw_address = (
            address
            or os.getenv(ENV_MAGNUS_ADDRESS)
            or site.get("address")
            or DEFAULT_ADDRESS
        )
        self.address = raw_address.rstrip("/")
        if self.address.endswith("/api"):
            self.address = self.address[:-4]

        self._client: Optional[httpx.Client] = None
        self._async_client: Optional[httpx.AsyncClient] = None
        self.last_job_id: Optional[str] = None

    # === Lifecycle ===

    def close(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None
        self._async_client = None

    async def aclose(self) -> None:
        if self._async_client is not None:
            await self._async_client.aclose()
            self._async_client = None
        if self._client is not None:
            self._client.close()
            self._client = None

    def __enter__(self) -> "MagnusClient":
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()

    async def __aenter__(self) -> "MagnusClient":
        return self

    async def __aexit__(self, *args: Any) -> None:
        await self.aclose()

    # === HTTP Properties ===

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
                timeout=30.0,
            )
        return self._client

    @property
    def ahttp(self) -> httpx.AsyncClient:
        if self._async_client is None:
            self._validate_config()
            self._async_client = httpx.AsyncClient(
                base_url=self.api_base,
                headers={"Authorization": f"Bearer {self.token}"},
                timeout=30.0,
            )
        return self._async_client

    def _validate_config(self) -> None:
        if not self.token:
            raise AuthenticationError(
                f"Magnus API Token is missing. Set {ENV_MAGNUS_TOKEN} or init with token."
            )

    def _handle_error(self, response: httpx.Response) -> None:
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
        elif response.status_code == 413:
            raise MagnusError(f"Upload rejected: {detail}")
        else:
            raise MagnusError(f"API Error ({response.status_code}): {detail}")

    def _join_url(self, base: str, part: Optional[str]) -> str:
        if not part:
            return base.rstrip("/")
        return f"{base.rstrip('/')}/{part.lstrip('/')}"

    def _parse_mcp_sse_response(self, text: str) -> str:
        results: List[str] = []
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

    # === File Upload ===

    def _upload_file(
        self,
        path: str,
        expire_minutes: int = 60,
        max_downloads: Optional[int] = 1,
        timeout: float = 300.0,
    ) -> str:
        p = Path(path)
        if not p.exists():
            raise MagnusError(f"Path does not exist: {path}")

        data: Dict[str, str] = {"expire_minutes": str(expire_minutes)}
        if max_downloads is not None:
            data["max_downloads"] = str(max_downloads)

        is_dir = p.is_dir()
        if is_dir:
            data["is_directory"] = "true"
            with _tempfile.TemporaryDirectory() as tmpdir:
                tmp_path = Path(tmpdir) / f"{p.name}.tar.gz"
                with _tarfile.open(str(tmp_path), "w:gz") as tar:
                    tar.add(str(p), arcname=p.name)
                with open(tmp_path, "rb") as f:
                    resp = self.http.post(
                        "/files/upload",
                        files={"file": (f"{p.name}.tar.gz", f)},
                        data=data,
                        timeout=timeout,
                    )
        else:
            with open(p, "rb") as f:
                resp = self.http.post(
                    "/files/upload",
                    files={"file": (p.name, f)},
                    data=data,
                    timeout=timeout,
                )

        self._handle_error(resp)
        return resp.json()["file_secret"]

    # === Blueprint Methods ===

    def _get_file_secret_params(self, blueprint_id: str) -> List[Dict[str, Any]]:
        try:
            schema = self.get_blueprint_schema(blueprint_id)
            return [
                {"key": param["key"], "is_list": bool(param.get("is_list"))}
                for param in schema
                if param.get("type") == "file_secret"
            ]
        except Exception:
            return []

    def _upload_file_secret_value(
        self,
        value: Any,
        is_list: bool,
        expire_minutes: int,
        max_downloads: Optional[int],
    ) -> Any:
        if value is None:
            return None
        if is_list:
            items = value if isinstance(value, list) else [value]
            return [
                item if is_file_secret(str(item)) else self._upload_file(str(item), expire_minutes, max_downloads)
                for item in items
            ]
        value_str = str(value)
        if is_file_secret(value_str):
            return value
        return self._upload_file(value_str, expire_minutes, max_downloads)

    def launch_blueprint(
        self,
        blueprint_id: str,
        args: Optional[Dict[str, Any]] = None,
        use_preference: bool = False,
        save_preference: bool = True,
        expire_minutes: int = 60,
        max_downloads: Optional[int] = 1,
        timeout: float = 10.0,
    ) -> str:
        final_args = dict(args) if args else {}

        for param in self._get_file_secret_params(blueprint_id):
            key = param["key"]
            if key not in final_args:
                continue
            final_args[key] = self._upload_file_secret_value(
                final_args[key], param["is_list"], expire_minutes, max_downloads,
            )

        payload = {
            "parameters": final_args,
            "use_preference": use_preference,
            "save_preference": save_preference,
        }

        try:
            resp = self.http.post(
                f"/blueprints/{blueprint_id}/run",
                json=payload,
                timeout=timeout,
            )
            self._handle_error(resp)
            return resp.json()["job_id"]
        except httpx.TimeoutException:
            raise MagnusError("Request timed out while launching blueprint.")

    async def launch_blueprint_async(
        self,
        blueprint_id: str,
        args: Optional[Dict[str, Any]] = None,
        use_preference: bool = False,
        save_preference: bool = True,
        expire_minutes: int = 60,
        max_downloads: Optional[int] = 1,
        timeout: float = 10.0,
    ) -> str:
        return await asyncio.to_thread(
            self.launch_blueprint,
            blueprint_id, args, use_preference, save_preference,
            expire_minutes, max_downloads, timeout,
        )

    # === Job Polling ===

    def _process_completed_job(
        self,
        data: Dict[str, Any],
        should_execute_action: bool,
    ) -> Optional[str]:
        result: Optional[str] = data.get("result")
        action: Optional[str] = data.get("action")
        if should_execute_action and action:
            execute_action(action)
        return result

    def _poll_job_completion(
        self,
        job_id: str,
        timeout: Optional[float] = None,
        poll_interval: float = 2.0,
        execute_action_flag: bool = True,
    ) -> Optional[str]:
        self.last_job_id = job_id
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
                return self._process_completed_job(data, execute_action_flag)
            elif status in ["Failed", "Terminated"]:
                raise ExecutionError(f"Job {job_id} ended with status: {status}")

            time.sleep(poll_interval)

    async def _poll_job_completion_async(
        self,
        job_id: str,
        timeout: Optional[float] = None,
        poll_interval: float = 2.0,
        execute_action_flag: bool = True,
    ) -> Optional[str]:
        self.last_job_id = job_id
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
                return self._process_completed_job(data, execute_action_flag)
            elif status in ["Failed", "Terminated"]:
                raise ExecutionError(f"Job {job_id} ended with status: {status}")

            await asyncio.sleep(poll_interval)

    # === Blueprint: Blocking ===

    def run_blueprint(
        self,
        blueprint_id: str,
        args: Optional[Dict[str, Any]] = None,
        use_preference: bool = False,
        save_preference: bool = True,
        expire_minutes: int = 60,
        max_downloads: Optional[int] = 1,
        timeout: Optional[float] = None,
        poll_interval: float = 2.0,
        execute_action: bool = True,
    ) -> Optional[str]:
        job_id = self.launch_blueprint(
            blueprint_id=blueprint_id,
            args=args,
            use_preference=use_preference,
            save_preference=save_preference,
            expire_minutes=expire_minutes,
            max_downloads=max_downloads,
        )
        return self._poll_job_completion(job_id, timeout, poll_interval, execute_action)

    async def run_blueprint_async(
        self,
        blueprint_id: str,
        args: Optional[Dict[str, Any]] = None,
        use_preference: bool = False,
        save_preference: bool = True,
        expire_minutes: int = 60,
        max_downloads: Optional[int] = 1,
        timeout: Optional[float] = None,
        poll_interval: float = 2.0,
        execute_action: bool = True,
    ) -> Optional[str]:
        job_id = await self.launch_blueprint_async(
            blueprint_id=blueprint_id,
            args=args,
            use_preference=use_preference,
            save_preference=save_preference,
            expire_minutes=expire_minutes,
            max_downloads=max_downloads,
        )
        return await self._poll_job_completion_async(job_id, timeout, poll_interval, execute_action)

    # === Job Submission ===

    @staticmethod
    def _build_job_payload(
        task_name: str,
        entry_command: str,
        repo_name: str,
        branch: Optional[str],
        commit_sha: Optional[str],
        gpu_type: str,
        gpu_count: int,
        namespace: str,
        job_type: str,
        description: Optional[str],
        container_image: Optional[str],
        cpu_count: Optional[int],
        memory_demand: Optional[str],
        ephemeral_storage: Optional[str],
        runner: Optional[str],
        system_entry_command: Optional[str],
    ) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "task_name": task_name,
            "entry_command": entry_command,
            "repo_name": repo_name,
            "gpu_type": gpu_type,
            "gpu_count": gpu_count,
            "namespace": namespace,
            "job_type": job_type,
        }
        for key, val in [
            ("branch", branch),
            ("commit_sha", commit_sha),
            ("description", description),
            ("container_image", container_image),
            ("cpu_count", cpu_count),
            ("memory_demand", memory_demand),
            ("ephemeral_storage", ephemeral_storage),
            ("runner", runner),
            ("system_entry_command", system_entry_command),
        ]:
            if val is not None:
                payload[key] = val
        return payload

    def submit_job(
        self,
        task_name: str,
        entry_command: str,
        repo_name: str,
        branch: Optional[str] = None,
        commit_sha: Optional[str] = None,
        gpu_type: str = "cpu",
        gpu_count: int = 0,
        namespace: str = "Rise-AGI",
        job_type: str = "A2",
        description: Optional[str] = None,
        container_image: Optional[str] = None,
        cpu_count: Optional[int] = None,
        memory_demand: Optional[str] = None,
        ephemeral_storage: Optional[str] = None,
        runner: Optional[str] = None,
        system_entry_command: Optional[str] = None,
        timeout: float = 10.0,
    ) -> str:
        payload = self._build_job_payload(
            task_name, entry_command, repo_name, branch, commit_sha,
            gpu_type, gpu_count, namespace, job_type, description,
            container_image, cpu_count, memory_demand, ephemeral_storage,
            runner, system_entry_command,
        )
        try:
            resp = self.http.post("/jobs/submit", json=payload, timeout=timeout)
            self._handle_error(resp)
            return resp.json()["id"]
        except httpx.TimeoutException:
            raise MagnusError("Request timed out while submitting job.")

    async def submit_job_async(
        self,
        task_name: str,
        entry_command: str,
        repo_name: str,
        branch: Optional[str] = None,
        commit_sha: Optional[str] = None,
        gpu_type: str = "cpu",
        gpu_count: int = 0,
        namespace: str = "Rise-AGI",
        job_type: str = "A2",
        description: Optional[str] = None,
        container_image: Optional[str] = None,
        cpu_count: Optional[int] = None,
        memory_demand: Optional[str] = None,
        ephemeral_storage: Optional[str] = None,
        runner: Optional[str] = None,
        system_entry_command: Optional[str] = None,
        timeout: float = 10.0,
    ) -> str:
        return await asyncio.to_thread(
            self.submit_job,
            task_name, entry_command, repo_name, branch, commit_sha,
            gpu_type, gpu_count, namespace, job_type, description,
            container_image, cpu_count, memory_demand, ephemeral_storage,
            runner, system_entry_command, timeout,
        )

    def execute_job(
        self,
        task_name: str,
        entry_command: str,
        repo_name: str,
        branch: Optional[str] = None,
        commit_sha: Optional[str] = None,
        gpu_type: str = "cpu",
        gpu_count: int = 0,
        namespace: str = "Rise-AGI",
        job_type: str = "A2",
        description: Optional[str] = None,
        container_image: Optional[str] = None,
        cpu_count: Optional[int] = None,
        memory_demand: Optional[str] = None,
        ephemeral_storage: Optional[str] = None,
        runner: Optional[str] = None,
        system_entry_command: Optional[str] = None,
        timeout: Optional[float] = None,
        poll_interval: float = 2.0,
        execute_action: bool = True,
    ) -> Optional[str]:
        job_id = self.submit_job(
            task_name=task_name, entry_command=entry_command,
            repo_name=repo_name, branch=branch,
            commit_sha=commit_sha,
            gpu_type=gpu_type, gpu_count=gpu_count, namespace=namespace,
            job_type=job_type, description=description,
            container_image=container_image, cpu_count=cpu_count,
            memory_demand=memory_demand, ephemeral_storage=ephemeral_storage,
            runner=runner, system_entry_command=system_entry_command,
        )
        return self._poll_job_completion(job_id, timeout, poll_interval, execute_action)

    async def execute_job_async(
        self,
        task_name: str,
        entry_command: str,
        repo_name: str,
        branch: Optional[str] = None,
        commit_sha: Optional[str] = None,
        gpu_type: str = "cpu",
        gpu_count: int = 0,
        namespace: str = "Rise-AGI",
        job_type: str = "A2",
        description: Optional[str] = None,
        container_image: Optional[str] = None,
        cpu_count: Optional[int] = None,
        memory_demand: Optional[str] = None,
        ephemeral_storage: Optional[str] = None,
        runner: Optional[str] = None,
        system_entry_command: Optional[str] = None,
        timeout: Optional[float] = None,
        poll_interval: float = 2.0,
        execute_action: bool = True,
    ) -> Optional[str]:
        job_id = await self.submit_job_async(
            task_name=task_name, entry_command=entry_command,
            repo_name=repo_name, branch=branch,
            commit_sha=commit_sha,
            gpu_type=gpu_type, gpu_count=gpu_count, namespace=namespace,
            job_type=job_type, description=description,
            container_image=container_image, cpu_count=cpu_count,
            memory_demand=memory_demand, ephemeral_storage=ephemeral_storage,
            runner=runner, system_entry_command=system_entry_command,
        )
        return await self._poll_job_completion_async(job_id, timeout, poll_interval, execute_action)

    # === Job Management ===

    def list_jobs(
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
        return await asyncio.to_thread(self.list_jobs, limit, skip, search, timeout)

    def get_job(
        self,
        job_id: str,
        timeout: float = 10.0,
    ) -> Dict[str, Any]:
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
        return await asyncio.to_thread(self.get_job, job_id, timeout)

    def get_job_result(
        self,
        job_id: str,
        timeout: float = 10.0,
    ) -> Optional[str]:
        try:
            resp = self.http.get(f"/jobs/{job_id}/result", timeout=timeout)
            self._handle_error(resp)
            return resp.json()
        except httpx.TimeoutException:
            raise MagnusError("Request timed out while getting job result.")

    def get_job_action(
        self,
        job_id: str,
        timeout: float = 10.0,
    ) -> Optional[str]:
        try:
            resp = self.http.get(f"/jobs/{job_id}/action", timeout=timeout)
            self._handle_error(resp)
            return resp.json()
        except httpx.TimeoutException:
            raise MagnusError("Request timed out while getting job action.")

    def terminate_job(
        self,
        job_id: str,
        timeout: float = 10.0,
    ) -> Dict[str, Any]:
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
        return await asyncio.to_thread(self.terminate_job, job_id, timeout)

    def get_job_logs(
        self,
        job_id: str,
        page: int = -1,
        timeout: float = 10.0,
    ) -> Dict[str, Any]:
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
        max_downloads: Optional[int] = None,
        timeout: float = 300.0,
    ) -> str:
        return self._upload_file(path, expire_minutes=expire_minutes, max_downloads=max_downloads, timeout=timeout)

    async def custody_file_async(
        self,
        path: str,
        expire_minutes: int = 60,
        max_downloads: Optional[int] = None,
        timeout: float = 300.0,
    ) -> str:
        return await asyncio.to_thread(
            self._upload_file, path,
            expire_minutes, max_downloads, timeout,
        )

    def call_service(
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

        headers: Dict[str, str] = {}
        request_kwargs: Dict[str, Any] = {}

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
                    "arguments": payload if isinstance(payload, dict) else {"data": payload},
                },
                "id": 1,
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
            **request_kwargs,
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
        return await asyncio.to_thread(
            self.call_service,
            service_id, payload, endpoint, timeout, protocol,
            **kwargs,
        )
