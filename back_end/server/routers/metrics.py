# back_end/server/routers/metrics.py
import os
import json
import logging
import glob
from typing import List, Optional, Dict, Any, Tuple

from fastapi import APIRouter, Depends, Query

from .. import models
from .._magnus_config import magnus_config
from .auth import get_current_user


logger = logging.getLogger(__name__)
router = APIRouter()

magnus_root = magnus_config["server"]["root"]
magnus_workspace_path = f"{magnus_root}/workspace"


def _metrics_dir_for_job(job_id: str) -> str:
    return f"{magnus_workspace_path}/jobs/{job_id}/metrics"


def _read_all_points(metrics_dir: str) -> List[Dict[str, Any]]:
    points: List[Dict[str, Any]] = []
    pattern = os.path.join(metrics_dir, "*.jsonl")
    for filepath in glob.glob(pattern):
        if not os.path.isfile(filepath):
            continue
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        points.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
        except OSError:
            continue
    return points


def _stream_key(point: Dict[str, Any]) -> Tuple:
    labels = point.get("labels") or {}
    return (
        point.get("name", ""),
        point.get("step_domain"),
        tuple(sorted(labels.items())),
    )


@router.get("/jobs/{job_id}/metrics/streams")
def list_metric_streams(
    job_id: str,
    _: models.User = Depends(get_current_user),
) -> List[Dict[str, Any]]:
    metrics_dir = _metrics_dir_for_job(job_id)
    if not os.path.isdir(metrics_dir):
        return []

    points = _read_all_points(metrics_dir)
    streams: Dict[Tuple, Dict[str, Any]] = {}

    for p in points:
        key = _stream_key(p)
        if key not in streams:
            streams[key] = {
                "name": p.get("name", ""),
                "kind": p.get("kind", "gauge"),
                "unit": p.get("unit"),
                "step_domain": p.get("step_domain"),
                "labels": p.get("labels") or {},
                "point_count": 0,
                "has_step": False,
            }
        streams[key]["point_count"] += 1
        if p.get("step") is not None:
            streams[key]["has_step"] = True

    return list(streams.values())


def _downsample(points: List[Dict[str, Any]], max_points: int) -> List[Dict[str, Any]]:
    if len(points) <= max_points:
        return points
    step = len(points) / max_points
    result = []
    i = 0.0
    while int(i) < len(points) and len(result) < max_points:
        result.append(points[int(i)])
        i += step
    if result[-1] is not points[-1]:
        result[-1] = points[-1]
    return result


@router.get("/jobs/{job_id}/metrics/query")
def query_metrics(
    job_id: str,
    name: str,
    labels: Optional[str] = None,
    step_domain: Optional[str] = None,
    since_ms: Optional[int] = None,
    until_ms: Optional[int] = None,
    max_points: int = Query(default=2000, le=10000),
    _: models.User = Depends(get_current_user),
) -> Dict[str, Any]:
    metrics_dir = _metrics_dir_for_job(job_id)
    if not os.path.isdir(metrics_dir):
        return {"name": name, "points": []}

    parsed_labels: Optional[Dict[str, str]] = None
    if labels:
        try:
            parsed_labels = json.loads(labels)
        except json.JSONDecodeError:
            parsed_labels = None

    all_points = _read_all_points(metrics_dir)

    filtered = []
    for p in all_points:
        if p.get("name") != name:
            continue
        if step_domain is not None and p.get("step_domain") != step_domain:
            continue
        if parsed_labels is not None:
            point_labels = p.get("labels") or {}
            if not all(point_labels.get(k) == v for k, v in parsed_labels.items()):
                continue
        t = p.get("time_unix_ms", 0)
        if since_ms is not None and t < since_ms:
            continue
        if until_ms is not None and t > until_ms:
            continue
        filtered.append(p)

    filtered.sort(key=lambda p: p.get("time_unix_ms", 0))
    filtered = _downsample(filtered, max_points)

    result_points = []
    for p in filtered:
        point = {
            "value": p.get("value"),
            "time_unix_ms": p.get("time_unix_ms"),
        }
        if p.get("step") is not None:
            point["step"] = p["step"]
        if p.get("labels"):
            point["labels"] = p["labels"]
        result_points.append(point)

    return {"name": name, "points": result_points}
