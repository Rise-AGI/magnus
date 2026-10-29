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
PENDING ───── 资源准入
  │             ├── 按优先级排序: A1(4) > A2(3) > B1(2) > B2(1), 同级 FIFO
  │             ├── 完整可知的单节点 → 预留队头，仅以剩余资源旁路
  │             ├── 只有实际分配构成完整回收方案，才抢占 B 类任务
  │             └── 复杂布局 → 仅队头交 SLURM；tenant → 交 SLURM 排队
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

SSH 产物回传独立于状态同步与任务准入，最多同时执行四项，每个 Job 只保留一个回传 worker。传输失败保留现场与回执，60 秒后重试。每轮 marker 读取与运行日志采样各共享 30 秒预算。`transport.ssh.transfer_timeout_seconds` 为正整数，默认 900 秒，限制每次准备阶段传输及每轮最终回传，预算包含 SSH 认证；大产物或慢链路可调大。超时终止本次本地传输进程树，保留共享 SSH master；任务终态仍需成功读取 marker 后判定。

准入与提交同样由单个后台任务执行，上传阻塞不会暂停状态同步，也不会并发重复执行资源准入。提交前的准备失败先保存终态，再独立重试清理；没有取得 SLURM ID 的 Job 也能恢复清理。

Slurm 的执行终态与资源释放分开处理。处于 `COMPLETING` 时，若 `scontrol --json` 提供结构化基础状态与标记，已确认的执行终态可先显示为 Success/Failed，同时保留外部绑定和 `is_releasing`。重排期间的清理不视为执行失败。旧解析器只提供合并状态、查询不受支持或证据不明确时，继续沿用保守行为。参见 [Slurm 状态协议](https://slurm.schedmd.com/job_state_codes.html)。工作区删除、镜像保护解除及最终产物/日志封存等待资源明确释放；等待期间仍增量回传 custody 和日志。`is_releasing` 涵盖仍有外部绑定的终态和暂停任务，不代表资源使用量。

后端启动失败和关闭都会依次清理已登记的资源。全部后台循环及其文件操作线程退出后，才关闭调度器、custody 与构建管理器；单项关闭失败不跳过其他项，重复取消也不会遗留脱离管理的收尾任务。此约定覆盖正常及异常的应用关闭，不承诺 `SIGKILL` 或断电时执行清理；这些情况仍依靠持久记录恢复。

### SSH 预置与受理未知的提交

目录上传使用一次压缩流代替递归 SCP，完整保留 `.git`、空目录、文件权限和软链文本，不复制软链指向的目录外内容。远端 Python 3.6+ 接收器校验字节数和 SHA-256，在私有同级目录中安全解包后原子发布完整目录。打包、建连和传输共用配置的传输时限，两端需要临时压缩文件空间；本机与 Docker 执行不变。新的预置尝试会清除旧的离线输入，再准备当前 custody 输入。

`sbatch` 调用限时 60 秒。实际发送前，Magnus 持久化唯一提交标识，并作为 Slurm comment 传入。超时、传输回复丢失或返回无效 ID 时，保留受理未知的尝试，禁止重提和回收工作区、镜像。每分钟通过有界的 `squeue`、`sacct` 查询轮转提交时刻附近的固定 20 分钟窗口，恢复唯一精确匹配，也处理提交同时发生取消的情况。依赖 accounting 恢复时，站点须保存 job comment；无结果或有歧义时继续保全，交由管理员对账。没有提交标识的历史 Job 保持原行为。

仅明确的提交配额原因码触发账户级 60 秒退避，同一后端进程内复用已经完成的预置；重启后仅在没有受理未知提交时重新预置。其他确定拒绝仍以有界、脱敏诊断记录失败。终态日志或 custody 传输成功后，下一次调度循环即可推进收尾；传输失败保留退避，清理仍须通过资源释放与文件完整性检查。

Slurm 已确认执行终态、仅等待资源释放时，10 秒后复查；传输失败仍退避 60 秒。准备失败或用户取消时，先等待两个准备分支全部退出，再持久登记清理。后端正常关闭保留未完成准备的可重试状态。

### Docker 启动与收尾恢复

Docker 启动同样在实际调用前持久登记提交尝试，使用确定的容器名及精确归属标签。CLI 限时 60 秒；回复丢失后保留尝试和工作区，对账只关联标签匹配的容器，不重复启动。明确拒绝且确认容器不存在后才可清理；受理未知时查不到或出现归属冲突，交由管理员对账，不自动重提。

Docker 执行终态先落库，再收尾。取消只停止容器，直到最终日志和清理完成才解除关联。日志限时 30 秒流式写盘，完整快照原子替换保留文件；读取失败保留容器，30 秒后重试，后端重启也可继续恢复。仅在最终日志取得成功或确认容器已被外部删除后清理容器；外部删除时保留已经取得的日志。

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

所有路径基于 `{magnus_root}/workspace/jobs/{job_id}/`（下文简称 `{work}/`）。三个临时产物（overlay image + apptainer tmp/cache）则落在 `{magnus_ephemeral_root}/workspace/jobs/{job_id}/`（简称 `{ephemeral}/`）；`ephemeral_root` 缺省等于 `root`，所以除非站点配置 `server.ephemeral_root`（把高频随机写的 overlay 放到更快的盘），否则 `{ephemeral}/` == `{work}/`：

| 路径 | 生命周期 | 写入方 | 读取方 | 说明 |
|------|----------|--------|--------|------|
| `{work}/repository/` | prepare → cleanup | resource_manager | 容器 (bind) | git checkout，容器内的工作目录 |
| `{work}/wrapper.py` | submit → cleanup | scheduler | SLURM | 生成的执行入口 |
| `{work}/slurm/output.txt` | submit → 保留(受 workspace_size_cap 淘汰) | SLURM | API (日志) | sbatch --output 指向此处 |
| `{work}/.magnus_user_script.sh` | wrapper 执行 → cleanup | wrapper.py | 容器 (bind) | 用户入口脚本 |
| `{work}/.magnus_success` | epilogue → sync_reality | wrapper.py | scheduler | 成功标记，存在即 SUCCESS |
| `{work}/.magnus_oom` | epilogue（仅在 ret≠0 时写）→ sync_reality / cleanup | wrapper.py（探测 cgroup memory.events） | scheduler | OOM 标记，存在即表示 job 所在 cgroup 内发生过内核 OOM-kill；调度器据此把 `job.result` 改写为内存超限提示，覆盖通用 FAILED 字符串。Docker 模式改用 `docker inspect .State.OOMKilled` 取代此文件。 |
| `{work}/.magnus_result` | 容器内用户写入 → API 读取 | 用户代码 | routers/jobs.py | 任务结果内容 |
| `{work}/.magnus_action` | 容器内用户写入 → API 读取 | 用户代码 | routers/jobs.py + SDK | 客户端动作指令 |
| `{ephemeral}/ephemeral_overlay.img` | Phase 2 → finally | wrapper shell | apptainer | 可写层，job 结束后删除 |
| `{ephemeral}/.magnus_tmp/` | Phase 2 → cleanup | apptainer | apptainer | APPTAINER_TMPDIR |
| `{ephemeral}/.magnus_cache/` | Phase 2 → cleanup | apptainer | apptainer | APPTAINER_CACHEDIR |
| `{work}/metrics/` | submit → 保留(受 workspace_size_cap 淘汰) | wrapper sidecar + 用户代码 | routers/metrics.py | Magnus Metrics Protocol v1 JSONL 指标文件 |

当 `ephemeral_root` 拆到独立盘时，`{ephemeral}/` 由后端账户创建，而 job 可能以别的 OS 用户运行，因此提交阶段给该目录授予与 working table 在仓库准备阶段相同的 runner + 后端账户 ACL（共享 `setfacl` helper）——runner 需在此创建 overlay / apptainer tmp，后端需在 cleanup 时回收它们。

**cleanup** 指 `_clean_up_working_table()`，在 job 结束（SUCCESS/FAILED/TERMINATED/PAUSED）时调用。它是 keep-whitelist：只保留 `slurm/`、`metrics/`、`.magnus_result`、`.magnus_action`、`.magnus_custody_receipts.json`，working table 下其余一切都删——包括用户写进 workspace bind mount 的任意文件（例如直接把 checkpoint / 输出堆进 `$MAGNUS_HOME/workspace` 而不走 `file_custody` / `.magnus_result`）。`ephemeral_root` 拆分时独立的 `{ephemeral}/` 目录整体删除。

上述执行结束状态的详情、result/action 接口优先读取保留的用户 marker，不依赖数据库是否保存结果 sentinel 或调度诊断。文件不存在时退回原内联值（sentinel 指向的文件缺失则返回 null）；活跃执行仍只按已有 sentinel 读取。读取不修改 ORM 字段或任务状态。SSH 取消/抢占在正常终态分类前已设置状态，因此在产物恢复阶段补取 marker；取回失败阻止清理，并可在服务重启后重试。

自动恢复只在上一轮回传完成后开始新尝试。`Paused` → `Preparing` 的条件更新提交前，先清除旧 result/action 字段和执行 marker，避免本轮准备失败时返回上一轮结果。marker 重置失败则保持暂停、等待重试；并发取消不能被恢复操作覆盖。

重置先把旧 marker 重命名到 Job 内的 `.magnus-output-reset-*` 目录，转换失败时恢复原文件。中断或恢复失败留下的目录不会被普通工作区清理删除，并会阻止自动恢复，等待维护取回；整个工作区仍受原有总量淘汰上限约束。转换成功后删除临时备份。

保留下来的产物（`slurm/`、`metrics/`、marker）还受一个跨-job 的总量上限约束：`server.scheduler.workspace_size_cap` 限定 `workspace/jobs` 的总大小，低频调度 janitor（`_reclaim_workspace_over_cap`）在终态总量超限时按 mtime 从最旧的终态 job 目录起整目录淘汰——绝不碰活跃 job，且只删磁盘产物（DB job 记录保留）。


### Custody 运行中回传

SSH tenant 的 SDK 把完整载荷与 `meta.json` 写入隐藏暂存目录，再原子改名为 `relay-<uuid>`；仅发布后的条目可导入。控制器在后台增量发现，保留旧版缺少 `storage_filename` / `size_bytes` 的元数据兼容。已登记的 token 不再读取远端文件，不能修改其元数据来更新内容或下载次数；新快照使用新 token。

临时 custody 的载荷、TTL、次数和导入身份保存在本地目录与 `_custody.sqlite3`。发布和导入身份在同一事务提交；即使在写 Job 回执之前被领完并重启，也不会重复导入。Job 回执持久化后才移除临时导入保护记录。读取领取先持久化计次再交出文件句柄，实际读取完成前保留磁盘占用；停止服务保留完整条目、取消未完成的接收并回收子进程。旧 permanent 登记格式保持不变。

运行中的 Job 成果暂不倒计时；调度器确认整个执行终止后启动 TTL，暂停与取消请求本身不触发。尚在回传的文件就绪后获得完整 TTL，已开始的期限不再延长。不能以全量回传完成作为起算条件，否则容量用尽时回传和过期会互等。未关联 Job 的上传仍从发布起算。HTTP 可选 `job_id` 只接受所属用户的活跃 Job，新版 SDK 在站点与凭据均匹配时自动传递 Job ID。

每条 SSH ControlMaster 链路共享两个发现/小文件/日志 worker 与一个大文件/终态日志 worker（载荷阈值 8 MiB）；各 Job 同一 token 只有一个接收者。目录发现和小文件有短时预算，大文件和终态完整日志使用配置中的传输预算；有界发现队列使用游标轮转并优先保留小文件，失败大包不能令目录扫描停下来。先预留总量/单文件容量，再有界接收到 custody，不另存一份完整 staging 副本。未导入的远端内容和收尾状态保留，直到传输成功；运行中回传不改变计算代码或调度决策，也不遍历未通过 custody 发布的任意结果目录。

bulk worker 每传一个文件便释放轮次，让其他排队 Job 获得机会。同一 Job 内优先选择可传输的最小载荷，但连续让行三次后，最早发布且可传输的载荷获得一次机会。先后顺序取不可变元数据文件的修改时间，同时间以 token 稳定排序，避免回收描述符或重启把旧成果变成新到项；发现缓存回收描述符时保留最老的 bulk 项。因此后到的普通结果可以越过排队缓存，大文件也不会一直让行；不抢占正在进行的传输，也不保证就绪时间上限。失败退避保持，终态日志尝试失败后先让一个可传输载荷获得机会，再尝试日志。重启从出版目录与持久回执重建待传任务；调度顺序不是交付承诺，已完成的导入不会重复。

## 信号与终止

Magnus 区分两种针对运行中 job 的外部干预：

| 操作 | 入口 | 后端实现 | DB 状态变化 |
|------|------|----------|-------------|
| 终止 (terminate) | `POST /api/jobs/{id}/terminate`、`magnus job kill` | SLURM: `scancel --signal=KILL --full` + 裸 `scancel`；Docker: `docker stop -t 0 && docker rm -f` | 取消获确认后置为 `Terminated`；SLURM 工作区等实际释放及最终回传完成后再清理 |
| 发送信号 (signal) | `POST /api/jobs/{id}/signal`、`magnus job signal`（仅 Running） | SLURM: `scancel --signal=TERM --batch <jobid>`；Docker: `docker kill --signal=TERM` | 不修改任何状态 |

**两个动作的分工**：终止请求强制取消。取消未获确认时，保留 Job 状态和工作区；确认后标为终止，但资源占用仍以 SLURM 为准，实际释放并回传最终产物后才清理。回传失败时保留执行 ID 和工作区供重试，不阻塞其他任务收尾；保留的关联不等于物理资源仍被占用。发送信号只转发 SIGTERM，不改数据库状态。装有 handler 的用户进程可保存检查点、释放资源；未装 handler 的进程继承 user-script shell 的 `SIG_IGN`。需要强制取消时使用 terminate。

handler 想让自己保存的 partial state 算 success，就在 handler 里写 `$MAGNUS_RESULT`（容器内挂载在 `$MAGNUS_HOME/workspace/.magnus_result`），再 `sys.exit(0)`。即便外层进程树被信号强行带走，magnus 也会把 job 收敛到 `Success` —— 详见下方"状态收敛"。

**SLURM 链路（signal）**：`scancel --signal=TERM --batch <jobid>` —— `--batch` 让 SLURM 只把 SIGTERM 投递到 batch step 的 parent process（即 sbatch script `exec wrapper.py` 之后的 wrapper.py 本身），cgroup 其它进程保持不动。apptainer starter / fuse-overlayfs / squashfuse_ll 等容器基础设施进程没装 SIGTERM handler，被信号到会按默认 disposition 终止、把 squashfs / overlay mount point 拆掉，user 进程访问内存映射时直接 SIGBUS —— `--batch` 让它们保活。wrapper.py 自己再 fan-out 到 user 进程，用户脚本不动、无 marker file、无任何协议表面：

- **启动期窗口防御**：sbatch batch script 写成 `#!/bin/bash\ntrap '' TERM\n\nexec <wrapper>`，外层 bash 在做任何事之前装 SIG_IGN，再由 POSIX exec 继承把 SIG_IGN 带进 wrapper.py 进程。从 wrapper.py 启动到 `main()` 里 `signal.signal(SIGTERM, _on_sigterm)` 之间那个极短窗口被覆盖 —— 这期间到达的信号被吞掉而不是按 SIG_DFL 杀掉 wrapper.py。
- **基于 NSpid 的 wrapper-side fan-out**：`_on_sigterm` 先把 `_signaled[0]` 置为 `True`（forward 失败 Phase 3 marker fallback 仍能触发），然后调 `_signal_user_processes(SIGTERM)`。该 helper 读本 job 的 cgroup.procs（hybrid 系统走 v1 freezer，pure v2 走 unified），然后对每个 host PID 看 `/proc/<host_pid>/status` 的 `NSpid:` 字段 —— 这一行列出进程在它所属的每层 PID namespace 内的 PID。单列 = 进程只在 host namespace（wrapper 自己、subprocess shell、apptainer starter、fuse-overlayfs、squashfuse_ll 等基础设施）；双列 = 进程也在子 PID namespace 里，即在用户容器内。helper 跳过单列 PID（host-only 基础设施）和双列里 inner==1 的（apptainer `appinit` / 容器 PID 1），对剩下的发 `kill(host_pid, SIGTERM)`。`kill(2)` 用 host PID 跨 PID namespace 直接生效 —— 内核 PID 是 host-global 标识，不需要 `nsenter` / `setns`。用户 `entry_command` fork 出的任何东西 —— main、fork children、MPI workers、嵌套 shell —— 都在子 namespace 里、都收到信号。同 `systemd KillMode=cgroup` 和 Kubernetes 优雅终止是同一套思路。各层都 fail-open：cgroup 读失败、status 解析失败、单个 kill 失败任一步 continue 不抛回 main。
- **用户脚本 SIG_IGN 守卫**：渲染出的 `.magnus_user_script.sh` 入口装 `trap '' TERM`，跑用户 `entry_command` 的那个 bash 拿到 SIGTERM 的 SIG_IGN。wrapper fan-out 沿 NSpid 也会信号到这个 bash；SIG_IGN 让它不被默认 disposition 杀掉，`wrapper.subprocess.call` 因此不会提前返回，SLURM step 也就不会在 user handler 还跑着的时候把整组进程拆掉。SIG_IGN 通过 POSIX exec 继承给后代；用户代码用 `signal.signal(SIGTERM, …)` / `sigaction(2)` 装 handler 会自然覆盖。
- **用户进程**：容器内用户进程从 user-script bash 继承到 SIG_IGN 的 SIGTERM disposition。没装 handler 的用户代码下，从 UI 按 send-SIGTERM 是 no-op —— 这是 Unix 的标准结果：没有把优雅终止接进来的程序，SIGTERM 就是不做事；想强制杀走 terminate 按钮（走 `kill_job` 的 SIGKILL 路径）。装了 `signal.signal(SIGTERM, …)`（或 C 的 `sigaction(2)`、Fortran 的 `SIGNAL` intrinsic 等任何 POSIX 进程的信号 idiom）的用户代码会覆盖继承的 SIG_IGN，自己跑收尾、自己定 exit code；handler 里写 `.magnus_result` 让 `sys.exit(0)` 算成功（见下方"状态收敛"）。协议送的是一发普通的 SIGTERM，用户那边走标准 `sigaction(2)` 语义 —— magnus 这边没有需要额外学习的东西。

**SLURM 链路（terminate / 抢占）**：`kill_job` 因此用 `scancel --signal=KILL --full` 直接 SIGKILL 全员清场（SIGKILL 在内核侧不可被 ignore），再裸 `scancel <jobid>` 让 SLURM 把 job state 转为 CANCELLED。如果走默认的 `scancel <jobid>`（KillSignal=SIGTERM、KillWait 后 SIGKILL），整个 KillWait 窗口都会被烧掉 —— wrapper.py 的 handler 不退而是 forward，handler-aware 的用户代码会用这段 grace 跑自己的 graceful shutdown，而不是立刻让出资源。SIGKILL 直发让 terminate 与抢占的延迟回到秒级。

**Docker 链路（signal）**：两层接力，跟 SLURM 在用户进程层面对等：

1. `docker run --init` 让 docker 自带的 tini 当 PID 1，reap 孤儿进程并把 SIGTERM 转发给直接子进程（外层 bash）。tini **不**像 SLURM proctrack 那样广播给容器内全员，只送给外层 bash —— 这正是为什么需要下面的脚本层接力。
2. `.magnus_user_script.sh`（由 `_scheduler/_submit.py` 的 `_render_docker_user_script` 渲染）开头 `set -m` 启用 bash monitor mode，然后把用户的 entry_command 放进 `( set -e; ... ) &` 后台子壳里 —— 在 `set -m` 下子壳自动 setpgid 成为新的 pgrp leader（`_magnus_pid` 同时是 PID 和 pgid）。不开 monitor mode 的非交互 bash 默认让后台子壳继承外层 pgid，负 PID kill 会指向一个不存在的 pgrp，转发被吞。外层 bash 装 `trap 'kill -TERM -- -$_magnus_pid' TERM`，SIGTERM 通过 `killpg` 转给整个 user pgrp —— main、fork 子进程、MPI workers、嵌套 shell 全员收到。`while ... wait` 循环：wait 被信号中断后返回 128+sig，重发 wait 直到子进程真退，最后用 `exit $?` 透传子进程真实退出码。外层 bash 装了非空 trap 不会 deferred terminate。

合起来两边在用户进程层面行为一致：子壳同样装 `trap '' TERM`（在 `( … ) &` 子壳入口），跟 SLURM 一侧 `.magnus_user_script.sh` 的守卫对等，让外层 trap fan-out 时子壳不被杀、保护正在跑的 handler 不被中途打断；没装 handler 的用户代码因继承 SIG_IGN 把 SIGTERM 当 no-op，装了 handler 的代码用自己的 `signal.signal` / `sigaction` 自然覆盖、跑 handler、自己定 exit。pgrp 转发把 entry_command 内部多层嵌套 shell 也覆盖到了 —— 用户代码不需要自己显式装 trap 转发。

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

默认 `execution.container_image_mode: native` 将 SIF 直接交给运行时，
可写层取决于隔离模式：

| 模式 | 可写层 | 容量限制 | 说明 |
|------|--------|----------|------|
| containall + overlay | ephemeral overlay (sparse ext3 image) | `ephemeral_storage` | 默认路径（rootless apptainer），`--sparse` 创建瞬间完成且按需占用磁盘 |
| containall/contain + writable-tmpfs | RAM tmpfs | 与 `memory_demand` 共享 | setuid apptainer 或 `MAGNUS_NO_OVERLAY=1` |
| none（裸跑） | host 文件系统穿透 | 无限制 | `MAGNUS_CONTAIN_LEVEL=none`，等效 overlay 出现之前的行为 |

### 运行时镜像准备

部分非特权 Singularity 安装每次执行 SIF 都会先展开目录。多 task 并发因此可能耗尽
共享文件系统的文件数量配额，即使压缩镜像很小、字节容量仍充足。这类 Slurm 目标
可配置 `execution.container_image_mode: sandbox`。
此模式要求执行 UID 拥有 Job 临时目录。本地 Slurm 仅允许后端默认 runner，跨用户
runner 继续使用 `native`。目录镜像权限可能屏蔽继承的清理 ACL，因此在转换前拒绝
不满足属主条件的配置。

wrapper 在启动 task 前，使用选定运行时原生的
`build --sandbox --notest --disable-cache`，从已经送达的本地 SIF 转换一次。
GNU `timeout` 将转换限制在 `execution.container_prepare_timeout_seconds`
（默认 300 秒）内，之后有 5 秒终止宽限。准备过程占用原 Slurm 分配与时间上限。
转换失败或超时不会启动用户 task，也不会写成功标记。每次尝试在
`{ephemeral}/.magnus_tmp/` 下创建独立的 Job 私有目录，不复用半成品。

所有 task 只读执行同一目录镜像，不添加 overlay 或 `--writable-tmpfs`，workspace
继续可写。按 Job 和节点隔离的目录显式绑定到 `/tmp`、`/var/tmp` 和
`$MAGNUS_HOME/.tmp`；前两者共享目录，SDK 中转文件使用另一目录。
同节点 task 共享这些临时路径，不同节点及不同 Job 互不共用。`TMPDIR` 默认设为
`/tmp`，entry command 可以覆盖。依赖应放入镜像，此模式不支持任意修改镜像根目录；
`ephemeral_storage` 不是这些目录写入的硬配额。

准备目录和临时目录与现有 workspace 一样，必须对分配的节点可见。SSH 目标使用
远端 Job 目录，不能把控制面的 `server.ephemeral_root` 当作远端路径。现有终态
清理负责回收这些目录及取消、异常退出后的残留，不新增持久 sandbox 镜像缓存。
删除前仅恢复清理用户所拥有目录的读写和遍历权限，不改变文件权限或跟随镜像符号链接。
转换期间可能暂时需要超过一份完整镜像的解包空间，容量配置也应考虑多个并行 Job。
默认 `native` 模式及 Docker/local 行为保持；sandbox 准备同时支持 Singularity 与
Apptainer。

### SDK 运行环境检测协议

SDK 的 `file_transfer.get_tmp_base()` 用以下判据决定文件中转目录（上传 tar 压缩、下载 tar 解压的临时文件）：

1. 环境变量 `MAGNUS_HOME` 存在
2. `$MAGNUS_HOME/workspace/` 是已存在的目录（由 Magnus runtime 创建并 bind-mount）

两个条件都满足时，中转目录为 `$MAGNUS_HOME/.tmp/`，否则使用系统临时目录。
native 模式由容器可写层提供该路径；sandbox 模式在同一路径显式绑定按 Job 和节点
隔离的可写目录，已有 SDK 无需改变调用方式。

**为什么不用 `/tmp`**：在两种隔离模式下 `/tmp` 都不是理想的中转位置：

| 模式 | `/tmp` 位置 | 问题 |
|------|-------------|------|
| overlay | overlay 镜像内 | 与 pip install 等容器内写入共享 `ephemeral_storage` 配额 |
| writable-tmpfs | RAM tmpfs | 受运行时 session 目录配置及 Job 内存限制，大文件可能触发 ENOSPC |

native 模式的 `$MAGNUS_HOME/.tmp/` 与 `/tmp` 共享容器可写层的预算，但分开存放
中转文件。显式绑定的 Job 目录也是运行时隔离边界的一部分，不能仅凭是否处于可写层
判断文件系统隔离是否成立。

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
  [--partition={partition}] \            # GPU 类型的 partition，否则 execution.slurm.partition；为 null 时不下发
  [--qos={qos}] \                        # execution.slurm.qos，为 null 时不下发
  [--account={account}] \                # execution.slurm.account，为 null 时不下发
  --output={work}/slurm/output.txt \
  [--open-mode=append] \                 # 非覆盖输出时
  --gres=gpu:[{gres_type}:]{gpu_count} \ # 仅 gpu_count > 0；无型号 gres 时省去型号名
  --mem={memory_demand} \                # 仅 explicit 内存模式（见下）
  --cpus-per-task={effective_cpu_count} \
  [--time={time_limit}] \                # 单 job 墙钟（分钟），为 null 时不下发
  # 脚本内容: {module load ...}; exec python3 {work}/wrapper.py
```

`partition` / `qos` / `account` 仅在 `execution.slurm` 配了才下发；自有站点留 null、不带这些 flag（与历史命令字节级一致），共享集群租户场景在此显式设置。`execution.slurm.module_loads` 的每一项会作为 `module load ...` 前置进 batch 脚本，在运行时上 `PATH` 之前执行。

**按 GPU 类型路由 partition + gres**（`cluster.gpus[]`）：每个 GPU 类型可声明可选的 `partition` 与 `gres_type`。job 请求某 GPU 类型时提交到该类型的 `partition`（未设则回退 `execution.slurm.partition`），`--gres` 用其 `gres_type` —— 缺省 = 类型名（`--gres=gpu:{value}:N`），显式 null 时为无型号 `--gres=gpu:N`。CPU job 以及没配 `partition` 的 GPU 类型都走默认分区。这让单个站点能驱动"CPU 与各 GPU 型号分处不同 SLURM 分区"的共享集群（如 CPU 走默认分区、A100-80G 走独立的 `gpu-a100` 分区且 gres 无型号），或在同一分区靠带型号 gres 承载多种 GPU（`--gres=gpu:rtx5090:N` vs `--gres=gpu:a100:N`）。单分区站点两个字段都不设、命令字节级不变。tenant 模式下 cluster 视图会把默认分区 + 每个不同 GPU 分区的容量与在跑任务聚合展示。

**内存模式**（`execution.slurm.mem_mode`）：
- `explicit`（默认，自有站点）：`--mem={memory_demand}` 请求的是**每节点内存**，与每节点 task 数无关。`--mem=0` 表示请求全部可用内存，不能仅凭请求计算总量。
- `per_cpu`（禁用 `--mem` 的集群）：不发 `--mem`。SLURM 按 `DefMemPerCPU` 分配内存，`memory_demand` 折算进核数：`effective_cpu_count = max(cpu_count, ceil(memory_demand_mb / mem_per_cpu_mb))`，所以请求更多内存会顶高核数。提交链路将存储的 `cpu_count` / `memory_demand` **归一化**为有效的**每 task 请求值**（`normalize_per_cpu_resources`，在填默认值之后、上限校验之前）。该折算与 sbatch 构建共用同一 helper（`effective_cpu_count_per_cpu`），且幂等。这两个字段不是全 Job 分配量，也不是实测内存使用量。

**多节点资源展示**：`node_count` 和 `tasks_per_node` 默认均为一。CPU 按 task 请求（`--cpus-per-task`），GPU 按节点请求（`--gres`）。Job 列表和详情响应保留原请求字段，并新增供展示的 `resource_summary`：

| 全 Job 请求量 | 计算方法 |
|---|---|
| CPU | `cpu_count × node_count × tasks_per_node` |
| RAM，`per_cpu` | 归一化后的 `memory_demand × node_count × tasks_per_node` |
| RAM，`explicit` | `memory_demand × node_count` |
| GPU | `gpu_count × node_count` |

例如两节点、每节点 64 tasks、每 task 1 CPU、每 CPU 4000 MiB，合计请求 **128 CPU、500 GiB**。表格和详情展示总量并提供拓扑明细。`resource_summary.memory_bytes` 单位为字节；null 表示总量未知，而非零。新 Job 保存服务端生成的 `resource_mode` 快照（`slurm_per_cpu`、`slurm_per_node` 或 `local`）。旧 Job 的快照保持 null，其请求摘要按当前站点配置推算并设置 `mode_inferred=true`，UI 标为估算；不回填历史记录来断言未保存过的口径。

集群运行列表对 Magnus 和外部 Job 统一采用 SLURM 的全 Job 分配量（`resource_summary.basis="allocation"`），不重复乘节点/task。普通列表与详情使用 `basis="request"`；两者都不是实时使用率。Local/Docker 摘要展示提交值，不乘未支持的节点/task 拓扑，也不宣称资源限额被强制执行。原 API 字段与 SDK 提交语义保持不变。

`time_limit`（分钟）映射到 `--time`；为 null 时不下发，沿用分区默认墙钟。声明短墙钟的 job 在共享集群更易被 backfill 插队。

环境变量 `MAGNUS_RUNNER` 和 `MAGNUS_TOKEN` 通过 sbatch 的进程环境传递。

### 资源核算边界

请求、分配和使用量采用不同证据源。提交上限校验与请求摘要共用内存单位约定（SLURM 无后缀内存值为 MiB）；按 CPU 折算时直接向上取整，不先丢掉不足一 MiB 的部分。显式内存先按每节点向上取整到 MiB，再乘节点数，sbatch 使用相同数值。磁盘容量保留独立的字节约定。通过旧版 SLURM 请求字段推算的分配带有 `allocation_inferred=true` 和来源字段，不冒称直接的分配证据。

集群卡片展示所选节点并集的可用 CPU、内存、GPU／配置容量。可用量为配置容量减去节点分配量，下限为零；不再额外扣减准入限制。CPU／内存分配量取各节点的 `AllocTRES`，可回退到 `CPUAlloc`／`AllocMem`。GPU 使用详情中的 `GresUsed`，其次是明确的 GPU TRES；仅缺少 GPU TRES 不能证明用量为零；它既非实时利用率，也不保证立即可放置新任务。draining／保留节点上的运行任务只计一次。整条 Job 可跨出节点池，也可能在节点计数已变化后仍处于 CG，因此不把 Job 行直接求和当作卡片数字。API 的 `cpu_used`、`mem_used_mb`、`gpu_used` 按维度保留 `null`，不把未知冒充零；旧数字字段 `used`／`free` 保留，free 表示配置容量减分配量，观察不完整时以 `allocation_tracked=false` 保护旧客户端。节点准入限制继续留在调度器内。Local/Docker 未跟踪分配，显示未知。

不完整核算或查询失败不能变成“空集群、全部空闲”。容量和分配的组合观察携带时间戳；缓存旧值有时间边界并标明陈旧。刷新失败返回 503，不生成看似新鲜的零快照。历史 GPU 采样按已观察 SLURM ID 匹配 Magnus 归属并累加实际分配，覆盖 CG 和数据库状态滞后；失败、GPU 分配仅为推算或跨出统计范围时，不写入伪造的精确历史。既有样本原样保留。GPU 历史要求所有 GPU 节点的 GPU 分配已知，并只校验 GPU 分配的可见性；隐藏的 CPU 或内存作业不会使已知 GPU 样本失效。所有采样尝试（包括跳过或失败）均遵循 `snapshot_interval`；Authoritative 准入仍校验全部资源维度。

Authoritative 调度使用全 Job 的 CPU/RAM/GPU 需求及实际 GPU 型号。已接受但尚未在分配快照中出现的任务阻止继续准入，避免重复承诺同一份空闲容量。布局完整可知的单节点场景中，backfill 仅消耗扣除队头预留后各维度当前剩余的资源。这是保守资源预留，**不是基于时间预测的 EASY**；原来的 `head + candidate <= total` 条件不能证明不会延迟队头。抢占必须以 B 类任务的已观察分配构成完整方案，同时补齐各项资源缺口；无关 GPU 型号、推算的分配或不完整方案不能成为终止任务的理由。取消后重新观察真实释放。

复杂/多节点布局中，聚合总量不能证明节点放置可行。Magnus 只将优先队头交给 SLURM，并等待其排队/分配状态；不会根据未证明的放置假设抢占或旁路。未知默认值、请求全部节点内存等也交 SLURM 解析。Tenant 模式继续将排队和抢占交给 SLURM。这些规则调整准入判断，不改变请求的 SLURM 布局。

系统指标标记实际采样范围。SLURM batch wrapper 的 CPU/RAM 样本带 `scope=batch_step`；使用 `srun` 时，MPI ranks 可能位于同级其他 cgroup，所以覆盖标为 `partial`，不是全 Job 使用量。Docker CPU/RAM 使用 `scope=container`。GPU 样本使用 `scope=device`：设备指标不是按进程归属统计的用量，node 0 也不能代表其他节点。CPU 样本携带归一化使用的 CPU 数，超过 100% 的值原样保留，不掩盖采样范围或配额差异。UI 对新样本和历史多 task 任务都说明覆盖边界。本次没有引入分布式指标采集器。

## scheduler 心跳与状态同步

心跳间隔 `scheduler.heartbeat_interval`（默认 2 秒），每次 tick:

1. **`_sync_reality`**: 遍历 QUEUED/RUNNING job，用 `squeue` 查真实状态
   - SLURM 报 RUNNING → DB 标 RUNNING
   - SLURM 报 COMPLETED + `.magnus_success` 存在 → SUCCESS，同时检查 `.magnus_result` 和 `.magnus_action`
   - SLURM 报 COMPLETED 但无 `.magnus_success` → FAILED
   - SLURM 报 FAILED/CANCELLED/TIMEOUT → FAILED
2. **`_make_decisions`**: 调度 PENDING/PAUSED job
3. **`_record_snapshot`**: 每 `snapshot_interval`（默认 300 秒）记录集群快照
4. **`_reclaim_workspace_over_cap`**: 至多每小时一次，当 `workspace/jobs` 下终态 job 目录总量超过 `scheduler.workspace_size_cap` 时按最旧优先滚动淘汰

## 资源准备

在 PREPARING 阶段并行执行：

**镜像拉取** (`_resource_manager.ensure_image`):
- 新 SIF 文件名使用镜像 URI 的完整 SHA-256。已登记的旧文件名继续有效；不能仅凭未登记的旧文件名猜测镜像身份。
- 缓存目录 `{magnus_root}/container_cache/`，LRU 淘汰，目标容量为 `resource_cache.container_cache_size`。准备、排队和执行中的 Job 持有镜像保护，直到提交及资源清理结束；重启后先恢复保护再调度。只淘汰空闲条目，因此受保护的镜像可能超过缓存目标；原有剩余空间检查仍会在余量不足时拒绝拉取。这是采样检查，不是文件系统配额。
- Apptainer 和 Docker 均以 per-image asyncio.Lock 防止重复拉取。取消或服务关闭时，所有者等待拉取进程和 scratch 清理结束再退出。删除仍被 Job 使用的 SIF 返回 HTTP 409，并保留缓存登记。
- 3 次重试 + 指数退避，非瞬态错误（unauthorized, not found）直接失败

**仓库克隆** (`_resource_manager.ensure_repo`):
- 缓存目录 `{magnus_root}/repo_cache/`，每个 (namespace, repo, branch) 一个目录、名字带哈希后缀，不同三元组不会共用目录；LRU 淘汰，上限 `resource_cache.repo_cache_size`
- 按需在缓存里 fetch → copy 到 `{work}/repository/` → checkout 到指定 commit SHA
- clone 与 fetch 各有 3 次重试 + 指数退避；磁盘满直接失败，不重试
- 用不了的缓存目录（本身不是 git 仓库、HEAD 解析不到 commit、残留 `index.lock` —— clone 被杀、拷贝中断、`.git` 损坏留下的现场）按未命中处理：删掉重新 clone
- job 重新进入准备阶段（例如后端重启后）时重建 `repository/` 副本，不沿用残留的那份
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
    workspace_size_cap: 50G                 # 终态 job 工作区目录总量上限 (LRU); null 关闭
    allow_root: false                       # 是否允许 root runner
  resource_cache:
    container_cache_size: 80G               # SIF 缓存上限 (LRU)
    repo_cache_size: 20G                    # git repo 缓存上限 (LRU)

cluster:
  default_cpu_count: 4
  default_memory_demand: 1600M
  max_cpu_count: 128                       # 提交时强制的上限（超出转 HTTP 400）
  max_memory_demand: 256G                  # 提交时强制的上限
  default_time_limit: null                 # 默认 --time（分钟）；null = 不下发 --time
  max_time_limit: null                     # 墙钟上限（分钟）；null = 不限
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

execution:
  backend: slurm                           # slurm | local
  container_runtime: apptainer             # apptainer | singularity（HPC）；docker（local）
  slurm:                                   # SLURM 提交方言；每项都带保持现状的默认
    partition: null                        # null = 不下发 --partition
    qos: null                              # null = 不下发 --qos
    account: null                          # null = 不下发 --account
    mem_mode: explicit                     # explicit（下发 --mem）| per_cpu（折核数、不发 --mem）
    mem_per_cpu_mb: 4000                   # 仅 mem_mode == per_cpu 时使用
    module_loads: []                       # batch 脚本里前置的 `module load` 行
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

## 协议文件与 Service 边界

宿主侧的结果、日志与指标 I/O 仅访问 Job 根目录内的普通文件，拒绝由 runner 控制的符号链接、junction、特殊文件与硬链接；远端完整下载后原子发布。协议标记应直接写普通文件。这不改变既有项目盘绑定挂载契约。

Service 代理由 Magnus 消费平台认证：Bearer Authorization、`token` 查询参数和 `token`/`access_token` Cookie 不会传给用户应用。业务 Cookie 与重复查询参数保留，上游重定向交给客户端处理。这一凭据边界不等同于任意 Service HTML 的浏览器同源隔离。

Service 普通更新保留所有者，显式 transfer 才改变所有权。停止后，只要准备、提交或外部分配尚未结束，就保留 Job 与端口关联；释放前删除可返回 409，停止未确认则返回 503 并保留关联。提交的迟到回复保留并发终止状态，登记外部 ID 后申请取消；取消未确认由同步重试。外部已受理但未收到 ID 的情形仍需独立对账。


## 预置取消与用户归属

远端输入预置期间终止 Job，会取消该 Job 自己的 SCP 或二进制流，等待传输线程退出并释放
custody 读取租约后，才允许回收工作区。共享 SSH 主连接及其他 Job 不受影响；控制命令
保持有界超时。服务关闭会取消并等待预置收尾，未提交的 Job 保持 Pending，供下次启动继续。

有现存记录（包括已完成 Job 历史）的 agent 删除请求返回 409，不删除历史或清空 owner。
创建或转交有归属的记录时，在同一数据库事务中保留用户写入顺序，避免已鉴权请求在用户
删除后创建悬空记录。无依赖的 agent 仍可删除；不要求客户端升级或数据库迁移。


请求线程与后台回收按 Service 分别串行处理生命周期决策。代理请求取消后，等待启动线程
结束再释放保护锁。端口分配先取得 SQLite 写事务，再查可用端口，与新 Job 关联一并提交；
外部取消命令不在该数据库写事务内运行。

SLURM 上限按每节点校验：`cpu_count × tasks_per_node` 不得超过节点 CPU 上限。
`per_cpu` 模式下，归一化后的每 task 内存同样乘以 `tasks_per_node`；显式 `--mem` 已是
每节点值，不再乘。`node_count` 不参与每节点上限乘算，Docker/local 口径不变。

### 远端准备诊断

`GET /api/jobs/{id}/staging` 返回准备阶段、适用时的输入序号、已知对象大小、操作预算和耗时。完成字节数指已准备好的对象，也包括复用的镜像，不代表实测网络流量；目录大小未知时不填写。控制进程中断会明确记录。

执行输出尚不存在时，现有日志接口显示简短状态；出现执行输出后保持原分页。快照不记录路径、命令或取件码，独立于工作区回收，最多保留最近 4,096 个非活动 Job。准备失败时 Job result 另存安全摘要。
