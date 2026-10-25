"""Execution identities and isolated configuration views for one Magnus site."""
from copy import deepcopy
from dataclasses import dataclass
import re
from typing import Any, Callable


_TARGET_ID = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}\Z")
_OVERRIDES = {"name", "transport", "slurm", "container_runtime", "cluster"}


@dataclass(frozen=True)
class ExecutionTarget:
    id: str
    name: str
    config: dict[str, Any]
    is_default: bool = False

    def job_filter(
        self,
        job_model,
    ):
        field = job_model.execution_target
        match = field == self.id
        # NULL rows predate target selection and belong to the base deployment.
        return match | field.is_(None) if self.is_default else match

    @property
    def remote_root(self) -> str:
        transport = self.config["transport"]
        if transport["mode"] == "ssh":
            return transport["ssh"]["remote_root"]
        return self.config["server"]["root"]


def execution_targets(
    config: dict[str, Any],
    *,
    validate: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, ExecutionTarget]:
    execution = config["execution"]
    base_id = execution.get("target_id", "default")
    if not isinstance(base_id, str) or not _TARGET_ID.fullmatch(base_id):
        raise ValueError("execution.target_id must be a nonempty identifier (up to 64 characters)")
    base_name = execution.get("target_name") or config["cluster"]["name"]
    if not isinstance(base_name, str) or not base_name.strip():
        raise ValueError("execution.target_name must be a nonempty string")
    overrides = execution.get("targets", {})
    if not isinstance(overrides, dict):
        raise ValueError("execution.targets must be a mapping of target IDs to configurations")
    if overrides and execution["backend"] != "slurm":
        raise ValueError("Multiple execution targets currently require the slurm backend")

    base = deepcopy(config)
    base["execution"].pop("targets", None)
    result = {base_id: ExecutionTarget(base_id, base_name, base, True)}
    for target_id, settings in overrides.items():
        if not isinstance(target_id, str) or not _TARGET_ID.fullmatch(target_id):
            raise ValueError("Each execution target must have a stable identifier (up to 64 characters)")
        if target_id in result:
            raise ValueError(f"Duplicate execution target: {target_id}")
        if not isinstance(settings, dict) or set(settings) - _OVERRIDES:
            raise ValueError(f"Invalid configuration fields for execution target {target_id}")
        target = deepcopy(base)
        name = settings.get("name", target_id)
        if not isinstance(name, str) or not name.strip():
            raise ValueError(f"Execution target {target_id} requires a nonempty name")
        for section in ("transport", "slurm", "cluster"):
            if section not in settings:
                continue
            value = settings[section]
            if not isinstance(value, dict):
                raise ValueError(f"Execution target {target_id}.{section} must be a mapping")
            if section == "transport":
                # Authentication and remote paths must never leak from another
                # target through a partially inherited transport definition.
                target["transport"] = deepcopy(value)
            elif section == "slurm":
                target["execution"]["slurm"] = {**target["execution"].get("slurm", {}), **deepcopy(value)}
            else:
                target["cluster"].update(deepcopy(value))
        if "container_runtime" in settings:
            target["execution"]["container_runtime"] = settings["container_runtime"]
        target["execution"]["target_id"] = target_id
        target["execution"]["target_name"] = name
        if validate is not None:
            validate(target)
        result[target_id] = ExecutionTarget(target_id, name, target)
    return result


def select_execution_target(
    targets: dict[str, ExecutionTarget],
    target_id: str | None,
) -> ExecutionTarget:
    if target_id is None:
        return next(target for target in targets.values() if target.is_default)
    if not isinstance(target_id, str) or not _TARGET_ID.fullmatch(target_id):
        raise ValueError("Invalid execution target identifier")
    try:
        return targets[target_id]
    except KeyError:
        raise ValueError(f"Unknown execution target {target_id!r}; available: {', '.join(targets)}") from None
