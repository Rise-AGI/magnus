> **Language / 语言**: **English** · [简体中文](metrics.zh-CN.md)

# Magnus Metrics Guide

This document defines the metrics protocol between Magnus Jobs and the Magnus Metrics Collector.
It is both a requirements document and the authoritative interface specification for subsequent implementations.

This document only defines the protocol itself; it does not define the Python SDK interaction layer, nor does it discuss compatibility strategies for legacy implementations.

The keywords in this document are interpreted as follows:

- `MUST`: Production implementations must satisfy this
- `SHOULD`: Strongly recommended; deviations must have clear justification
- `MAY`: Optional capability

---

## 1. Goals

The Magnus metrics protocol MUST simultaneously satisfy the following goals:

1. **Cross-language**: Jobs may be written in Python, C, C++, Rust, Fortran, or other languages; the protocol MUST NOT depend on a specific language runtime.
2. **Jobs actively adapt to the protocol**: Jobs actively produce metrics according to the Magnus protocol; Magnus does not presuppose hooking into user programs.
3. **Support both time series and step series**: The same metric point may simultaneously carry a physical time coordinate and a step coordinate; queries and displays may project onto either axis.
4. **Unified carrier for system metrics and training metrics**: GPU utilization, CPU utilization, memory usage, loss, lr, tokens, entropy, sharpness, etc. all use the same protocol.
5. **Distributed-friendly**: Single-machine, multi-process, multi-machine multi-GPU, and heterogeneous-rank jobs can all use the same protocol.
6. **Fail-open**: When metrics are unavailable, the Collector is unavailable, or certain runtime modes do not support certain kinds of metrics, the Job MUST still run normally.

---

## 2. Scope

This protocol applies to:

- All structured metrics produced during the runtime of a Magnus Job
- System metrics produced automatically by the Magnus runtime
- Training, inference, simulation, evaluation, and physical experiment metrics actively reported by user programs

This protocol currently does not define:

- Python SDK wrappers
- Specific frontend UI layouts
- Database storage structures
- Internal implementations of aggregation, downsampling, or alerting rules

---

## 3. Core Concepts

### 3.1 Metric Point

A `metric point` is an indivisible single observation value.

A point MUST satisfy:

- Express only one metric value
- The value MUST be a finite numeric value
- Carry at least a real physical time coordinate
- MAY additionally carry a step coordinate

### 3.2 Metric Stream

A `metric stream` is a sequence of metric points sharing the same name, the same label set, and the same step semantics.

The following dimensions jointly determine a stream:

- `name`
- `labels`
- `step_domain`

The same `name` under different labels constitutes a different stream.
The same `name` under a different `step_domain` also constitutes a different stream.
A time-series stream without a step axis is treated as `step_domain = null`, which is different from any stream with a step.

### 3.3 Labels

`labels` are discrete dimensional markers on metric points, used to split streams.

Common labels include:

- `node`
- `host`
- `global_rank`
- `local_rank`
- `device`
- `phase`
- `layer`
- `optimizer_group`

`labels` are only used for low-cardinality, enumerable, stable dimensions.

### 3.4 Time Axis

`time_unix_ms` represents real-world time, in milliseconds, using the Unix epoch.

It is used for:

- Real timeline display
- Time alignment across Jobs or across streams
- System resource metric display
- Ordering and debouncing

### 3.5 Step Axis

`step` represents the logical progression coordinate inside a task.

It is used for:

- Training curves
- Inference token progress
- Simulation iterations
- Optimizer step counts
- Custom algorithm phase progression

`step` is not physical time, nor is it required to linearly correspond to wall clock.

### 3.6 Step Domain

`step_domain` is used to describe the semantics of `step`.

Typical values include:

- `train`
- `optimizer`
- `eval`
- `token`
- `simulation`
- `iteration`
- `epoch`
- `global`

If a point carries a `step`, it `MUST` also carry a `step_domain`.
When not explicitly provided, the default value is `global`.
If a point does not carry a `step`, it `MUST NOT` carry a `step_domain`.

Step values from different `step_domain`s are by default not directly comparable.

---

## 4. Dual-Axis Model

The Magnus metrics protocol adopts a dual-axis model:

- Every point **MUST** carry `time_unix_ms`
- Every point **MAY** additionally carry `step`

`time_unix_ms` and `step` are two independent coordinate axes; neither is derived from the other.
Real time across nodes may have clock skew; strict progression ordering across nodes should not be judged solely on wall clock.

This means a point can take any of the following three legal forms:

### 4.1 Time Series Only

Applicable to pure system metrics or metrics without a logical step.

Examples:

- `system.gpu.utilization`
- `system.cpu.utilization`
- `system.memory.used_bytes`

### 4.2 Time Series + Step Series

Applicable to the vast majority of training and simulation metrics.

Examples:

- `train.loss`
- `train.lr`
- `train.tokens.total`
- `train.attn_entropy`
- `simulation.residual`

This is the recommended form.
Even if a metric is primarily displayed by step, it should still carry real time.

### 4.3 Time Series + Non-Training Step Series

Applicable to other progression logic such as inference, decoding, optimization, and physical solving.

Examples:

- `inference.tokens.generated`
- `solver.energy_error`
- `optimizer.grad_norm`

---

## 5. Data Format

Magnus Metrics Protocol v1 uses JSON Lines as the base exchange format.

- Encoding: UTF-8
- File format: `.jsonl`
- One complete JSON object per line
- Each line ends with `\\n`
- Producers write in append mode
- Single-line size `MUST NOT` exceed 256 KiB

A valid metric point object format is as follows:

```json
{
  "name": "train.loss",
  "kind": "gauge",
  "value": 1.2345,
  "time_unix_ms": 1770000123456,
  "step": 1280,
  "step_domain": "optimizer",
  "unit": "loss",
  "labels": {
    "phase": "train",
    "global_rank": "0",
    "local_rank": "0",
    "device": "cuda:0"
  }
}
```

### 5.1 Field Definitions

| Field | Type | Required | Description |
|------|------|------|------|
| `name` | `string` | Yes | Metric name |
| `kind` | `string` | Yes | Metric type, see below |
| `value` | `number` | Yes | Finite numeric value, `NaN` / `Inf` prohibited |
| `time_unix_ms` | `integer` | Yes | Real time, Unix epoch milliseconds |
| `step` | `integer` | No | Logical step |
| `step_domain` | `string` | No | Step semantic domain; should be provided when `step` is present |
| `unit` | `string` | No | Unit |
| `labels` | `object<string,string>` | No | Label set |

### 5.2 Field Validation Rules

The following rules apply to all production implementations of v1:

| Field | Rule |
|------|------|
| `name` | Regex `^[a-z][a-z0-9_]*(\\.[a-z][a-z0-9_]*)*$`, length `1..128` |
| `kind` | Can only be `gauge` or `counter` |
| `value` | Finite IEEE-754 double-precision numeric value, `NaN` / `+Inf` / `-Inf` prohibited |
| `time_unix_ms` | Non-negative 64-bit integer |
| `step` | Non-negative 64-bit integer |
| `step_domain` | Regex `^[a-z][a-z0-9_]*$`, length `1..64` |
| `unit` | Regex `^[a-z][a-z0-9_]*$`, length `1..32` |
| `labels` key | Regex `^[a-z][a-z0-9_]*$`, length `1..64` |
| `labels` value | Non-empty UTF-8 string, length `1..256` |
| `labels` count | At most `32` labels per point |

### 5.3 `kind`

v1 defines two base types:

- `gauge`
- `counter`

Meanings are as follows:

- `gauge`: An instantaneous value that may fluctuate up or down, e.g. utilization, loss, lr, entropy, memory usage
- `counter`: A monotonically cumulative value, e.g. number of tokens processed, number of samples completed, number of bytes written

From the producer's perspective, a `counter` MUST be monotonically cumulative within the same stream.
If job restarts, rank rebuilds, or internal application resets cause a counter to reset to zero, the producer should start a new stream by changing `labels` or `step_domain`.
The Collector MUST tolerate occasional backward inputs, but MUST NOT interpret them as normal behavior of the same healthy stream.

### 5.4 `value`

`value` MUST satisfy:

- Be a single numeric value
- Be a finite real number
- MUST NOT be a string, array, object, boolean, or null

The following forms are not legal in v1:

- `{"last": 1.2, "mean": 1.3}`
- `{"layer1": 3.2, "layer2": 4.5}`
- `[1.2, 1.3, 1.4]`

If multi-layer or multi-device metrics need to be expressed, they MUST be split into multiple points, distinguished via `labels`.

### 5.5 Monotonicity and Reset Rules

The following rules ensure that streams are queryable, aggregatable, and plottable:

1. Within the same stream, `step` `MUST` be non-decreasing when present.
2. Within the same `counter` stream, `value` `MUST` be non-decreasing.
3. Within the same file, `time_unix_ms` `SHOULD` be non-decreasing.
4. If the application needs to reset the step count, it `MUST` start a new stream rather than rolling back within the original stream.
5. If the application needs to reset a counter, it `MUST` start a new stream rather than zeroing it within the original stream.

Legal ways to start a new stream include:

- Changing `labels`
- Changing `step_domain`

It is recommended to use low-cardinality labels `attempt` or `segment` to represent a new producer lifecycle segment.

### 5.6 Deduplication and Delivery Semantics

v1 defaults to at-least-once semantics.

This means:

1. Producers `SHOULD` try to avoid emitting duplicate points.
2. Collectors `MUST` be able to tolerate duplicate points.
3. The protocol `MUST NOT` assume exactly-once.
4. Arrival order across files `MAY` be out of order; the Collector `MUST` tolerate this.

---

## 6. Naming Conventions

### 6.1 Metric Name Rules

`name` should adopt a stable dot-delimited path:

```text
system.gpu.utilization
system.gpu.memory.used_bytes
system.cpu.utilization
train.loss
train.lr
train.tokens.total
train.attn_entropy
simulation.energy_error
physics.hamiltonian.residual
```

Constraints are as follows:

- Use lowercase letters, digits, and underscores
- Use `.` to separate segments
- No spaces
- No units
- Do not encode specific rank, device, or layer; such information should be placed in `labels`
- The `magnus.` prefix is reserved for Magnus internal implementations

### 6.2 Recommended Prefixes

The following prefixes are recommended:

- `system.`: Runtime, host, container, device resource metrics
- `train.`: Training-process metrics
- `inference.`: Inference-process metrics
- `eval.`: Evaluation-process metrics
- `simulation.`: Simulation-process metrics
- `app.`: Application-custom metrics

Magnus does not maintain a fixed whitelist.
Any legal metric name should be accepted by the protocol.

---

## 7. Unit Conventions

`unit` is an optional field, but recommended to provide.

Recommended units include:

- `percent`
- `bytes`
- `mib`
- `gib`
- `seconds`
- `milliseconds`
- `tokens`
- `tokens_per_second`
- `samples`
- `loss`
- `entropy`

A unit only describes the value itself; it does not change metric identity.
The same stream should not switch units during runtime.

Production implementations should preferentially follow these conventions:

- Utilization uniformly uses `percent`, with a recommended range of `[0, 100]`
- Memory, storage, and traffic preferentially use `bytes`
- Durations preferentially use `seconds` or `milliseconds`
- Cumulative tokens preferentially use `tokens`
- Throughput preferentially uses explicit rate units such as `tokens_per_second`, `samples_per_second`

If a cumulative quantity can be naturally expressed as a counter, a counter `SHOULD` be reported in preference to reporting only the rate.

---

## 8. Label Conventions

### 8.1 Label Values

Both the key and value of `labels` MUST be strings.

Example:

```json
{
  "labels": {
    "phase": "train",
    "global_rank": "7",
    "node": "node-03",
    "device": "cuda:3"
  }
}
```

### 8.2 Label Cardinality Constraints

`labels` MUST be low-cardinality dimensions.

Typical permitted dimensions:

- rank
- node
- host
- device
- phase
- layer
- optimizer_group
- attempt
- segment

Typical disallowed dimensions:

- Prompt content
- Raw user input
- The full set of file paths
- Token text
- The full set of sample IDs
- Timestamp strings
- step itself

If a field continuously produces new unique values over time, it typically should not go into `labels`.

### 8.3 Reserved Labels

The following label names are reserved for Magnus and distributed environment use:

- `node`
- `host`
- `pid`
- `global_rank`
- `local_rank`
- `world_size`
- `device`
- `phase`
- `attempt`
- `segment`

Applications may still fill these labels, but the meaning MUST match the name.

---

## 9. Transport Protocol

v1 uses file-directory discovery + JSONL append-writes.

The Magnus runtime injects the following environment variables into the Job:

| Variable | Description |
|------|------|
| `MAGNUS_METRICS_PROTO` | Current protocol version; v1 is `metrics.v1` |
| `MAGNUS_METRICS_DIR` | Metrics write directory |
| `MAGNUS_JOB_ID` | Current Job ID |

Write constraints on the Job side are as follows:

1. After the Job discovers `MAGNUS_METRICS_DIR`, it may decide whether to enable metrics writing.
2. Producers `SHOULD` only write files directly at the top level of `MAGNUS_METRICS_DIR`; v1 does not define subdirectory scanning.
3. Each producer `MUST` write only its own exclusive `.jsonl` file.
4. In multi-process, multi-rank scenarios, different processes `MUST NOT` share the same output file.
5. Writes `MUST` use append mode.
6. Producers `MUST NOT` truncate, rewrite, or delete existing metrics files.
7. Each line `MUST` be a complete JSON object and cannot span lines.
8. A record is considered committed only after the newline character is written.
9. Producers `SHOULD` proactively flush at reasonable frequency to avoid lingering too long in user-space buffers.

Metrics file names `SHOULD` satisfy:

- Regex `^[A-Za-z0-9][A-Za-z0-9._-]*\\.jsonl$`
- Do not start with `.magnus_` or `_magnus_`
- Naming is stable, predictable, and consistent with producer identity

The Collector side `MUST` follow these rules:

1. Only read regular files.
2. `MUST` ignore symbolic links, device files, FIFOs, and sockets.
3. `MUST` ignore trailing half-lines that do not end with a newline, until they appear in full.
4. `MUST` ignore reserved files that do not conform to naming rules.

Recommended file naming:

```text
${MAGNUS_METRICS_DIR}/rank-0.jsonl
${MAGNUS_METRICS_DIR}/rank-1.jsonl
${MAGNUS_METRICS_DIR}/system.jsonl
${MAGNUS_METRICS_DIR}/worker-3.jsonl
```

Collector implementation details are out of scope for this document, but the Collector MUST treat these files as append-only input streams.

---

## 10. Failure Semantics

The metrics system defaults to fail-open semantics.

Specific requirements are as follows:

1. When `MAGNUS_METRICS_DIR` does not exist, the Job MUST still be able to continue running.
2. When a metrics file cannot be written, the Job by default SHOULD NOT fail.
3. The Collector being offline, the Collector being delayed, or certain metrics having no consumer MUST NOT affect the Job's main flow.
4. local mode, Docker mode, and SLURM mode may differ in system metric coverage, but the user-space metrics protocol MUST remain consistent.

In other words, metrics are an additional capability, not a hard dependency of the Job's main logic.

Production implementations should further satisfy:

- When the metrics directory does not exist, the Emitter `MUST` enter a disabled state or return a recoverable error, but the main task `MUST NOT` exit because of it
- When a metric line fails validation, the Emitter `SHOULD` discard that point and continue with subsequent points
- When the Collector fails to process, it `MUST NOT` reverse-impact the Job process

---

## 11. Distributed Conventions

Distributed Jobs MUST follow these principles:

1. Each rank independently writes its own points.
2. Rank identity is expressed via `labels`, not by modifying `name`.
3. Jobs are not required to perform global aggregation before writing.
4. Aggregation should be done by the Magnus query layer or UI layer.

If the same logical metric has both a local step and a global step, they `MUST` use different `step_domain`s rather than being mixed into the same stream.

Recommended minimum label set:

- `global_rank`
- `local_rank`
- `world_size`
- `node`
- `device`

Example:

```json
{
  "name": "train.loss",
  "kind": "gauge",
  "value": 1.108,
  "time_unix_ms": 1770000123456,
  "step": 3200,
  "step_domain": "optimizer",
  "labels": {
    "global_rank": "3",
    "local_rank": "1",
    "world_size": "8",
    "node": "node-02",
    "device": "cuda:1",
    "phase": "train"
  }
}
```

---

## 12. Derived Metrics and Windowed Metrics

v1 does not allow placing aggregation objects in the `value` of a point.

If an application wants to express windowed statistics, it `MUST` expand them into independent scalar streams.

Recommended approaches:

- `train.loss.window_mean`
- `train.loss.window_max`
- `train.grad_norm.window_mean`

Window information `SHOULD` be expressed via low-cardinality labels, for example:

```json
{
  "name": "train.loss.window_mean",
  "kind": "gauge",
  "value": 1.203,
  "time_unix_ms": 1770000123456,
  "step": 1200,
  "step_domain": "optimizer",
  "unit": "loss",
  "labels": {
    "window_steps": "100",
    "phase": "train"
  }
}
```

Similarly, per-layer metrics `MUST` also be split into multiple points, rather than stuffing the entire layer dictionary into a single point.

---

## 13. Recommended Metric Names

The table below gives commonly-used canonical names recommended by v1.

| Category | Recommended Name |
|------|--------|
| GPU | `system.gpu.utilization` |
| GPU memory | `system.gpu.memory.used_bytes` |
| CPU | `system.cpu.utilization` |
| Memory | `system.memory.used_bytes` |
| Loss | `train.loss` |
| Learning rate | `train.lr` |
| Cumulative tokens | `train.tokens.total` |
| Token throughput | `train.tokens.throughput` |
| Gradient norm | `train.grad_norm` |
| Parameter norm | `train.param_norm` |
| Attention entropy | `train.attn_entropy` |
| Sharpness | `train.sharpness` |
| Evaluation loss | `eval.loss` |
| Inference cumulative tokens | `inference.tokens.total` |
| Physics simulation residual | `simulation.residual` |

These names are recommended conventions, not a whitelist.

---

## 14. Canonical Examples

### 14.1 GPU Utilization

```json
{
  "name": "system.gpu.utilization",
  "kind": "gauge",
  "value": 87.0,
  "time_unix_ms": 1770000123456,
  "unit": "percent",
  "labels": {
    "device": "cuda:0",
    "node": "node-01"
  }
}
```

### 14.2 CPU Utilization

```json
{
  "name": "system.cpu.utilization",
  "kind": "gauge",
  "value": 63.2,
  "time_unix_ms": 1770000123456,
  "unit": "percent",
  "labels": {
    "node": "node-01"
  }
}
```

### 14.3 Training Loss

```json
{
  "name": "train.loss",
  "kind": "gauge",
  "value": 1.2841,
  "time_unix_ms": 1770000123456,
  "step": 1200,
  "step_domain": "optimizer",
  "unit": "loss",
  "labels": {
    "phase": "train",
    "global_rank": "0"
  }
}
```

### 14.4 Total Tokens Processed

```json
{
  "name": "train.tokens.total",
  "kind": "counter",
  "value": 9830400,
  "time_unix_ms": 1770000123456,
  "step": 1200,
  "step_domain": "optimizer",
  "unit": "tokens",
  "labels": {
    "phase": "train",
    "global_rank": "0"
  }
}
```

### 14.5 Attention Entropy

```json
{
  "name": "train.attn_entropy",
  "kind": "gauge",
  "value": 3.817,
  "time_unix_ms": 1770000123456,
  "step": 1200,
  "step_domain": "optimizer",
  "unit": "entropy",
  "labels": {
    "phase": "train",
    "layer": "transformer.h.11.attn"
  }
}
```

### 14.6 Physics Simulation Residual

```json
{
  "name": "simulation.energy_error",
  "kind": "gauge",
  "value": 0.000023,
  "time_unix_ms": 1770001123456,
  "step": 480,
  "step_domain": "iteration",
  "labels": {
    "phase": "solver"
  }
}
```

---

## 15. Minimum Implementation Requirements on the Job Side

A Job in any language is considered protocol-compatible as long as it does the following:

1. Reads `MAGNUS_METRICS_DIR`
2. Creates its own `.jsonl` output file
3. Writes legal JSON lines when needed
4. By default does not let the main task exit when writing fails

Therefore, the minimum implementation threshold for Python, C, Rust, and Fortran should be the same.

The protocol does not require:

- Using the Magnus SDK
- Using HTTP
- Using a socket
- Hooking into model internals
- Implementing system metrics

---

## 16. Content Explicitly Not Supported in v1

The following content does not belong to v1:

- Point values being objects, arrays, or nested structures
- Carrying a batch of layer values in a single point
- Relying on specific framework hooks to work
- Treating log text as metrics
- Writing UI chart configurations into the metrics stream
- Hardcoding Collector addresses into task logic
- Reusing an old stream by rolling back step or zeroing a counter
- Multiple producers writing the same file concurrently

---

## 17. Overview of the Current Landed Implementation

The following describes Magnus's first production implementation of this protocol (v1 landing), to help subsequent developers get up to speed quickly.

### 17.1 Architecture Overview

```
┌─────────────────────────────────────────────────┐
│  SLURM compute node (wrapper.py)                  │
│                                                   │
│  ┌──────────────┐    ┌──────────────────────┐     │
│  │ User Job process│    │ system metrics sidecar│     │
│  │ (any language)  │    │ (Python daemon thread)│     │
│  │               │    │                      │     │
│  │ write *.jsonl ──▶ │ write system.jsonl ──────▶ │
│  └──────────────┘    └──────────────────────┘     │
│         │                      │                   │
│         └──────────┬───────────┘                   │
│                    ▼                               │
│         $MAGNUS_METRICS_DIR/                       │
│         (bind mount → host disk)                   │
└─────────────────────────────────────────────────┘
                     │
                     ▼ (shared filesystem)
┌─────────────────────────────────────────────────┐
│  Magnus head node                                  │
│                                                   │
│  routers/metrics.py                               │
│  ├─ GET /jobs/{id}/metrics/streams  (list streams) │
│  └─ GET /jobs/{id}/metrics/query    (query points) │
│         │                                         │
│         ▼ read JSONL files directly, no DB        │
│                                                   │
│  Frontend MetricsChart component                   │
│  └─ recharts line chart, sidebar picks metric      │
└─────────────────────────────────────────────────┘
```

### 17.2 System Metrics Collection

In SLURM mode, wrapper.py has a built-in daemon thread (`_metrics_sidecar`) that samples every 5 seconds:

- `system.gpu.utilization`: via `nvidia-smi --query-gpu=utilization.gpu`
- `system.gpu.memory.used_bytes`: via `nvidia-smi --query-gpu=memory.used`, converting MiB to bytes
- `system.cpu.utilization`: via two samples of `/proc/stat` to compute delta

Writes to `$MAGNUS_METRICS_DIR/system.jsonl`, strictly following this protocol.

The sidecar is fail-open end-to-end: if nvidia-smi does not exist (CPU-only tasks) or `/proc/stat` is not readable, it silently skips. The sidecar starts before the container starts and stops after the container exits (`threading.Event` + `join(timeout=5)`).

Docker local mode does not start the sidecar (no wrapper.py), but environment variables are still injected as usual; user code may report on its own.

### 17.3 Environment Variables

The Magnus runtime injects into the Job:

| Variable | Value | Injection method |
|------|---|---------|
| `MAGNUS_METRICS_DIR` | `$MAGNUS_HOME/workspace/metrics` | SLURM: `APPTAINERENV_*`; Docker: `-e` |
| `MAGNUS_METRICS_PROTO` | `metrics.v1` | Same as above |
| `MAGNUS_JOB_ID` | Job UUID | Already existed, no addition needed |

The host path corresponding to `MAGNUS_METRICS_DIR` is `{workspace}/jobs/{job_id}/metrics/`, mapped into the container via bind mount. Data is actually written to the host disk (not to the container's writable layer), not occupying overlay quota.

### 17.4 Storage and Lifecycle

- **No database**. JSONL files are the data source; the API reads and parses on demand.
- **The metrics/ directory is not deleted when the Job is cleaned up**, same policy as `slurm/output.txt`. After the Job ends, users can still review historical metrics.
- Typical data volume: 8 GPUs × 3 metrics × 5-second interval × 24 hours ≈ 410k lines ≈ 50 MB, negligible for a shared filesystem.

### 17.5 API

Two endpoints, both requiring Bearer token authentication:

**`GET /api/jobs/{job_id}/metrics/streams`**

Scans `metrics/*.jsonl` and returns a deduplicated list of streams:

```json
[
  {
    "name": "system.gpu.utilization",
    "kind": "gauge",
    "unit": "percent",
    "step_domain": null,
    "labels": {"device": "cuda:0", "node": "node-01"},
    "point_count": 1234
  }
]
```

**`GET /api/jobs/{job_id}/metrics/query?name=...&labels=...&max_points=2000`**

Filters by name, labels, step_domain, and time range; uniformly downsamples when exceeding the limit. Returns:

```json
{
  "name": "system.gpu.utilization",
  "points": [
    {"value": 87.0, "time_unix_ms": 1770000123456, "labels": {"device": "cuda:0"}}
  ]
}
```

### 17.6 Frontend

The "Metrics" tab on the Job Detail page, based on recharts:

- Left sidebar groups available metric names by system / train / other
- Right line chart; different label combinations of the same metric (e.g. multi-GPU) automatically render as multiple lines
- Auto-polls and refreshes in Running state
- Empty-data state has guidance copy

### 17.7 Known Limitations and Evolution Directions

| Limitation | Impact | Evolution path |
|------|------|---------|
| API reads entire files | OK for tens of GPUs; needs optimization at thousand-GPU supercomputer scale | Introduce Collector async ingest (time-series DB) or seek-based reading |
| Frontend views one metric at a time | Cannot simultaneously compare GPU utilization and memory | Multi-chart dashboard view |
| No step-axis toggle | Training metrics can only be viewed by time | Add time/step axis toggle on frontend |
| Docker mode has no system metrics | No GPU monitoring in local development | Add Docker entrypoint wrapper or sidecar container |
| No alerting rules | Cannot automatically alert | Future version defines alert threshold syntax |

---

## 18. One-Sentence Principle

The core principle of Magnus Metrics Protocol v1 is:

**Jobs actively report metrics as a minimal, stable, cross-language JSONL point stream; each point faces both real time and logical step coordinate systems simultaneously.**
