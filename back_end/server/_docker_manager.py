# back_end/server/_docker_manager.py
# Symmetric interface to SlurmManager for local-mode Docker execution.
import logging
import subprocess
from typing import Dict, List, Optional


__all__ = [
    "DockerManager",
    "DockerError",
]


logger = logging.getLogger(__name__)


class DockerError(Exception):
    pass


# Magnus stores images as docker://org/repo:tag; Docker CLI expects org/repo:tag
def _normalize_image_uri(image: str) -> str:
    if image.startswith("docker://"):
        return image[len("docker://"):]
    return image


class DockerManager:

    def __init__(self) -> None:
        pass

    def run_container(
        self,
        container_name: str,
        image: str,
        entry_command: str,
        bind_mounts: List[str],
        env_vars: Dict[str, str],
        working_dir: str,
        gpu_enabled: bool = False,
        network_mode: Optional[str] = None,
    ) -> str:
        image = _normalize_image_uri(image)
        command = [
            "docker", "run", "-d",
            "--name", container_name,
            "-w", working_dir,
        ]

        if network_mode:
            command.extend(["--network", network_mode])

        for mount in bind_mounts:
            command.extend(["-v", mount])

        for key, value in env_vars.items():
            command.extend(["-e", f"{key}={value}"])

        if gpu_enabled:
            command.extend(["--gpus", "all"])

        command.append(image)
        command.extend(["bash", "-c", entry_command])

        logger.info(f"Starting container '{container_name}' (image: {image})")

        try:
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                check=True,
            )
            container_id = result.stdout.strip()[:12]
            logger.info(f"Container '{container_name}' started (ID: {container_id})")
            return container_id
        except subprocess.CalledProcessError as e:
            error_msg = e.stderr.strip()
            logger.error(f"Docker run failed: {error_msg}")
            raise DockerError(f"docker run failed: {error_msg}")

    def check_container_status(self, container_name: str) -> str:
        # Maps Docker State.Status to Magnus job status constants
        try:
            result = subprocess.run(
                ["docker", "inspect", "--format", "{{.State.Status}}:{{.State.ExitCode}}", container_name],
                capture_output=True,
                text=True,
            )
            if result.returncode != 0:
                return "UNKNOWN"

            output = result.stdout.strip()
            if ":" not in output:
                return "UNKNOWN"

            status, exit_code_str = output.rsplit(":", 1)

            if status == "running":
                return "RUNNING"
            elif status == "exited":
                exit_code = int(exit_code_str)
                return "COMPLETED" if exit_code == 0 else "FAILED"
            elif status in ("created", "restarting"):
                return "PENDING"
            else:
                return "UNKNOWN"
        except Exception as e:
            logger.error(f"Failed to check container status '{container_name}': {e}")
            return "UNKNOWN"

    def get_container_exit_code(self, container_name: str) -> Optional[int]:
        try:
            result = subprocess.run(
                ["docker", "inspect", "--format", "{{.State.ExitCode}}", container_name],
                capture_output=True,
                text=True,
            )
            if result.returncode == 0:
                return int(result.stdout.strip())
        except Exception:
            pass
        return None

    def stop_container(self, container_name: str, timeout: int = 10) -> None:
        try:
            subprocess.run(
                ["docker", "stop", "-t", str(timeout), container_name],
                capture_output=True,
                check=False,
            )
        except Exception as e:
            logger.error(f"docker stop failed for '{container_name}': {e}")

    def remove_container(self, container_name: str) -> None:
        try:
            subprocess.run(
                ["docker", "rm", "-f", container_name],
                capture_output=True,
                check=False,
            )
        except Exception as e:
            logger.warning(f"docker rm failed for '{container_name}': {e}")

    def pull_image(self, image: str) -> bool:
        image = _normalize_image_uri(image)
        logger.info(f"Pulling Docker image: {image}")
        try:
            subprocess.run(
                ["docker", "pull", image],
                capture_output=True,
                check=True,
            )
            logger.info(f"Docker image ready: {image}")
            return True
        except subprocess.CalledProcessError as e:
            logger.error(f"Docker pull failed: {e.stderr.strip()}")
            return False

    def image_exists(self, image: str) -> bool:
        image = _normalize_image_uri(image)
        try:
            result = subprocess.run(
                ["docker", "image", "inspect", image],
                capture_output=True,
            )
            return result.returncode == 0
        except Exception:
            return False
