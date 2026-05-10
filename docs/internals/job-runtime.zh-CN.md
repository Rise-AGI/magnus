> **Language / 语言**: [English](job-runtime.md) · **简体中文**

# Magnus Job Runtime

本文档描述 Magnus job 从提交到容器内执行的完整运行时链路，以及宿主机与容器之间的文件系统协议和环境变量协议。

## 执行链路总览

```
用户提交 (POST /api/jobs/submit)
  │
  ▼
PREPARING ─── 异步资源准备（并行）
  │             ├── ensure_image: docker:// → .sif (LRU cache, 80G)
  │             └── ensure_repo:  git clone → copy → checkout → setfacl
  ▼
PENDING ───── EASY backfill 调度 (Lifka 1995)
  │             ├── 按优先级排序: A1(4) > A2(3) > B1(2) > B2(1), 同级 FIFO
  │             ├── A 类可抢占 RUNNING 的 B 类 (B2 优先, LIFO)
  │             ├── 队头能跑 → 严格优先级贪心提交全部装得下的 job
  │             └── 队头在等 → 后续候选满足 demand+head.demand ≤ cluster_total
  │                          可旁路启动（数学保证不延迟队头）
  ▼
QUEUED ────── SLURM sbatch 已提交
  │             └── sbatch 脚本: python3 {workspace}/jobs/{id}/wrapper.py
  ▼
RUNNING ───── wrapper.py 开始执行
  │             ├── Phase 1: GPU spy 守护线程 (nvidia-smi 轮询)
  │             ├── Phase 2: 写 .magnus_user_script.sh
  │             ├── Phase 3: shell 引导层 → apptainer exec → 用户脚本
  │             └── Phase 4: epilogue, 写 .magnus_success
  ▼
SUCCESS / FAILED
```

## wrapper.py: 三层结构

`_scheduler/_wrapper_template.py` 的 `_build_wrapper_content()` 生成 `wrapper.py`，它是 SLURM 实际执行的入口。wrapper 内含三个嵌套层次：

```
wrapper.py (Python, SLURM 直接运行)
  ├── GPU spy thread        ← Python threading
  ├── .magnus_user_script.sh 写入
  └── shell_cmd (Bash)      ← subprocess.call(shell=True)
        ├── 环境变量注入 (APPTAINERENV_*)
        ├── system_entry_command 执行
        ├── overlay 创建
        └── apptainer exec   ← 容器入口
              └── .magnus_user_script.sh  ← 用户代码
```

### Phase 1: 用户脚本

将 `job.entry_command` 写入 `.magnus_user_script.sh`，前面加 `set -e`。

### Phase 2: Shell 引导层

这是最复杂的部分，按顺序执行：

1. **注入容器环境变量** — 通过 `APPTAINERENV_` 前缀（apptainer 会自动去前缀注入容器）
2. **执行 `system_entry_command`** — 宿主机侧的 per-job 可配置 shell 脚本
3. **兜底 `MAGNUS_HOME`** — `export MAGNUS_HOME=${MAGNUS_HOME:-/magnus}`，此后所有路径引用 `$MAGNUS_HOME`
4. **设置 apptainer 运行时目录** — `APPTAINER_TMPDIR`, `APPTAINER_CACHEDIR`
5. **追加 bind mount** — workspace 挂到 `$MAGNUS_HOME/workspace`
6. **代理穿透** — bridge 模式下将 `127.0.0.1`/`localhost` 替换为 `$MAGNUS_HOST_GATEWAY`
7. **检测 setuid apptainer** — `[ -u "$(command -v apptainer)" ]`，零 I/O，结果决定后续分支
8. **确定隔离级别** — rootless 默认 `containall`，setuid 默认 `contain`（避免 userns 冲突），`MAGNUS_CONTAIN_LEVEL=none` 回退到裸跑
9. **创建 overlay 或降级** — 有隔离 + rootless 时创建 sparse overlay（`--sparse` 瞬间完成，apptainer ≥ 1.3；旧版自动回退到 dense 创建并 warn）；有隔离 + setuid 或 `MAGNUS_NO_OVERLAY=1` 时降级到 `--writable-tmpfs`；无隔离（none）时裸跑
10. **注入 HOME** — `--env HOME=$MAGNUS_HOME`（`APPTAINERENV_HOME` 被 apptainer 禁止，用 `--env` 绕过）
11. **执行 apptainer** — host 模式直接 exec，bridge 模式走 `rootlesskit`

### Phase 3: Epilogue

apptainer 返回 0 时写 `.magnus_success` 标记。finally 块中清理 overlay 文件。

## 文件系统协议

### 宿主机侧

所有路径基于 `{magnus_root}/workspace/jobs/{job_id}/`（下文简称 `{work}/`）：

| 路径 | 生命周期 | 写入方 | 读取方 | 说明 |
|------|----------|--------|--------|------|
| `{work}/repository/` | prepare → cleanup | resource_manager | 容器 (bind) | git checkout，容器内的工作目录 |
| `{work}/wrapper.py` | submit → cleanup | scheduler | SLURM | 生成的执行入口 |
| `{work}/slurm/output.txt` | submit → 永久 | SLURM | API (日志) | sbatch --output 指向此处 |
| `{work}/.magnus_user_script.sh` | wrapper 执行 → cleanup | wrapper.py | 容器 (bind) | 用户入口脚本 |
| `{work}/.magnus_success` | epilogue → sync_reality | wrapper.py | scheduler | 成功标记，存在即 SUCCESS |
| `{work}/.magnus_oom` | epilogue（仅在 ret≠0 时写）→ sync_reality / cleanup | wrapper.py（探测 cgroup memory.events） | scheduler | OOM 标记，存在即表示 job 所在 cgroup 内发生过内核 OOM-kill；调度器据此把 `job.result` 改写为内存超限提示，覆盖通用 FAILED 字符串。Docker 模式改用 `docker inspect .State.OOMKilled` 取代此文件。 |
| `{work}/.magnus_result` | 容器内用户写入 → API 读取 | 用户代码 | routers/jobs.py | 任务结果内容 |
| `{work}/.magnus_action` | 容器内用户写入 → API 读取 | 用户代码 | routers/jobs.py + SDK | 客户端动作指令 |
| `{work}/ephemeral_overlay.img` | Phase 2 → finally | wrapper shell | apptainer | 可写层，job 结束后删除 |
| `{work}/.magnus_tmp/` | Phase 2 → cleanup | apptainer | apptainer | APPTAINER_TMPDIR |
| `{work}/.magnus_cache/` | Phase 2 → cleanup | apptainer | apptainer | APPTAINER_CACHEDIR |
| `{work}/metrics/` | submit → 永久 | wrapper sidecar + 用户代码 | routers/metrics.py | Magnus Metrics Protocol v1 JSONL 指标文件 |

**cleanup** 指 `_clean_up_working_table()`，在 job 结束（SUCCESS/FAILED/TERMINATED/PAUSED）时调用。`slurm/output.txt` 不被清理。

## 信号与终止

Magnus 区分两种针对运行中 job 的外部干预：

| 操作 | 入口 | 后端实现 | DB 状态变化 |
|------|------|----------|-------------|
| 终止 (terminate) | `POST /api/jobs/{id}/terminate`、`magnus job kill` | SLURM: `scancel --signal=KILL --full` + 裸 `scancel`；Docker: `docker stop -t 0 && docker rm -f` | 立即置为 `Terminated`，清理工作目录 |
| 发送信号 (signal) | `POST /api/jobs/{id}/signal`、`magnus job signal`（仅 Running） | SLURM: `scancel --signal=TERM --full <jobid>`；Docker: `docker kill --signal=TERM` | 不修改任何状态 |

**两个动作的分工**：终止是不可逆的硬取消，调度器立刻把 job 移出运行状态、释放占位、清理工作目录，应用层进程没有协调窗口；发送信号则是纯转发器，DB 不动、调度器视角下 job 仍在运行，把"收到 SIGTERM 之后做什么"完全交给用户代码。装了 SIGTERM 处理器的用户进程可以借这个窗口做自己的收尾（保存中间结果 / 检查点、释放 GPU 显存与 NCCL 资源、关闭外部连接、刷新输出缓冲等），适用于 AI 训练、长跑数值仿真、流式数据处理等任何持有外部资源的场景。

**SLURM 链路（signal）**：必须带 `--full`，否则 `scancel --signal` 只发给非 batch step；wrapper.py 是 sbatch 直接拉起的 batch script（不走 srun）。proctrack 按 cgroup / proc 树扩散信号给每个 PID，到达性由 SLURM 自己保证。链路 `外层 bash (sbatch shebang) → wrapper.py → subprocess shell → apptainer → 容器内 bash → 用户进程` 必须共享同一个 SIGTERM disposition（SIG_IGN），否则任何中间节点收到 SIGTERM 自身 deferred terminate exit 143，会盖掉用户进程的真实退出码。POSIX 规定 SIG_IGN 通过 fork+exec 继承，链路顶端装一次就覆盖整条下游。我们装了两次做对称兜底：

- sbatch batch script（由 `submit_job_simple` 渲染）写成 `#!/bin/bash\ntrap '' TERM\n\nexec <wrapper>`。`trap '' TERM` 把外层 bash disposition 设为 SIG_IGN；`exec` 立刻让 wrapper.py 替换该 bash 进程，POSIX SIG_IGN inheritance 把 disposition 带进 wrapper.py。这一行是关键：没有它，外层 bash 收到 SIGTERM 会 deferred terminate exit 143，即便 wrapper.py exit 0 也会让 SLURM 把 job state 标成 FAILED / CANCELLED。
- `wrapper.py main()` 自己在入口再调一次 `signal.signal(SIGTERM, SIG_IGN)` —— 在外层 bash 已经把 SIG_IGN inherit 进来的前提下是 idempotent，但保留作为 defense-in-depth：让 wrapper.py 的 disposition 不依赖外层的拉起方式。

用户代码用 `signal.signal()` 显式覆盖来响应（Python 直接调 sigaction，不受继承的 SIG_IGN 影响）。装了 handler 的进程响应信号后 exit 0 → wrapper 写 success marker → DB 收敛 `Success`；没装 handler 的进程跟随 SIG_IGN 沉默继续跑 → 符合"温柔提醒"语义，由用户决定是否再点 terminate 强制结束。

**SLURM 链路（terminate / 抢占）**：`kill_job` 因此必须改用 `scancel --signal=KILL --full` 直接 SIGKILL 全员清场（SIGKILL 在内核侧不可被 ignore），再裸 `scancel <jobid>` 让 SLURM 把 job state 转为 CANCELLED。如果走默认的 `scancel <jobid>`（KillSignal=SIGTERM、KillWait 后 SIGKILL），前 KillWait 秒会因为全链 SIG_IGN 完全空转，破坏抢占的"瞬时让出 GPU"承诺。SIGKILL 直发让 terminate 与抢占的延迟回到秒级。

**Docker 链路**：信号穿透由两层接力完成，对齐 SLURM 模式 SIG_IGN 链路的语义：

1. `docker run` 启用 `--init` 让 docker 自带的 tini 当 PID 1，reap 孤儿进程并把 SIGTERM 转发给直接子进程（外层 bash）。tini **不**像 SLURM proctrack 那样广播给容器内全员，只送给外层 bash —— 这正是为什么需要下面的脚本层接力。
2. `.magnus_user_script.sh` 由 `_scheduler/_submit.py` 的 `_render_docker_user_script` 渲染：把用户的 entry_command 整体放进 `( trap '' TERM; set -e; ... ) &` 子壳后台跑，子壳 disposition 是 SIG_IGN。当子壳的最后一个命令是单一长跑进程（典型 `pip install ... && python train.py` 模式），bash exec 替换让"子壳 PID 直接变成用户进程 PID"，POSIX SIG_IGN inheritance 把 disposition 带到用户进程。外层 bash 装 `trap 'kill -TERM "$_magnus_pid"' TERM` + `while ... wait` 循环：tini → 外层 bash 收到 SIGTERM，trap 主动把信号转发给子壳/用户 PID；wait 被信号中断后返回 128+sig，循环重发 wait 直到子进程真退，最后用 `exit $?` 透传子进程真实退出码。

合起来：没装 handler 的用户代码在 kernel 层面收到 SIG_IGN 处理（跟 SLURM 一致）；装了 `signal.signal(SIGTERM, ...)` 的代码自己覆盖 disposition 触发 handler（跟 SLURM 一致）；用户进程的真实 exit code 通过 docker container 的 exit code 透传，让 `_sync_reality_docker` 正确收敛到 `Success` / `Failed`。entry_command 内部多层嵌套 shell 仍需各自显式 trap 才能继续接力 —— 这跟 SLURM 模式 apptainer 子壳的限制相同。两边能力对等。

**Docker 链路（terminate）**：对偶 SLURM SIGKILL 直发的设计，`terminate_job` 调 `stop_container(container_name, timeout=0)` 让 `docker stop -t 0` 立即发 SIGKILL，跳过 docker stop 默认 10 秒 SIGTERM grace。如果不传 `timeout=0`，这 10 秒会因为上面建立的全链 SIG_IGN 完全空转，破坏 terminate"无协调窗口"的语义。

**状态收敛**：`signal_job` 不动 DB。用户进程响应 SIGTERM 自然退出后，由 `_sync_reality` 走常规路径（success marker / 退出码）收敛到 `Success` 或 `Failed`。用户代码忽略信号则 job 继续 `Running`，由用户决定是否再点终止强制结束。

**抢占语义不变**：`_decisions._kill_and_pause` 仍走 `kill_job`，瞬时让出 GPU 的承诺由上面 SIGKILL 直发的设计兜底。要给 B 类任务保存检查点的能力，需要单独设计抢占预通知机制，本次不在范围内。

### 容器内侧

```
${MAGNUS_HOME}/                              默认 /magnus
${MAGNUS_HOME}/workspace/                    bind mount ← {work}/
${MAGNUS_HOME}/workspace/repository/         git checkout, 也是 --pwd
${MAGNUS_HOME}/workspace/.magnus_user_script.sh
${MAGNUS_HOME}/workspace/.magnus_result      $MAGNUS_RESULT
${MAGNUS_HOME}/workspace/.magnus_action      $MAGNUS_ACTION
${MAGNUS_HOME}/workspace/metrics/            $MAGNUS_METRICS_DIR (Metrics Protocol v1)
${MAGNUS_HOME}/.tmp/                         SDK 文件中转目录 (容器可写层, 自动创建)
```

`MAGNUS_ACTION` 只是一个普通文本文件，运行时不会在后端自动执行。当前各客户端的行为是：

- SDK / CLI：默认读取并执行 `MAGNUS_ACTION`
- Web：不执行任意 shell，只对白名单形式 `magnus receive <secret> [--output/-o <target>]` 做浏览器下载映射

因此在 Web 场景里，`--output/-o <target>` 的语义不是“写入浏览器本地指定路径”，而是“建议下载名”。

容器文件系统是只读 squashfs (SIF)。可写层取决于隔离模式：

| 模式 | 可写层 | 容量限制 | 说明 |
|------|--------|----------|------|
| containall + overlay | ephemeral overlay (sparse ext3 image) | `ephemeral_storage` | 默认路径（rootless apptainer），`--sparse` 创建瞬间完成且按需占用磁盘 |
| containall/contain + writable-tmpfs | RAM tmpfs | 与 `memory_demand` 共享 | setuid apptainer 或 `MAGNUS_NO_OVERLAY=1` |
| none（裸跑） | host 文件系统穿透 | 无限制 | `MAGNUS_CONTAIN_LEVEL=none`，等效 overlay 出现之前的行为 |

### SDK 运行环境检测协议

SDK 的 `file_transfer.get_tmp_base()` 用以下判据决定文件中转目录（上传 tar 压缩、下载 tar 解压的临时文件）：

1. 环境变量 `MAGNUS_HOME` 存在
2. `$MAGNUS_HOME/workspace/` 是已存在的目录（由 Magnus runtime 创建并 bind-mount）

两个条件都满足时，中转目录为 `$MAGNUS_HOME/.tmp/`（容器可写层），否则 fallback 到系统 `/tmp`。

**为什么不用 `/tmp`**：在两种隔离模式下 `/tmp` 都不是理想的中转位置：

| 模式 | `/tmp` 位置 | 问题 |
|------|-------------|------|
| overlay | overlay 镜像内 | 与 pip install 等容器内写入共享 `ephemeral_storage` 配额 |
| writable-tmpfs | RAM tmpfs | 容量有限（内核默认 50% cgroup RAM），大文件触发 ENOSPC |

`$MAGNUS_HOME/.tmp/` 同样位于容器可写层（overlay 或 tmpfs），与 `/tmp` 共享同一写入预算，但避免了与系统临时文件混杂。更重要的是，它**不**写入 `$MAGNUS_HOME/workspace/`（host 磁盘 bind mount），从而保持容器隔离——容器内用户代码不会通过中转文件逃逸到宿主机文件系统。

嵌套容器场景下，内层 `$MAGNUS_HOME/workspace/` 同样是 bind-mount 链的一环，检测协议同样有效；内层 `$MAGNUS_HOME/.tmp/` 仍位于内层容器的可写层。

## 环境变量协议

### 容器内注入的环境变量

通过 `APPTAINERENV_` 前缀机制注入，容器内去掉前缀后可直接读取：

| 变量 | 来源 | 说明 |
|------|------|------|
| `MAGNUS_TOKEN` | `job.user.token` | 当前用户的 trust token，SDK 自动识别 |
| `MAGNUS_ADDRESS` | `{server.address}:{server.front_end_port}` | Magnus 后端地址 |
| `MAGNUS_JOB_ID` | `job.id` | 当前 job ID |
| `MAGNUS_HOME` | `${MAGNUS_HOME:-/magnus}` | 容器内根目录，子 Magnus 可覆盖 |
| `MAGNUS_RESULT` | `$MAGNUS_HOME/workspace/.magnus_result` | 结果文件路径 |
| `MAGNUS_ACTION` | `$MAGNUS_HOME/workspace/.magnus_action` | 动作文件路径 |
| `HOME` | `$MAGNUS_HOME`（通过 `--env` 注入） | 容器内 HOME，用户 entry_command 可覆盖 |
| `HTTP_PROXY` 等 | 宿主机继承 | bridge 模式下自动替换 localhost → gateway |

### shell 引导层的环境变量旋钮

这些变量由 `system_entry_command` 设置，控制 wrapper shell 的行为：

| 变量 | 默认值 | 作用 |
|------|--------|------|
| `MAGNUS_HOME` | `/magnus` | 容器内根路径，影响 bind mount 目标和所有内部路径。system_entry_command 后兜底赋值，后续全部引用 `$MAGNUS_HOME` |
| `MAGNUS_NO_OVERLAY` | `0` | 设为 `1` 跳过 ephemeral overlay，降级到 `--writable-tmpfs`（RAM） |
| `MAGNUS_CONTAIN_LEVEL` | `containall`(rootless) / `contain`(setuid) | apptainer 隔离级别，设为 `none` 完全禁用隔离（裸跑，host /tmp 穿透） |
| `MAGNUS_FAKEROOT` | `0` | 设为 `1` 添加 `--fakeroot` |
| `MAGNUS_NET_MODE` | `host` | 设为 `bridge` 启用 rootlesskit 网络隔离 |
| `MAGNUS_PORT_MAP` | (无) | bridge 模式下 rootlesskit 的端口映射 |
| `MAGNUS_HOST_GATEWAY` | `10.0.2.2` | bridge 模式下代理地址替换目标 |
| `MAGNUS_HOST_LOOPBACK` | `0` | 设为 `1` 允许容器访问宿主机 loopback |
| `APPTAINER_BIND` | (无) | 额外 bind mount，wrapper 会追加 workspace 绑定 |

`system_entry_command` 是 per-job 可配置的，不设则用 `cluster.default_system_entry_command`。它在宿主机侧、容器外执行。

## apptainer 执行参数

### setuid 检测与自适应决策树

apptainer 有两种安装方式，行为差异巨大：

| | rootless (`-rwxr-xr-x`) | setuid (`-rwsr-xr-x`) |
|---|---|---|
| 检测 | `[ -u apptainer ]` 为 false | `[ -u apptainer ]` 为 true |
| overlay 创建 | 文件属主为调用用户 ✓ | 文件属主为 root:0600 ✗ |
| `--containall` | 正常 (`--userns` 可用) | **报错** (setuid + userns 冲突) |

决策树：

```
[ -u apptainer ]?
├── no (rootless)
│   └── MAGNUS_CONTAIN_LEVEL=none?
│       ├── yes → 裸跑 --nv
│       └── no  → --containall + overlay
└── yes (setuid)
    └── MAGNUS_CONTAIN_LEVEL=none?
        ├── yes → 裸跑 --nv
        └── no  → --contain + --writable-tmpfs (WARNING)
```

### 命令模板

```bash
# 隔离模式 (默认)
apptainer exec \
  --nv \                                  # GPU 驱动透传
  --${APPTAINER_CONTAIN} \               # containall 或 contain
  --no-mount tmp \                        # 禁止 /tmp 上的 64MB tmpfs
  [--overlay ephemeral_overlay.img] \     # rootless + overlay 时 (sparse, 按需占磁盘)
  [--writable-tmpfs] \                    # setuid 或 MAGNUS_NO_OVERLAY=1 时
  --env HOME=$MAGNUS_HOME \              # 容器内 HOME
  [--fakeroot] \                          # MAGNUS_FAKEROOT=1 时
  --pwd $MAGNUS_HOME/workspace/repository \
  {sif_path} \
  bash $MAGNUS_HOME/workspace/.magnus_user_script.sh

# 裸跑模式 (MAGNUS_CONTAIN_LEVEL=none)
apptainer exec \
  --nv \
  --env HOME=$MAGNUS_HOME \
  --pwd $MAGNUS_HOME/workspace/repository \
  {sif_path} \
  bash $MAGNUS_HOME/workspace/.magnus_user_script.sh
```

bridge 模式下整个 apptainer 命令被 rootlesskit 包裹：
```bash
rootlesskit \
  --net=slirp4netns \
  --port-driver=builtin \
  --publish $MAGNUS_PORT_MAP \
  [--disable-host-loopback] \             # MAGNUS_HOST_LOOPBACK!=1 时
  apptainer exec ...
```

## SLURM 提交参数

```bash
sbatch --parsable \
  --job-name={task_name} \
  --output={work}/slurm/output.txt \
  --gres=gpu:{gpu_type}:{gpu_count} \
  --mem={memory_demand} \
  --cpus-per-task={cpu_count} \
  # 脚本内容: python3 {work}/wrapper.py
```

环境变量 `MAGNUS_RUNNER` 和 `MAGNUS_TOKEN` 通过 sbatch 的进程环境传递。

## scheduler 心跳与状态同步

心跳间隔 `scheduler.heartbeat_interval`（默认 2 秒），每次 tick:

1. **`_sync_reality`**: 遍历 QUEUED/RUNNING job，用 `squeue` 查真实状态
   - SLURM 报 RUNNING → DB 标 RUNNING
   - SLURM 报 COMPLETED + `.magnus_success` 存在 → SUCCESS，同时检查 `.magnus_result` 和 `.magnus_action`
   - SLURM 报 COMPLETED 但无 `.magnus_success` → FAILED
   - SLURM 报 FAILED/CANCELLED/TIMEOUT → FAILED
2. **`_make_decisions`**: 调度 PENDING/PAUSED job
3. **`_record_snapshot`**: 每 `snapshot_interval`（默认 300 秒）记录集群快照

## 资源准备

在 PREPARING 阶段并行执行：

**镜像拉取** (`_resource_manager.ensure_image`):
- docker URI → SIF 文件名映射（`docker://a/b:tag` → `a_b_tag.sif`）
- 缓存目录 `{magnus_root}/container_cache/`，LRU 淘汰，上限 `resource_cache.container_cache_size`
- per-image asyncio.Lock 防重复拉取
- 3 次重试 + 指数退避，非瞬态错误（unauthorized, not found）直接失败

**仓库克隆** (`_resource_manager.ensure_repo`):
- 缓存目录 `{magnus_root}/repo_cache/`，LRU 淘汰，上限 `resource_cache.repo_cache_size`
- 缓存 → copy 到 `{work}/repository/` → fetch + checkout 到指定 commit SHA
- `setfacl` 设置 runner 用户权限（容器内以 runner 身份执行时需要）

## 子 Magnus (嵌套容器)

子 Magnus 是在容器内运行完整 Magnus + SLURM 栈的场景。

### 已知的底层陷阱

**SLURM `PartitionName=default` 是保留字**: SLURM 将 `default`（大小写不敏感）解释为分区默认模板，不是实际分区名。子 SLURM 集群使用 `PartitionName=batch`。

**容器内 bind mount 路径不能与母 Magnus 冲突**: 母 Magnus 已经 bind-mount 了 `/magnus`，子 apptainer 再 bind 同路径会冲突。解法：在子 Magnus 的 `system_entry_command` 中 `export MAGNUS_HOME=/submagnus`，所有内部路径自动跟随。

### 子 Magnus 的典型 system_entry_command

```bash
# 额外 bind mount
mounts=(
  "/dev/fuse:/dev/fuse"           # 子 apptainer 需要 fuse 设备
)
export APPTAINER_BIND=$(IFS=,; echo "${mounts[*]}")

# 路径隔离
export MAGNUS_HOME=/submagnus     # 不能叫 /magnus，母容器已占用

# 降级隔离
export MAGNUS_CONTAIN_LEVEL=contain  # containall 在嵌套场景下过于严格
export MAGNUS_NO_OVERLAY=1           # fuse-overlayfs 不支持嵌套

# 网络
export MAGNUS_HOST_LOOPBACK=1     # 允许访问宿主机代理

# 权限
# 配合 server.scheduler.allow_root=true
```

### 子 SLURM 引导

`scripts/setup_single_node_slurm.sh` 在容器内引导单节点 SLURM 集群：
- 集群名 `magnus-child`，分区名 `batch`
- 启动 munge → slurmctld → slurmd
- 通过 `sinfo` 验证集群就绪

### 嵌套容器的已知限制

**Ephemeral overlay (fuse-overlayfs) 在嵌套容器中不工作**。第一层 apptainer 已经使用 squashfuse (SIF 挂载) + fuse-overlayfs (可写层)，第二层再叠 fuse-overlayfs 形成 FUSE-on-FUSE，Linux 内核的 mount namespace 隔离导致内层 FUSE 进程无法正确 unmount——mount 状态在不同 namespace 之间不一致。这不是 apptainer 的 bug，而是 Linux 内核不支持无限嵌套隔离（`CAP_SYS_ADMIN` 在第一层就被剥掉，FUSE 是无 capabilities 时的妥协方案，嵌套 FUSE 的 mount propagation 跨 namespace 会出问题）。当前通过 `MAGNUS_NO_OVERLAY=1` 绕过。

其他已踩过的嵌套陷阱：

| 问题 | 根因 | 解法 |
|------|------|------|
| `/dev/fuse` 不可用 | `--containall` 隔离了设备 | bind mount `/dev/fuse:/dev/fuse` |
| 代理 `10.0.2.2` 不可达 | rootlesskit `--disable-host-loopback` | `MAGNUS_HOST_LOOPBACK=1` |
| `setfacl` 不存在 | 容器镜像未装 `acl` 包 | resource_manager 降级为 warning |
| root 用户被拒 | wrapper.py 硬编码禁止 root | `server.scheduler.allow_root=true` |
| git clone SSH 失败 | 容器内无 SSH 客户端 | resource_manager HTTPS fallback |
| SLURM `PartitionName=default` | SLURM 保留字 | 改为 `PartitionName=batch` |

## 配置参考

### `magnus_config.yaml` 中与 job runtime 相关的配置

```yaml
server:
  root: /home/magnus/magnus-data            # 所有路径的根
  scheduler:
    heartbeat_interval: 2                   # 心跳间隔 (秒)
    snapshot_interval: 300                  # 集群快照间隔 (秒)
    allow_root: false                       # 是否允许 root runner
  resource_cache:
    container_cache_size: 80G               # SIF 缓存上限 (LRU)
    repo_cache_size: 20G                    # git repo 缓存上限 (LRU)

cluster:
  default_cpu_count: 4
  default_memory_demand: 1600M
  default_runner: zycai
  default_container_image: docker://pytorch/pytorch:2.5.1-cuda12.4-cudnn9-runtime
  default_ephemeral_storage: 10G
  default_system_entry_command: |-
    mounts=(
      "/home:/home"
      "/opt/miniconda3:/opt/miniconda3"
    )
    export APPTAINER_BIND=$(IFS=,; echo "${mounts[*]}")
    export MAGNUS_HOME=/magnus
    unset -f nvidia-smi
    unset VIRTUAL_ENV SSL_CERT_FILE
    export UV_CACHE_DIR=/home/magnus/magnus-data-develop/uv_cache/$USER
```

### 源文件索引

| 文件 | 职责 |
|------|------|
| `back_end/server/_scheduler/` | 调度器核心：心跳、状态同步、wrapper 生成、SLURM 提交 |
| `back_end/server/_slurm_manager/` | SLURM CLI 封装 (sbatch/squeue/scancel/sinfo) |
| `back_end/server/_resource_manager/` | 镜像拉取 + 仓库克隆，带 LRU 缓存 |
| `back_end/server/routers/jobs.py` | Job CRUD API，惰性读取 .magnus_result/.magnus_action |
| `back_end/server/models/` | Job 模型 (SQLAlchemy) |
| `configs/magnus_config.yaml` | 配置源 |
| `docker/magnus-runtime/Dockerfile` | 子 Magnus 运行时镜像 |
| `scripts/setup_single_node_slurm.sh` | 容器内 SLURM 引导脚本 |
