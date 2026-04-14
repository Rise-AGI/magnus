> **Language / 语言**: [English](metrics.md) · **简体中文**

# Magnus Metrics Guide

本文档定义 Magnus Job 与 Magnus Metrics Collector 之间的指标协议。
它是需求文档，也是后续实现的权威接口说明。

本文档只定义协议本身，不定义 Python SDK 交互层，不讨论旧实现兼容策略。

本文档中的关键词按以下含义解释：

- `MUST` / `必须`：生产实现必须满足
- `SHOULD` / `应`：强烈推荐，偏离时必须有明确理由
- `MAY` / `可以`：可选能力

---

## 1. 目标

Magnus 指标协议必须同时满足以下目标：

1. **跨语言**：Job 可以由 Python、C、C++、Rust、Fortran 或其他语言编写，协议不能依赖特定语言运行时。
2. **任务主动适配协议**：Job 主动按照 Magnus 协议产出指标；Magnus 不以 hook 用户程序为前提。
3. **同时支持时间序列与 step 序列**：同一个指标点可以同时拥有物理时间坐标和 step 坐标，查询与展示时可按任一轴投影。
4. **统一承载系统指标与训练指标**：GPU 利用率、CPU 利用率、显存使用量、loss、lr、tokens、entropy、sharpness 等都使用同一协议。
5. **分布式友好**：单机、多进程、多机多卡、异构 rank 的作业都可以使用同一协议。
6. **Fail-open**：指标不可用、Collector 不可用、某些运行模式不支持某类指标时，Job 仍然必须正常运行。

---

## 2. 适用范围

本协议适用于：

- Magnus Job 在运行过程中产生的所有结构化指标
- Magnus 运行时自动产生的系统指标
- 用户程序主动上报的训练、推理、模拟、评估、物理实验类指标

本协议当前不定义：

- Python SDK 封装
- 前端 UI 具体布局
- 数据库存储结构
- 聚合、降采样、告警规则的内部实现

---

## 3. 核心概念

### 3.1 Metric Point

一个 `metric point` 是一个不可再分的单点观测值。

一个点必须满足：

- 只表达一个指标值
- 值必须是一个有限数值
- 至少有真实物理时间坐标
- 可以额外带有 step 坐标

### 3.2 Metric Stream

一个 `metric stream` 是一组同名、同标签集合、同 step 语义的指标点序列。

下列维度共同决定一个 stream：

- `name`
- `labels`
- `step_domain`

同一个 `name` 在不同标签下是不同 stream。
同一个 `name` 在不同 `step_domain` 下也是不同 stream。
没有 step 轴的时间序列 stream 视为 `step_domain = null`，与任意带 step 的 stream 不同。

### 3.3 Labels

`labels` 是对指标点的离散维度标记，用于拆分 stream。

常见 labels 包括：

- `node`
- `host`
- `global_rank`
- `local_rank`
- `device`
- `phase`
- `layer`
- `optimizer_group`

`labels` 只用于低基数、可枚举、稳定的维度。

### 3.4 Time Axis

`time_unix_ms` 表示真实世界时间，单位为毫秒，使用 Unix epoch。

它用于：

- 真实时间线展示
- 跨 Job 或跨 stream 的时间对齐
- 系统资源指标展示
- 排序与去抖

### 3.5 Step Axis

`step` 表示任务内部的逻辑推进坐标。

它用于：

- 训练曲线
- 推理 token 进度
- 模拟迭代
- 优化器步数
- 自定义算法阶段推进

`step` 不是物理时间，也不要求与 wall clock 线性对应。

### 3.6 Step Domain

`step_domain` 用于说明 `step` 的语义。

典型取值包括：

- `train`
- `optimizer`
- `eval`
- `token`
- `simulation`
- `iteration`
- `epoch`
- `global`

如果一个点带有 `step`，则它 `MUST` 同时带有 `step_domain`。
未显式提供时，默认值为 `global`。
如果一个点没有 `step`，则它 `MUST NOT` 带有 `step_domain`。

不同 `step_domain` 之间的 step 数值默认不可直接比较。

---

## 4. 双轴模型

Magnus 指标协议采用双轴模型：

- 每个点**必须**带有 `time_unix_ms`
- 每个点**可以**额外带有 `step`

`time_unix_ms` 与 `step` 是两个独立坐标轴，任何一方都不从另一方派生。
跨节点真实时间可能存在时钟偏移；跨节点的严格推进顺序不应仅依赖 wall clock 判断。

这意味着一个点可以属于以下三种合法形态：

### 4.1 仅时间序列

适用于纯系统指标或没有逻辑 step 的指标。

示例：

- `system.gpu.utilization`
- `system.cpu.utilization`
- `system.memory.used_bytes`

### 4.2 时间序列 + Step 序列

适用于绝大多数训练与模拟指标。

示例：

- `train.loss`
- `train.lr`
- `train.tokens.total`
- `train.attn_entropy`
- `simulation.residual`

这是推荐形态。
即使一个指标主要按 step 展示，也仍然应该带真实时间。

### 4.3 时间序列 + 非训练 Step 序列

适用于推理、解码、优化、物理求解等其他推进逻辑。

示例：

- `inference.tokens.generated`
- `solver.energy_error`
- `optimizer.grad_norm`

---

## 5. 数据格式

Magnus Metrics Protocol v1 使用 JSON Lines 作为基础交换格式。

- 编码：UTF-8
- 文件格式：`.jsonl`
- 每行一个完整 JSON 对象
- 每行末尾以 `\\n` 结束
- 生产者按追加方式写入
- 单行大小 `MUST NOT` 超过 256 KiB

一个合法的指标点对象格式如下：

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

### 5.1 字段定义

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `name` | `string` | 是 | 指标名 |
| `kind` | `string` | 是 | 指标类型，见下文 |
| `value` | `number` | 是 | 有限数值，禁止 `NaN` / `Inf` |
| `time_unix_ms` | `integer` | 是 | 真实时间，Unix epoch 毫秒 |
| `step` | `integer` | 否 | 逻辑 step |
| `step_domain` | `string` | 否 | step 语义域；有 `step` 时应提供 |
| `unit` | `string` | 否 | 单位 |
| `labels` | `object<string,string>` | 否 | 标签集合 |

### 5.2 字段校验规则

以下规则适用于 v1 的所有生产实现：

| 字段 | 规则 |
|------|------|
| `name` | 正则 `^[a-z][a-z0-9_]*(\\.[a-z][a-z0-9_]*)*$`，长度 `1..128` |
| `kind` | 只能是 `gauge` 或 `counter` |
| `value` | 有限 IEEE-754 双精度数值，禁止 `NaN` / `+Inf` / `-Inf` |
| `time_unix_ms` | 非负 64 位整数 |
| `step` | 非负 64 位整数 |
| `step_domain` | 正则 `^[a-z][a-z0-9_]*$`，长度 `1..64` |
| `unit` | 正则 `^[a-z][a-z0-9_]*$`，长度 `1..32` |
| `labels` key | 正则 `^[a-z][a-z0-9_]*$`，长度 `1..64` |
| `labels` value | 非空 UTF-8 字符串，长度 `1..256` |
| `labels` count | 单点最多 `32` 个标签 |

### 5.3 `kind`

v1 定义两种基础类型：

- `gauge`
- `counter`

含义如下：

- `gauge`：可上下波动的即时值，例如利用率、loss、lr、entropy、显存占用
- `counter`：单调累计值，例如已处理 token 数、已完成样本数、已写出字节数

生产者视角下，`counter` 在同一 stream 内必须单调累计。
如果作业重启、rank 重建或应用内部重置导致 counter 归零，生产者应通过改变 `labels` 或 `step_domain` 启动新 stream。
Collector 必须容忍偶发回退输入，但不应把它解释为同一健康 stream 的正常行为。

### 5.4 `value`

`value` 必须满足：

- 是单个数值
- 是有限实数
- 不能是字符串、数组、对象、布尔值、空值

下列形式在 v1 中不合法：

- `{"last": 1.2, "mean": 1.3}`
- `{"layer1": 3.2, "layer2": 4.5}`
- `[1.2, 1.3, 1.4]`

如果需要表达多层或多设备指标，必须拆成多个点，并使用 `labels` 区分。

### 5.5 单调性与重置规则

以下规则用于保证 stream 可查询、可聚合、可绘图：

1. 同一个 stream 内，`step` 在出现时 `MUST` 非递减。
2. 同一个 `counter` stream 内，`value` `MUST` 非递减。
3. 同一个文件内，`time_unix_ms` `SHOULD` 非递减。
4. 如果应用需要重置 step 计数，`MUST` 启动一个新 stream，而不是在原 stream 内回退。
5. 如果应用需要重置 counter，`MUST` 启动一个新 stream，而不是在原 stream 内归零。

启动新 stream 的合法方式包括：

- 改变 `labels`
- 改变 `step_domain`

推荐使用低基数标签 `attempt` 或 `segment` 表示新的 producer 生命周期片段。

### 5.6 去重与投递语义

v1 默认采用 at-least-once 语义。

这意味着：

1. 生产者 `SHOULD` 尽量避免重复发点。
2. Collector `MUST` 能容忍重复点。
3. 协议 `MUST NOT` 假设 exactly-once。
4. 跨文件到达顺序 `MAY` 乱序，Collector `MUST` 容忍。

---

## 6. 命名规范

### 6.1 指标名规则

`name` 应采用稳定的点分路径：

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

约束如下：

- 使用小写字母、数字、下划线
- 分段使用 `.` 分隔
- 不带空格
- 不带单位
- 不编码具体 rank、device、layer，这些信息应放入 `labels`
- `magnus.` 前缀保留给 Magnus 内部实现

### 6.2 推荐前缀

推荐使用以下前缀：

- `system.`：运行时、宿主机、容器、设备资源指标
- `train.`：训练过程指标
- `inference.`：推理过程指标
- `eval.`：评估过程指标
- `simulation.`：模拟过程指标
- `app.`：应用自定义指标

Magnus 不维护固定白名单。
任何合法指标名都应被协议接受。

---

## 7. 单位规范

`unit` 是可选字段，但推荐提供。

推荐单位包括：

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

单位只描述数值本身，不改变指标身份。
同一 stream 不应在运行中切换单位。

生产实现应优先遵循以下规范：

- 利用率统一使用 `percent`，取值范围推荐为 `[0, 100]`
- 内存、存储、流量优先使用 `bytes`
- 时间长度优先使用 `seconds` 或 `milliseconds`
- 累计 token 优先使用 `tokens`
- 吞吐率优先使用 `tokens_per_second`、`samples_per_second` 一类显式速率单位

如果一个累计量可以自然表达为 counter，则 `SHOULD` 优先上报 counter，而不是只上报速率。

---

## 8. 标签规范

### 8.1 标签值

`labels` 的 key 和 value 都必须是字符串。

示例：

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

### 8.2 标签基数约束

`labels` 必须是低基数维度。

允许的典型维度：

- rank
- node
- host
- device
- phase
- layer
- optimizer_group
- attempt
- segment

不允许的典型维度：

- prompt 内容
- 用户输入原文
- 文件路径全集
- token 文本
- 样本 ID 全量集合
- 时间戳字符串
- step 本身

如果一个字段随时间持续产生新的唯一值，它通常不应该进入 `labels`。

### 8.3 保留标签

以下标签名保留给 Magnus 和分布式环境使用：

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

应用仍可填写这些标签，但含义必须与名称一致。

---

## 9. 传输协议

v1 采用文件目录发现 + JSONL 追加写入。

Magnus 运行时向 Job 注入以下环境变量：

| 变量 | 说明 |
|------|------|
| `MAGNUS_METRICS_PROTO` | 当前协议版本，v1 为 `metrics.v1` |
| `MAGNUS_METRICS_DIR` | 指标写入目录 |
| `MAGNUS_JOB_ID` | 当前 Job ID |

Job 侧的写入约束如下：

1. Job 发现 `MAGNUS_METRICS_DIR` 后，可以决定是否启用指标写入。
2. 生产者 `SHOULD` 只在 `MAGNUS_METRICS_DIR` 直接子层级写入文件；v1 不定义子目录扫描。
3. 每个 producer `MUST` 只写自己独占的 `.jsonl` 文件。
4. 多进程、多 rank 场景下，不同进程 `MUST NOT` 共享同一个输出文件。
5. 写入 `MUST` 采用追加模式。
6. 生产者 `MUST NOT` 截断、重写、删除已有指标文件。
7. 每一行 `MUST` 是完整 JSON 对象，不能跨行。
8. 一条记录只有在换行符写入完成后才视为提交成功。
9. 生产者 `SHOULD` 在合理频率上主动 flush，避免长时间只停留在用户态缓冲区。

指标文件命名 `SHOULD` 满足：

- 正则 `^[A-Za-z0-9][A-Za-z0-9._-]*\\.jsonl$`
- 不以 `.magnus_` 或 `_magnus_` 开头
- 命名稳定、可预测、与 producer 身份一致

Collector 侧 `MUST` 遵循以下规则：

1. 只读取常规文件。
2. `MUST` 忽略符号链接、设备文件、FIFO、socket。
3. `MUST` 忽略未以换行结束的尾部半行，直到其完整出现。
4. `MUST` 忽略不符合命名规则的保留文件。

推荐文件命名：

```text
${MAGNUS_METRICS_DIR}/rank-0.jsonl
${MAGNUS_METRICS_DIR}/rank-1.jsonl
${MAGNUS_METRICS_DIR}/system.jsonl
${MAGNUS_METRICS_DIR}/worker-3.jsonl
```

Collector 的实现细节不在本文档范围内，但 Collector 必须把这些文件视为 append-only 输入流。

---

## 10. 失败语义

指标系统默认采用 fail-open 语义。

具体要求如下：

1. `MAGNUS_METRICS_DIR` 不存在时，Job 必须能继续运行。
2. 指标文件无法写入时，Job 默认不应失败。
3. Collector 不在线、Collector 延迟、某些指标没人消费，都不应影响 Job 主流程。
4. local mode、Docker mode、SLURM mode 可以在系统指标覆盖度上不同，但用户态指标协议必须保持一致。

换言之，指标是附加能力，不是 Job 主逻辑的硬依赖。

生产实现还应满足：

- 指标目录不存在时，Emitter `MUST` 进入 disabled 状态或返回可恢复错误，但主任务 `MUST NOT` 因此退出
- 指标行校验失败时，Emitter `SHOULD` 丢弃该点并继续后续点
- Collector 处理失败时，`MUST NOT` 反向影响 Job 进程

---

## 11. 分布式约定

分布式 Job 必须遵循以下原则：

1. 每个 rank 独立写出自己的点。
2. rank 身份通过 `labels` 表达，不通过修改 `name` 表达。
3. 不要求任务侧在写入前做全局聚合。
4. 聚合应由 Magnus 查询层或 UI 层完成。

如果同一个逻辑指标同时存在 local step 与 global step，它们 `MUST` 使用不同的 `step_domain`，而不是混写到同一 stream 中。

推荐最小标签集合：

- `global_rank`
- `local_rank`
- `world_size`
- `node`
- `device`

示例：

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

## 12. 派生指标与窗口指标

v1 不允许在一个点的 `value` 中放入聚合对象。

如果应用要表达窗口统计量，`MUST` 将其展开为独立的标量 stream。

推荐方式如下：

- `train.loss.window_mean`
- `train.loss.window_max`
- `train.grad_norm.window_mean`

窗口信息 `SHOULD` 使用低基数标签表达，例如：

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

类似地，per-layer 指标也 `MUST` 拆成多个点，而不是把整层字典塞进一个点中。

---

## 13. 推荐指标名

下表给出 v1 推荐的常用规范名。

| 类别 | 推荐名 |
|------|--------|
| GPU | `system.gpu.utilization` |
| GPU 显存 | `system.gpu.memory.used_bytes` |
| CPU | `system.cpu.utilization` |
| 内存 | `system.memory.used_bytes` |
| Loss | `train.loss` |
| 学习率 | `train.lr` |
| Token 累计 | `train.tokens.total` |
| Token 吞吐 | `train.tokens.throughput` |
| 梯度范数 | `train.grad_norm` |
| 参数范数 | `train.param_norm` |
| 注意力熵 | `train.attn_entropy` |
| 锐度 | `train.sharpness` |
| 评估损失 | `eval.loss` |
| 推理累计 token | `inference.tokens.total` |
| 物理模拟残差 | `simulation.residual` |

这些名称是推荐规范，不是白名单。

---

## 14. 规范示例

### 14.1 GPU 利用率

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

### 14.2 CPU 利用率

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

### 14.3 训练 Loss

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

### 14.4 已处理 Token 总数

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

### 14.5 注意力熵

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

### 14.6 物理模拟残差

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

## 15. Job 侧最小实现要求

任意语言的 Job 只需要做到以下几点，就算协议兼容：

1. 读取 `MAGNUS_METRICS_DIR`
2. 创建自己的 `.jsonl` 输出文件
3. 在需要时写入合法 JSON 行
4. 写入失败时默认不让主任务退出

因此，Python、C、Rust、Fortran 的最小实现门槛应当是相同的。

协议不要求：

- 必须使用 Magnus SDK
- 必须使用 HTTP
- 必须使用 socket
- 必须 hook 模型内部
- 必须实现系统指标

---

## 16. v1 明确不支持的内容

以下内容不属于 v1：

- 点值为对象、数组或嵌套结构
- 在同一条点中同时携带一批 layer 值
- 依赖特定框架 hook 才能工作
- 把日志文本当成指标
- 把 UI 图表配置写入指标流
- 把 Collector 地址硬编码到任务逻辑里
- 通过回退 step 或归零 counter 来复用旧 stream
- 多个 producer 并发写同一文件

---

## 17. 当前落地实现概述

以下描述 Magnus 对本协议的首个生产实现（v1 落地），供后续开发者快速上手。

### 17.1 架构总览

```
┌─────────────────────────────────────────────────┐
│  SLURM 计算节点 (wrapper.py)                      │
│                                                   │
│  ┌──────────────┐    ┌──────────────────────┐     │
│  │ 用户 Job 进程  │    │ system metrics sidecar│     │
│  │ (任意语言)     │    │ (Python daemon thread)│     │
│  │               │    │                      │     │
│  │ 写 *.jsonl ──────▶ │ 写 system.jsonl ──────────▶ │
│  └──────────────┘    └──────────────────────┘     │
│         │                      │                   │
│         └──────────┬───────────┘                   │
│                    ▼                               │
│         $MAGNUS_METRICS_DIR/                       │
│         (bind mount → 宿主机磁盘)                   │
└─────────────────────────────────────────────────┘
                     │
                     ▼ (共享文件系统)
┌─────────────────────────────────────────────────┐
│  Magnus 头节点                                     │
│                                                   │
│  routers/metrics.py                               │
│  ├─ GET /jobs/{id}/metrics/streams  (列出 stream)  │
│  └─ GET /jobs/{id}/metrics/query    (查询数据点)   │
│         │                                         │
│         ▼ 直读 JSONL 文件，不入数据库               │
│                                                   │
│  前端 MetricsChart 组件                             │
│  └─ recharts 折线图，sidebar 选择指标              │
└─────────────────────────────────────────────────┘
```

### 17.2 系统指标采集

SLURM 模式下，wrapper.py 内置一个 daemon thread（`_metrics_sidecar`），每 5 秒采集一次：

- `system.gpu.utilization`：通过 `nvidia-smi --query-gpu=utilization.gpu`
- `system.gpu.memory.used_bytes`：通过 `nvidia-smi --query-gpu=memory.used`，MiB 换算为 bytes
- `system.cpu.utilization`：通过 `/proc/stat` 两次采样算 delta

写入 `$MAGNUS_METRICS_DIR/system.jsonl`，格式严格遵循本协议。

sidecar 全链路 fail-open：nvidia-smi 不存在（CPU-only 任务）或 `/proc/stat` 不可读时静默跳过。sidecar 在容器启动前开始，容器退出后停止（`threading.Event` + `join(timeout=5)`）。

Docker 本地模式不启动 sidecar（无 wrapper.py），但环境变量照常注入，用户代码可自行上报。

### 17.3 环境变量

Magnus 运行时向 Job 注入：

| 变量 | 值 | 注入方式 |
|------|---|---------|
| `MAGNUS_METRICS_DIR` | `$MAGNUS_HOME/workspace/metrics` | SLURM: `APPTAINERENV_*`; Docker: `-e` |
| `MAGNUS_METRICS_PROTO` | `metrics.v1` | 同上 |
| `MAGNUS_JOB_ID` | Job UUID | 已有，无需新增 |

`MAGNUS_METRICS_DIR` 对应的宿主机路径是 `{workspace}/jobs/{job_id}/metrics/`，通过 bind mount 映射到容器内。数据实际写在宿主机磁盘（非容器可写层），不占 overlay 配额。

### 17.4 存储与生命周期

- **不入数据库**。JSONL 文件即数据源，API 按需读取解析。
- **metrics/ 目录不随 Job 清理删除**，与 `slurm/output.txt` 同策略。Job 结束后用户仍可回看历史指标。
- 典型数据量：8 卡 × 3 指标 × 5 秒间隔 × 24 小时 ≈ 41 万行 ≈ 50 MB，对共享文件系统而言可忽略。

### 17.5 API

两个端点，均需 Bearer token 认证：

**`GET /api/jobs/{job_id}/metrics/streams`**

扫描 `metrics/*.jsonl`，返回去重的 stream 列表：

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

按 name、labels、step_domain、时间范围过滤，超限时均匀降采样。返回：

```json
{
  "name": "system.gpu.utilization",
  "points": [
    {"value": 87.0, "time_unix_ms": 1770000123456, "labels": {"device": "cuda:0"}}
  ]
}
```

### 17.6 前端

Job Detail 页面的"指标"tab，基于 recharts：

- 左侧 sidebar 按 system / train / 其他分组展示可用指标名
- 右侧折线图，同一指标的不同 label 组合（如多 GPU）自动多线渲染
- Running 状态下自动轮询刷新
- 空数据状态有引导文案

### 17.7 已知限制与演进方向

| 限制 | 影响 | 演进路径 |
|------|------|---------|
| API 全量读文件 | 几十卡规模 OK；千卡超算需优化 | 引入 Collector 异步入库（time-series DB）或 seek-based 读取 |
| 前端一次看一个指标 | 不能同时对比 GPU 利用率和显存 | 多图 dashboard 视图 |
| 无 step 轴切换 | 训练指标只能按时间看 | 前端加 time/step 轴切换 |
| Docker 模式无系统指标 | 本地开发无 GPU 监控 | 加 Docker entrypoint wrapper 或 sidecar 容器 |
| 无告警规则 | 无法自动报警 | 未来版本定义告警阈值语法 |

---

## 18. 一句话原则

Magnus Metrics Protocol v1 的核心原则是：

**Job 以最小、稳定、跨语言的 JSONL 点流主动上报指标；每个点同时面向真实时间和逻辑 step 两个坐标系。**
