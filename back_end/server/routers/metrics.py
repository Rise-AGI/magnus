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


def _build_streams(points: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
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


def _metric_priority(stream: Dict[str, Any]) -> int:
    # user metrics > system.gpu.* > other system.*; step-bearing first within tier
    name = stream.get("name", "")
    is_system = name.startswith("system.")
    is_gpu = name.startswith("system.gpu.")
    has_step = stream.get("has_step", False)
    if not is_system:
        return 0 if has_step else 1
    if is_gpu:
        return 2 if has_step else 3
    return 4 if has_step else 5


@router.get("/jobs/{job_id}/metrics/streams")
def list_metric_streams(
    job_id: str,
    _: models.User = Depends(get_current_user),
) -> List[Dict[str, Any]]:
    metrics_dir = _metrics_dir_for_job(job_id)
    if not os.path.isdir(metrics_dir):
        return []
    return _build_streams(_read_all_points(metrics_dir))


def _shape_point(p: Dict[str, Any]) -> Dict[str, Any]:
    out: Dict[str, Any] = {
        "value": p.get("value"),
        "time_unix_ms": p.get("time_unix_ms"),
    }
    if p.get("step") is not None:
        out["step"] = p["step"]
    if p.get("labels"):
        out["labels"] = p["labels"]
    return out


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

    return {"name": name, "points": [_shape_point(p) for p in filtered]}


@router.get("/jobs/{job_id}/metrics/initial")
def get_initial_metrics(
    job_id: str,
    max_points: int = Query(default=2000, le=10000),
    _: models.User = Depends(get_current_user),
) -> Dict[str, Any]:
    metrics_dir = _metrics_dir_for_job(job_id)
    empty = {"streams": [], "default_metric": None, "default_data": []}
    if not os.path.isdir(metrics_dir):
        return empty

    all_points = _read_all_points(metrics_dir)
    stream_list = _build_streams(all_points)
    if not stream_list:
        return empty

    default_metric = min(stream_list, key=_metric_priority)["name"]

    by_key: Dict[Tuple, List[Dict[str, Any]]] = {}
    for p in all_points:
        if p.get("name") != default_metric:
            continue
        by_key.setdefault(_stream_key(p), []).append(p)

    default_data: List[Dict[str, Any]] = []
    for pts in by_key.values():
        pts.sort(key=lambda p: p.get("time_unix_ms", 0))
        pts = _downsample(pts, max_points)
        default_data.append({
            "labels": pts[0].get("labels") or {},
            "points": [_shape_point(p) for p in pts],
        })

    return {
        "streams": stream_list,
        "default_metric": default_metric,
        "default_data": default_data,
    }
