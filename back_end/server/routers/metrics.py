# back_end/server/routers/metrics.py
import os
import io
import json
import asyncio
import logging
import glob
from typing import List, Optional, Dict, Any, Tuple

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response

# Matplotlib must use a non-GUI backend before pyplot is imported.
# This is a process-wide setting; importing this router at FastAPI startup
# is what locks the backend in. Do NOT move the use("Agg") call below
# pyplot import — the default backend may try to talk to an X server.
import matplotlib  # noqa: E402
matplotlib.use("Agg")  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402

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
    max_points: int = Query(default=2000, ge=1, le=10000),
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


# === Chart Rendering ===

# Mirrors front_end/src/components/jobs/metrics-chart.tsx STREAM_COLORS so that
# server-rendered PNGs share the same visual semantics as the web UI.
_CHART_COLORS = (
    "#60a5fa", "#34d399", "#fbbf24", "#f87171", "#a78bfa",
    "#2dd4bf", "#fb923c", "#e879f9", "#38bdf8", "#4ade80",
)


def _labels_legend_key(labels: Dict[str, str]) -> str:
    if not labels:
        return "default"
    return ",".join(f"{k}={v}" for k, v in sorted(labels.items()))


def _series_has_step(points: List[Dict[str, Any]]) -> bool:
    for p in points:
        if p.get("step") is not None:
            return True
    return False


def _render_chart_png(
    series: List[Tuple[str, List[Dict[str, Any]]]],
    name: str,
    unit: Optional[str],
) -> bytes:
    """Render a metric chart as PNG. Pure CPU-bound; call via asyncio.to_thread.

    series: list of (legend_label, sorted_points) tuples — one per labels combo.
    """
    fig, ax = plt.subplots(figsize=(9.0, 4.5), dpi=110)
    try:
        any_step = any(_series_has_step(pts) for _, pts in series)
        x_axis = "step" if any_step else "time"

        plotted_legends: List[str] = []
        for idx, (legend, pts) in enumerate(series):
            if not pts:
                continue
            color = _CHART_COLORS[idx % len(_CHART_COLORS)]
            if x_axis == "step":
                xs = [p.get("step") for p in pts if p.get("step") is not None]
                ys = [p["value"] for p in pts if p.get("step") is not None]
            else:
                xs = [p.get("time_unix_ms") for p in pts]
                ys = [p["value"] for p in pts]
            if not xs:
                continue
            ax.plot(xs, ys, color=color, linewidth=1.5, label=legend)
            plotted_legends.append(legend)

        title = f"{name} [{unit}]" if unit else name
        ax.set_title(title)
        ax.set_xlabel("step" if x_axis == "step" else "time (unix ms)")
        ax.set_ylabel(unit or "value")
        ax.grid(True, linestyle="--", alpha=0.3)
        # Show legend whenever there are multiple plotted series, or a single
        # plotted series whose label is non-default. Decision is based on
        # series that actually got plotted, not array position — empty series
        # earlier in the list must not suppress the legend for later ones.
        if plotted_legends and (len(plotted_legends) > 1 or plotted_legends[0] != "default"):
            ax.legend(loc="best", fontsize=8)

        buf = io.BytesIO()
        fig.tight_layout()
        fig.savefig(buf, format="png")
        return buf.getvalue()
    finally:
        plt.close(fig)


def _no_data_png(name: str) -> bytes:
    fig, ax = plt.subplots(figsize=(9.0, 4.5), dpi=110)
    try:
        ax.set_title(name)
        ax.text(
            0.5, 0.5, "no data",
            transform=ax.transAxes,
            ha="center", va="center",
            fontsize=14, color="#71717a",
        )
        ax.set_xticks([])
        ax.set_yticks([])
        for spine in ax.spines.values():
            spine.set_visible(False)
        buf = io.BytesIO()
        fig.tight_layout()
        fig.savefig(buf, format="png")
        return buf.getvalue()
    finally:
        plt.close(fig)


@router.get("/jobs/{job_id}/metrics/render")
async def render_metric_chart(
    job_id: str,
    name: str,
    labels: Optional[str] = None,
    step_domain: Optional[str] = None,
    since_ms: Optional[int] = None,
    until_ms: Optional[int] = None,
    max_points: int = Query(default=2000, ge=1, le=10000),
    format: str = Query(default="png"),
    _: models.User = Depends(get_current_user),
) -> Response:
    """Render a metric chart server-side and return image bytes.

    Fail-open: when the metric / job has no data, returns a "no data" PNG
    rather than 404 — friendlier for users who paste this into a notebook
    or grab it via the SDK.
    """
    if format != "png":
        return Response(
            content=json.dumps({"detail": f"Unsupported format: {format}"}),
            status_code=400,
            media_type="application/json",
        )

    metrics_dir = _metrics_dir_for_job(job_id)
    parsed_labels: Optional[Dict[str, str]] = None
    if labels:
        try:
            parsed_labels = json.loads(labels)
        except json.JSONDecodeError:
            parsed_labels = None

    if not os.path.isdir(metrics_dir):
        png = await asyncio.to_thread(_no_data_png, name)
        return Response(content=png, media_type="image/png")

    # 在这个 async 端点里，_read_all_points 会 glob + 逐行 json.loads 整个 metrics 目录，
    # 长跑 job 可达数十 MB —— 必须丢线程池，否则阻塞事件循环（本函数其余重活已都 to_thread）。
    all_points = await asyncio.to_thread(_read_all_points, metrics_dir)
    unit: Optional[str] = None
    by_labels: Dict[Tuple, List[Dict[str, Any]]] = {}
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
        if unit is None and p.get("unit"):
            unit = p["unit"]
        key = tuple(sorted((p.get("labels") or {}).items()))
        by_labels.setdefault(key, []).append(p)

    if not by_labels:
        png = await asyncio.to_thread(_no_data_png, name)
        return Response(content=png, media_type="image/png")

    series: List[Tuple[str, List[Dict[str, Any]]]] = []
    for key, pts in by_labels.items():
        pts.sort(key=lambda p: p.get("time_unix_ms", 0))
        pts = _downsample(pts, max_points)
        legend = _labels_legend_key(dict(key))
        series.append((legend, pts))

    png = await asyncio.to_thread(_render_chart_png, series, name, unit)
    return Response(content=png, media_type="image/png")


@router.get("/jobs/{job_id}/metrics/initial")
def get_initial_metrics(
    job_id: str,
    max_points: int = Query(default=2000, ge=1, le=10000),
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
