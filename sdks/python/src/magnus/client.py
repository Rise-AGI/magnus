# sdks/python/src/magnus/client.py
import os
import ast
import time
import json
import asyncio
import logging
import tempfile as _tempfile
import tarfile as _tarfile
import httpx
from typing import Optional, Dict, Any, Union, Literal, List
from pathlib import Path

from .exceptions import (
    MagnusError, APIError,
    AuthenticationError, ForbiddenError, ResourceNotFoundError, ConflictError,
    ExecutionError, _ServerError,
)
from .config import (
    DEFAULT_ADDRESS, DEFAULT_TOKEN, ENV_MAGNUS_TOKEN, ENV_MAGNUS_ADDRESS,
    _get_current_site, normalize_address,
)
from .actions import execute_action
from .file_transfer import is_file_secret, get_tmp_base

logger = logging.getLogger("magnus")


def strip_imports(code: str) -> str:
    """Strip top-level import/from-import statements from blueprint code.

    Blueprint .py files can include real imports for IDE support, linting,
    and local testing. The backend sandbox already provides all necessary
    symbols via execution_globals, so imports are stripped before upload
    to avoid triggering the restricted-import guard.

    Uses ast to correctly handle multi-line imports (parenthesized, backslash).
    """
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return code

    import_lines: set = set()
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            for lineno in range(node.lineno, node.end_lineno + 1):  # type: ignore[operator]
                import_lines.add(lineno)

    lines = code.splitlines(keepends=True)
    result: List[str] = []
    for i, line in enumerate(lines):
        if (i + 1) not in import_lines:
            result.append(line)

    # Remove leading blank lines left by stripping
    while result and not result[0].strip():
        result.pop(0)
    return "".join(result)


def _format_schema_hint(schema: List[Dict[str, Any]]) -> str:
    lines = ["Blueprint parameters:"]
    for param in schema:
        key = param.get("key", "?")
        ptype = param.get("type", "unknown")
        default = param.get("default")
        desc = param.get("description", "")
        is_optional = param.get("is_optional", False)
        is_list = param.get("is_list", False)

        # 构建类型标签：select 展示合法值，其余展示原始类型
        options = param.get("options") or []
        if ptype == "select" and options:
            values = [str(o["value"]) for o in options]
            type_label = " | ".join(f'"{v}"' for v in values)
        else:
            type_label = ptype

        if is_list:
            type_label = f"List[{type_label}]"
        if is_optional:
            type_label = f"Optional[{type_label}]"

        if default is not None:
            header = f"  {key} ({type_label}, default={default!r})"
        else:
            header = f"  {key} ({type_label})"

        extras: List[str] = []
        if param.get("min") is not None:
            extras.append(f"min={param['min']}")
        if param.get("max") is not None:
            extras.append(f"max={param['max']}")

        # select 选项带 description 时逐行列出
        option_lines: List[str] = []
        if ptype == "select" and options:
            for o in options:
                opt_desc = o.get("description")
                if opt_desc:
                    option_lines.append(f"      {o['value']}: {opt_desc}")

        suffix = f" [{', '.join(extras)}]" if extras else ""
        line = f"{header}: {desc}{suffix}" if desc else f"{header}{suffix}"
        lines.append(line)
        lines.extend(option_lines)
    return "\n".join(lines)


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
        self.address = normalize_address(raw_address)
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
        try:
            self.token.encode("ascii")
        except UnicodeEncodeError:
            raise AuthenticationError(
                "Token contains non-ASCII characters (likely from copy-paste). "
                "Please re-enter the token with: magnus login"
            )
        try:
            self.address.encode("ascii")
        except UnicodeEncodeError:
            raise MagnusError(
                "Server address contains non-ASCII characters. "
                "Please re-enter the address with: magnus login"
            )

    def _network_error(self, context: str, error: Exception) -> MagnusError:
        """构造网络错误，对 localhost 连接失败自动追加诊断提示。"""
        msg = f"Network error while {context}: {error}"
        if isinstance(error, httpx.ConnectError) and "127.0.0.1" in self.address:
            msg += "\n\nConnection refused. Run 'magnus local start' to start the local server."
        return MagnusError(msg)

    _STATUS_EXCEPTIONS: Dict[int, type] = {
        401: AuthenticationError,
        403: ForbiddenError,
        404: ResourceNotFoundError,
        409: ConflictError,
    }

    def _handle_error(self, response: httpx.Response) -> None:
        if response.is_success:
            return

        try:
            detail = response.json().get("detail", response.text)
        except ValueError:
            detail = response.text

        if not detail:
            detail = f"Server returned HTTP {response.status_code}"

        exc_class = self._STATUS_EXCEPTIONS.get(response.status_code)
        if exc_class:
            raise exc_class(detail)
        raise APIError(response.status_code, detail)

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

    _TRANSFER_TRANSIENT_ERRORS = (httpx.TransportError,)
    _TRANSFER_MAX_RETRIES = 3
    _TRANSFER_BACKOFF_BASE = 2.0
    _TRANSFER_MAX_BACKOFF = 30.0

    def _post_file_with_retry(
        self,
        filename: str,
        file_handle: Any,
        data: Dict[str, str],
        timeout: float,
    ) -> httpx.Response:
        last_exc: Optional[Exception] = None
        for attempt in range(self._TRANSFER_MAX_RETRIES):
            try:
                file_handle.seek(0)
                resp = self.http.post(
                    "/files/upload",
                    files={"file": (filename, file_handle)},
                    data=data,
                    timeout=timeout,
                )
                if resp.status_code >= 500:
                    raise _ServerError(f"Server error {resp.status_code}")
                return resp
            except (*self._TRANSFER_TRANSIENT_ERRORS, _ServerError) as e:
                last_exc = e
                if attempt == self._TRANSFER_MAX_RETRIES - 1:
                    break
                backoff = min(self._TRANSFER_BACKOFF_BASE * (2 ** attempt), self._TRANSFER_MAX_BACKOFF)
                logger.warning(f"Upload attempt {attempt + 1} failed: {e}. Retrying in {backoff:.0f}s...")
                time.sleep(backoff)
        assert last_exc is not None
        raise last_exc

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
            with _tempfile.TemporaryDirectory(dir=get_tmp_base()) as tmpdir:
                tmp_path = Path(tmpdir) / f"{p.name}.tar.gz"
                with _tarfile.open(str(tmp_path), "w:gz", format=_tarfile.PAX_FORMAT, dereference=True) as tar:
                    tar.add(str(p), arcname=p.name)
                with open(tmp_path, "rb") as f:
                    resp = self._post_file_with_retry(f"{p.name}.tar.gz", f, data, timeout)
        else:
            with open(p, "rb") as f:
                resp = self._post_file_with_retry(p.name, f, data, timeout)

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
        except ResourceNotFoundError:
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

        try:
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

            resp = self.http.post(
                f"/blueprints/{blueprint_id}/run",
                json=payload,
                timeout=timeout,
            )
            self._handle_error(resp)
            return resp.json()["job_id"]
        except MagnusError as e:
            try:
                schema = self.get_blueprint_schema(blueprint_id)
                hint = _format_schema_hint(schema)
                raise MagnusError(f"{e}\n\n{hint}") from e
            except MagnusError as schema_err:
                if schema_err.__cause__ is e:
                    raise
                raise e from None
        except httpx.TimeoutException:
            raise MagnusError("Request timed out while launching blueprint.")
        except httpx.TransportError as e:
            raise self._network_error("launching blueprint", e)

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

    # Transient errors retried during polling.
    # httpx.TransportError covers: TimeoutException, NetworkError (ConnectError,
    # ReadError, ...), ProtocolError (RemoteProtocolError, ...), DecodingError, etc.
    # _ServerError handles 5xx from reverse proxies during restarts.
    # Auth/404 errors propagate immediately via _handle_error — NOT retried.
    _TRANSIENT_ERRORS = (httpx.TransportError, _ServerError)
    _MAX_CONSECUTIVE_FAILURES = 30
    _MAX_BACKOFF = 30.0

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

    def _poll_once(self, job_id: str) -> Dict[str, Any]:
        resp = self.http.get(f"/jobs/{job_id}")
        if resp.status_code >= 500:
            raise _ServerError(f"Server error {resp.status_code}")
        self._handle_error(resp)
        return resp.json()

    async def _poll_once_async(self, job_id: str) -> Dict[str, Any]:
        resp = await self.ahttp.get(f"/jobs/{job_id}")
        if resp.status_code >= 500:
            raise _ServerError(f"Server error {resp.status_code}")
        self._handle_error(resp)
        return resp.json()

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
        consecutive_failures = 0
        while True:
            if timeout and (time.time() - start_time > timeout):
                raise TimeoutError(f"Job {job_id} timed out after {timeout}s")

            try:
                data = self._poll_once(job_id)
                consecutive_failures = 0
            except self._TRANSIENT_ERRORS as e:
                consecutive_failures += 1
                if consecutive_failures >= self._MAX_CONSECUTIVE_FAILURES:
                    raise MagnusError(
                        f"Lost connection to server after {consecutive_failures} consecutive failures "
                        f"(last: {e}). Job {job_id} may still be running — "
                        f"check with: magnus job status {job_id}"
                    ) from e
                backoff = min(poll_interval * (2 ** (consecutive_failures - 1)), self._MAX_BACKOFF)
                logger.warning(f"Poll attempt failed ({consecutive_failures}x): {e}. Retrying in {backoff:.0f}s...")
                time.sleep(backoff)
                continue

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
        consecutive_failures = 0
        while True:
            if timeout and (time.time() - start_time > timeout):
                raise TimeoutError(f"Job {job_id} timed out after {timeout}s")

            try:
                data = await self._poll_once_async(job_id)
                consecutive_failures = 0
            except self._TRANSIENT_ERRORS as e:
                consecutive_failures += 1
                if consecutive_failures >= self._MAX_CONSECUTIVE_FAILURES:
                    raise MagnusError(
                        f"Lost connection to server after {consecutive_failures} consecutive failures "
                        f"(last: {e}). Job {job_id} may still be running — "
                        f"check with: magnus job status {job_id}"
                    ) from e
                backoff = min(poll_interval * (2 ** (consecutive_failures - 1)), self._MAX_BACKOFF)
                logger.warning(f"Poll attempt failed ({consecutive_failures}x): {e}. Retrying in {backoff:.0f}s...")
                await asyncio.sleep(backoff)
                continue

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
        except httpx.TransportError as e:
            raise self._network_error("submitting job", e)

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
        except httpx.TransportError as e:
            raise self._network_error("listing jobs", e)

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
        except httpx.TransportError as e:
            raise self._network_error("getting job info", e)

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
        except httpx.TransportError as e:
            raise self._network_error("getting job result", e)

    async def get_job_result_async(
        self,
        job_id: str,
        timeout: float = 10.0,
    ) -> Optional[str]:
        return await asyncio.to_thread(self.get_job_result, job_id, timeout)

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
        except httpx.TransportError as e:
            raise self._network_error("getting job action", e)

    async def get_job_action_async(
        self,
        job_id: str,
        timeout: float = 10.0,
    ) -> Optional[str]:
        return await asyncio.to_thread(self.get_job_action, job_id, timeout)

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
        except httpx.TransportError as e:
            raise self._network_error("terminating job", e)

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
        except httpx.TransportError as e:
            raise self._network_error("getting job logs", e)

    async def get_job_logs_async(
        self,
        job_id: str,
        page: int = -1,
        timeout: float = 10.0,
    ) -> Dict[str, Any]:
        return await asyncio.to_thread(self.get_job_logs, job_id, page, timeout)

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
        except httpx.TransportError as e:
            raise self._network_error("getting cluster stats", e)

    async def get_cluster_stats_async(
        self,
        timeout: float = 10.0,
    ) -> Dict[str, Any]:
        return await asyncio.to_thread(self.get_cluster_stats, timeout)

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
        except httpx.TransportError as e:
            raise self._network_error("listing blueprints", e)

    async def list_blueprints_async(
        self,
        limit: int = 20,
        skip: int = 0,
        search: Optional[str] = None,
        timeout: float = 10.0,
    ) -> Dict[str, Any]:
        return await asyncio.to_thread(self.list_blueprints, limit, skip, search, timeout)

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
        except httpx.TransportError as e:
            raise self._network_error("getting blueprint schema", e)

    async def get_blueprint_schema_async(
        self,
        blueprint_id: str,
        timeout: float = 10.0,
    ) -> List[Dict[str, Any]]:
        return await asyncio.to_thread(self.get_blueprint_schema, blueprint_id, timeout)

    def get_blueprint(
        self,
        blueprint_id: str,
        timeout: float = 10.0,
    ) -> Dict[str, Any]:
        try:
            resp = self.http.get(f"/blueprints/{blueprint_id}", timeout=timeout)
            self._handle_error(resp)
            return resp.json()
        except httpx.TimeoutException:
            raise MagnusError("Request timed out while getting blueprint.")
        except httpx.TransportError as e:
            raise self._network_error("getting blueprint", e)

    async def get_blueprint_async(
        self,
        blueprint_id: str,
        timeout: float = 10.0,
    ) -> Dict[str, Any]:
        return await asyncio.to_thread(self.get_blueprint, blueprint_id, timeout)

    def save_blueprint(
        self,
        blueprint_id: str,
        title: str,
        description: str,
        code: str,
        timeout: float = 10.0,
    ) -> Dict[str, Any]:
        payload = {
            "id": blueprint_id,
            "title": title,
            "description": description,
            "code": strip_imports(code),
        }
        try:
            resp = self.http.post("/blueprints", json=payload, timeout=timeout)
            self._handle_error(resp)
            return resp.json()
        except httpx.TimeoutException:
            raise MagnusError("Request timed out while saving blueprint.")
        except httpx.TransportError as e:
            raise self._network_error("saving blueprint", e)

    async def save_blueprint_async(
        self,
        blueprint_id: str,
        title: str,
        description: str,
        code: str,
        timeout: float = 10.0,
    ) -> Dict[str, Any]:
        return await asyncio.to_thread(
            self.save_blueprint, blueprint_id, title, description, code, timeout,
        )

    def delete_blueprint(
        self,
        blueprint_id: str,
        timeout: float = 10.0,
    ) -> None:
        try:
            resp = self.http.delete(f"/blueprints/{blueprint_id}", timeout=timeout)
            self._handle_error(resp)
        except httpx.TimeoutException:
            raise MagnusError("Request timed out while deleting blueprint.")
        except httpx.TransportError as e:
            raise self._network_error("deleting blueprint", e)

    async def delete_blueprint_async(
        self,
        blueprint_id: str,
        timeout: float = 10.0,
    ) -> None:
        return await asyncio.to_thread(self.delete_blueprint, blueprint_id, timeout)

    # === Skill Methods ===

    _SKILL_MAX_TOTAL_BYTES = 512 * 1024  # 512 KB
    _SKILL_RESOURCE_MAX_BYTES = 32 * 1024 * 1024  # 32 MB per resource file
    _SKILL_RESOURCE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".gif"}

    def _validate_skill_size(self, files: List[Dict[str, str]]) -> None:
        total = sum(len(f.get("content", "").encode("utf-8")) for f in files)
        if total > self._SKILL_MAX_TOTAL_BYTES:
            raise MagnusError(
                f"Skill total file size ({total:,} bytes) exceeds limit "
                f"({self._SKILL_MAX_TOTAL_BYTES:,} bytes). "
                f"Skills are for knowledge & prompts, not large data."
            )

    def list_skills(
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
            resp = self.http.get("/skills", params=params, timeout=timeout)
            self._handle_error(resp)
            return resp.json()
        except httpx.TimeoutException:
            raise MagnusError("Request timed out while listing skills.")
        except httpx.TransportError as e:
            raise self._network_error("listing skills", e)

    async def list_skills_async(
        self,
        limit: int = 20,
        skip: int = 0,
        search: Optional[str] = None,
        timeout: float = 10.0,
    ) -> Dict[str, Any]:
        return await asyncio.to_thread(self.list_skills, limit, skip, search, timeout)

    def get_skill(
        self,
        skill_id: str,
        timeout: float = 10.0,
    ) -> Dict[str, Any]:
        try:
            resp = self.http.get(f"/skills/{skill_id}", timeout=timeout)
            self._handle_error(resp)
            return resp.json()
        except httpx.TimeoutException:
            raise MagnusError("Request timed out while getting skill.")
        except httpx.TransportError as e:
            raise self._network_error("getting skill", e)

    async def get_skill_async(
        self,
        skill_id: str,
        timeout: float = 10.0,
    ) -> Dict[str, Any]:
        return await asyncio.to_thread(self.get_skill, skill_id, timeout)

    def save_skill(
        self,
        skill_id: str,
        title: str,
        description: str,
        files: List[Dict[str, str]],
        timeout: float = 10.0,
    ) -> Dict[str, Any]:
        self._validate_skill_size(files)
        payload = {
            "id": skill_id,
            "title": title,
            "description": description,
            "files": files,
        }
        try:
            resp = self.http.post("/skills", json=payload, timeout=timeout)
            self._handle_error(resp)
            return resp.json()
        except httpx.TimeoutException:
            raise MagnusError("Request timed out while saving skill.")
        except httpx.TransportError as e:
            raise self._network_error("saving skill", e)

    async def save_skill_async(
        self,
        skill_id: str,
        title: str,
        description: str,
        files: List[Dict[str, str]],
        timeout: float = 10.0,
    ) -> Dict[str, Any]:
        return await asyncio.to_thread(
            self.save_skill, skill_id, title, description, files, timeout,
        )

    def delete_skill(
        self,
        skill_id: str,
        timeout: float = 10.0,
    ) -> None:
        try:
            resp = self.http.delete(f"/skills/{skill_id}", timeout=timeout)
            self._handle_error(resp)
        except httpx.TimeoutException:
            raise MagnusError("Request timed out while deleting skill.")
        except httpx.TransportError as e:
            raise self._network_error("deleting skill", e)

    async def delete_skill_async(
        self,
        skill_id: str,
        timeout: float = 10.0,
    ) -> None:
        return await asyncio.to_thread(self.delete_skill, skill_id, timeout)

    def upload_skill_resource(
        self,
        skill_id: str,
        file_path: Path,
        timeout: float = 30.0,
    ) -> Dict[str, Any]:
        file_path = Path(file_path)
        ext = file_path.suffix.lower()
        if ext not in self._SKILL_RESOURCE_EXTENSIONS:
            raise MagnusError(f"Unsupported resource type '{ext}'. Allowed: {', '.join(sorted(self._SKILL_RESOURCE_EXTENSIONS))}")
        if file_path.stat().st_size > self._SKILL_RESOURCE_MAX_BYTES:
            raise MagnusError(f"Resource file too large ({file_path.stat().st_size:,} bytes). Limit: {self._SKILL_RESOURCE_MAX_BYTES:,} bytes.")
        try:
            with open(file_path, "rb") as f:
                resp = self.http.post(
                    f"/skills/{skill_id}/resources",
                    files={"file": (file_path.name, f)},
                    timeout=timeout,
                )
            self._handle_error(resp)
            return resp.json()
        except httpx.TimeoutException:
            raise MagnusError("Request timed out while uploading skill resource.")
        except httpx.TransportError as e:
            raise self._network_error("uploading skill resource", e)

    async def upload_skill_resource_async(
        self,
        skill_id: str,
        file_path: Path,
        timeout: float = 30.0,
    ) -> Dict[str, Any]:
        return await asyncio.to_thread(self.upload_skill_resource, skill_id, file_path, timeout)

    def download_skill_resource(
        self,
        skill_id: str,
        resource_path: str,
        dest: Path,
        timeout: float = 30.0,
    ) -> Path:
        try:
            resp = self.http.get(f"/skills/{skill_id}/files/{resource_path}", timeout=timeout)
            self._handle_error(resp)
            dest = Path(dest)
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(resp.content)
            return dest
        except httpx.TimeoutException:
            raise MagnusError("Request timed out while downloading skill resource.")
        except httpx.TransportError as e:
            raise self._network_error("downloading skill resource", e)

    # === Image Management ===

    def list_images(
        self,
        search: Optional[str] = None,
        timeout: float = 10.0,
    ) -> Dict[str, Any]:
        params: Dict[str, Any] = {}
        if search:
            params["search"] = search
        try:
            resp = self.http.get("/images", params=params, timeout=timeout)
            self._handle_error(resp)
            return resp.json()
        except httpx.TimeoutException:
            raise MagnusError("Request timed out while listing images.")
        except httpx.TransportError as e:
            raise self._network_error("listing images", e)

    async def list_images_async(
        self,
        search: Optional[str] = None,
        timeout: float = 10.0,
    ) -> Dict[str, Any]:
        return await asyncio.to_thread(self.list_images, search, timeout)

    def pull_image(
        self,
        uri: str,
        timeout: float = 30.0,
    ) -> Dict[str, Any]:
        try:
            resp = self.http.post("/images", json={"uri": uri}, timeout=timeout)
            self._handle_error(resp)
            return resp.json()
        except httpx.TimeoutException:
            raise MagnusError("Request timed out while submitting image pull.")
        except httpx.TransportError as e:
            raise self._network_error("pulling image", e)

    async def pull_image_async(
        self,
        uri: str,
        timeout: float = 30.0,
    ) -> Dict[str, Any]:
        return await asyncio.to_thread(self.pull_image, uri, timeout)

    def refresh_image(
        self,
        image_id: int,
        timeout: float = 30.0,
    ) -> Dict[str, Any]:
        try:
            resp = self.http.post(f"/images/{image_id}/refresh", timeout=timeout)
            self._handle_error(resp)
            return resp.json()
        except httpx.TimeoutException:
            raise MagnusError("Request timed out while submitting image refresh.")
        except httpx.TransportError as e:
            raise self._network_error("refreshing image", e)

    async def refresh_image_async(
        self,
        image_id: int,
        timeout: float = 30.0,
    ) -> Dict[str, Any]:
        return await asyncio.to_thread(self.refresh_image, image_id, timeout)

    def remove_image(
        self,
        image_id: int,
        timeout: float = 10.0,
    ) -> None:
        try:
            resp = self.http.delete(f"/images/{image_id}", timeout=timeout)
            self._handle_error(resp)
        except httpx.TimeoutException:
            raise MagnusError("Request timed out while removing image.")
        except httpx.TransportError as e:
            raise self._network_error("removing image", e)

    async def remove_image_async(
        self,
        image_id: int,
        timeout: float = 10.0,
    ) -> None:
        return await asyncio.to_thread(self.remove_image, image_id, timeout)

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
        except httpx.TransportError as e:
            raise self._network_error("listing services", e)

    async def list_services_async(
        self,
        limit: int = 20,
        skip: int = 0,
        search: Optional[str] = None,
        active_only: bool = False,
        timeout: float = 10.0,
    ) -> Dict[str, Any]:
        return await asyncio.to_thread(
            self.list_services, limit, skip, search, active_only, timeout,
        )

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

        try:
            resp = self.http.post(
                url,
                timeout=timeout,
                headers=headers,
                **request_kwargs,
            )
        except httpx.TimeoutException:
            raise MagnusError("Request timed out while calling service.")
        except httpx.TransportError as e:
            raise self._network_error("calling service", e)
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
