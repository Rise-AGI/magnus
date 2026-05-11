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
| 发送信号 (signal) | `POST /api/jobs/{id}/signal`、`magnus job signal`（仅 Running） | SLURM: `scancel --signal=TERM --batch <jobid>`；Docker: `docker kill --signal=TERM` | 不修改任何状态 |

**两个动作的分工**：终止是不可逆的硬取消，调度器立刻把 job 移出运行状态、释放占位、清理工作目录，应用层进程没有协调窗口；发送信号则是纯转发器，DB 不动，**用户进程会收到 SIGTERM**。**装了 handler 的用户进程**可以借这个窗口做自己的收尾（保存中间结果 / 检查点、释放 GPU 显存与 NCCL 资源、关闭外部连接、刷新输出缓冲等）；**没装 handler 的进程**对 SIGTERM 不响应 —— magnus 的 wrapper / user_script 层把用户 shell 渲染成 SIGTERM=SIG_IGN（机制见下方 SLURM / Docker 链路），协议契约是"发送信号给的是把优雅终止接进来的用户代码；强制杀走 terminate"。适用于 AI 训练、长跑数值仿真、流式数据处理等任何持有外部资源的场景。

handler 想让自己保存的 partial state 算 success，就在 handler 里写 `$MAGNUS_RESULT`（容器内挂载在 `$MAGNUS_HOME/workspace/.magnus_result`），再 `sys.exit(0)`。即便外层进程树被信号强行带走，magnus 也会把 job 收敛到 `Success` —— 详见下方"状态收敛"。

**SLURM 链路（signal）**：`scancel --signal=TERM --batch <jobid>` —— `--batch`（不是 `--full`）是关键 flag。`--full` 会把 SIGTERM 广播给 batch step cgroup 里所有 PID，包括 apptainer starter / fuse-overlayfs / squashfuse_ll 等容器基础设施进程；它们没装 SIGTERM handler，被默认 disposition 终止后会把 squashfs / overlay mount point 拆掉，user 进程访问内存映射时直接 SIGBUS。`--batch` 改为只投递到 batch step 的 parent process（= wrapper.py），cgroup 其它进程保持不动。wrapper.py 自己再 fan-out，用户脚本不动、无 marker file、无任何协议表面：

- **启动期窗口防御**：sbatch batch script 写成 `#!/bin/bash\ntrap '' TERM\n\nexec <wrapper>`，外层 bash 在做任何事之前装 SIG_IGN，再由 POSIX exec 继承把 SIG_IGN 带进 wrapper.py 进程。从 wrapper.py 启动到 `main()` 里 `signal.signal(SIGTERM, _on_sigterm)` 之间那个极短窗口被覆盖 —— 这期间到达的信号被吞掉而不是按 SIG_DFL 杀掉 wrapper.py。
- **基于 NSpid 的 wrapper-side fan-out**：`_on_sigterm` 先把 `_signaled[0]` 置为 `True`（forward 失败 Phase 3 marker fallback 仍能触发），然后调 `_signal_user_processes(SIGTERM)`。该 helper 读本 job 的 cgroup.procs（hybrid 系统走 v1 freezer，pure v2 走 unified），然后对每个 host PID 看 `/proc/<host_pid>/status` 的 `NSpid:` 字段 —— 这一行列出进程在它所属的每层 PID namespace 内的 PID。单列 = 进程只在 host namespace（wrapper 自己、subprocess shell、apptainer starter、fuse-overlayfs、squashfuse_ll 等基础设施）；双列 = 进程也在子 PID namespace 里，即在用户容器内。helper 跳过单列 PID（host-only 基础设施）和双列里 inner==1 的（apptainer `appinit` / 容器 PID 1），对剩下的发 `kill(host_pid, SIGTERM)`。`kill(2)` 用 host PID 跨 PID namespace 直接生效 —— 内核 PID 是 host-global 标识，不需要 `nsenter` / `setns`。用户 `entry_command` fork 出的任何东西 —— main、fork children、MPI workers、嵌套 shell —— 都在子 namespace 里、都收到信号。同 `systemd KillMode=cgroup` 和 Kubernetes 优雅终止是同一套思路。各层都 fail-open：cgroup 读失败、status 解析失败、单个 kill 失败任一步 continue 不抛回 main。
- **用户脚本 SIG_IGN 守卫**：渲染出的 `.magnus_user_script.sh` 入口装 `trap '' TERM`，跑用户 `entry_command` 的那个 bash 拿到 SIGTERM 的 SIG_IGN。wrapper fan-out 会把 NSpid 命中的所有 cgroup PID 都信号一遍，bash 也在其中；没有这层守卫，bash 会按默认 disposition 立即终止、cascade up 到 `wrapper.subprocess.call`，把还在处理 handler 的 user 进程跟着 SLURM step teardown 一起带走，没机会 `sys.exit(0)`。SIG_IGN 通过 POSIX exec 继承给后代；用户代码用 `signal.signal(SIGTERM, …)` / `sigaction(2)` 装 handler 会自然覆盖。
- **用户进程**：容器内用户进程从 user-script bash 继承到 SIG_IGN 的 SIGTERM disposition。没装 handler 的用户代码下，从 UI 按 send-SIGTERM 是 no-op —— 这是 Unix 的标准结果：没有把优雅终止接进来的程序，SIGTERM 就是不做事；想强制杀走 terminate 按钮（走 `kill_job` 的 SIGKILL 路径）。装了 `signal.signal(SIGTERM, …)`（或 C 的 `sigaction(2)`、Fortran 的 `SIGNAL` intrinsic 等任何 POSIX 进程的信号 idiom）的用户代码会覆盖继承的 SIG_IGN，自己跑收尾、自己定 exit code；handler 里写 `.magnus_result` 让 `sys.exit(0)` 算成功（见下方"状态收敛"）。协议送的是一发普通的 SIGTERM，用户那边走标准 `sigaction(2)` 语义 —— magnus 这边没有需要额外学习的东西。

**SLURM 链路（terminate / 抢占）**：`kill_job` 因此用 `scancel --signal=KILL --full` 直接 SIGKILL 全员清场（SIGKILL 在内核侧不可被 ignore），再裸 `scancel <jobid>` 让 SLURM 把 job state 转为 CANCELLED。如果走默认的 `scancel <jobid>`（KillSignal=SIGTERM、KillWait 后 SIGKILL），整个 KillWait 窗口都会被烧掉 —— wrapper.py 的 handler 不退而是 forward，handler-aware 的用户代码会用这段 grace 跑自己的 graceful shutdown，而不是立刻让出资源。SIGKILL 直发让 terminate 与抢占的延迟回到秒级。

**Docker 链路（signal）**：两层接力，跟 SLURM 在用户进程层面对等：

1. `docker run --init` 让 docker 自带的 tini 当 PID 1，reap 孤儿进程并把 SIGTERM 转发给直接子进程（外层 bash）。tini **不**像 SLURM proctrack 那样广播给容器内全员，只送给外层 bash —— 这正是为什么需要下面的脚本层接力。
2. `.magnus_user_script.sh`（由 `_scheduler/_submit.py` 的 `_render_docker_user_script` 渲染）开头 `set -m` 启用 bash monitor mode，然后把用户的 entry_command 放进 `( set -e; ... ) &` 后台子壳里 —— 在 `set -m` 下子壳自动 setpgid 成为新的 pgrp leader（`_magnus_pid` 同时是 PID 和 pgid）。不开 monitor mode 的非交互 bash 默认让后台子壳继承外层 pgid，负 PID kill 会指向一个不存在的 pgrp，转发被吞。外层 bash 装 `trap 'kill -TERM -- -$_magnus_pid' TERM`，SIGTERM 通过 `killpg` 转给整个 user pgrp —— main、fork 子进程、MPI workers、嵌套 shell 全员收到。`while ... wait` 循环：wait 被信号中断后返回 128+sig，重发 wait 直到子进程真退，最后用 `exit $?` 透传子进程真实退出码。外层 bash 装了非空 trap 不会 deferred terminate。

合起来两边在用户进程层面行为一致：子壳同样装 `trap '' TERM`（在 `( … ) &` 子壳入口），跟 SLURM 一侧 `.magnus_user_script.sh` 的守卫对等，把 cascade-up race 闭合；没装 handler 的用户代码因继承 SIG_IGN 把 SIGTERM 当 no-op，装了 handler 的代码用自己的 `signal.signal` / `sigaction` 自然覆盖、跑 handler、自己定 exit。pgrp 转发把 entry_command 内部多层嵌套 shell 也覆盖到了 —— 用户代码不需要自己显式装 trap 转发。

**Docker 链路（terminate）**：对偶 SLURM SIGKILL 直发的设计，`terminate_job` 调 `stop_container(container_name, timeout=0)` 让 `docker stop -t 0` 立即发 SIGKILL，跳过 docker stop 默认 10 秒 SIGTERM grace。如果不传 `timeout=0`，docker 会先发 SIGTERM 给外层 bash，外层 bash 只转发不死，10 秒后才 SIGKILL —— 破坏 terminate"无协调窗口"的语义。

**状态收敛**：`signal_job` 不动 DB。信号送达后由 `_sync_reality` 走常规路径收敛到 `Success` / `Failed`：

- **SLURM 模式**走 wrapper 与 sync 双层 defense-in-depth。`_wrapper_template.py` Phase 3：`ret_code == 0` 照旧写 success marker；否则若 `_signaled` 为 `True` 且用户在 SIGTERM handler 里写了 `.magnus_result`，wrapper 仍写 success marker（label `success-after-signal`）并 `sys.exit(0)`，让 SLURM 有机会把 job 标 COMPLETED。`_sync.py:_sync_reality_slurm` 兜底让 marker 优先于 SLURM state —— `FAILED / CANCELLED / TIMEOUT` 分支先读 `.magnus_success`，存在就走 `_finalize_completed_job`，不存在才把 SLURM state 当成失败原因记录。这是为了应对实测中"SLURM 某些版本 / 配置下 batch step 收过 SIGTERM 一律标 CANCELLED 不管 wrapper exit 几"的情况。ret ≠ 0 又没有 handler 写的 result、也没 marker 仍 `Failed`。
- **Docker 模式**：container `exit_code == 0` 收敛 `Success`（handler 自己 `sys.exit(0)`），非 0 是 `Failed`。Docker 没有 wrapper 中间层，container exit code 即是用户 exit code，不需要额外的 fallback marker。

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
