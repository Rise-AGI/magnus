# 多执行集群

一个 Magnus 站点可以接入多个 Slurm 集群，共用用户、本地构建器、有界缓存和 Job
数据库。每个 Job 选择一个执行目标，不自动跨集群迁移或重复投递。

资源统计不可用时，`/api/cluster/stats` 保持原有 HTTP 503 响应。后端日志记录执行目标、
异常类型、数值形式的进程/系统状态及代码位置，每个目标每分钟最多一条。此诊断不包含
命令参数、异常消息、原始输出或 traceback 局部变量，便于区分传输和解析/校验失败，
避免泄露凭据，也不把未知容量记为零。

原有顶层 `transport`、`execution.slurm`、`execution.container_runtime` 和 `cluster`
继续描述默认目标，旧配置无需修改。额外目标通过可选覆盖项声明：

```yaml
execution:
  backend: slurm
  target_id: primary
  target_name: Primary
  # 原有运行时、构建器和 Slurm 设置仍放在这里。
  targets:
    secondary:
      name: Secondary
      transport:
        mode: ssh
        ssh:
          control_path: /home/operator/.ssh/secondary.sock
          host: secondary.example.org
          user: account
          remote_root: /shared/home/account/magnus-data
      slurm:
        partition: compute
        account: project
        mem_mode: per_cpu
        mem_per_cpu_mb: 4000
        module_init: /etc/profile.d/modules.sh
        module_loads: [singularity/3.11]
        mpi_type: pmi2
      container_runtime: singularity
      cluster:
        max_cpu_count: 64
        max_memory_demand: 256G
        max_node_count: 8
        default_runner: account
        scheduling: {mode: tenant}
        gpus: []
```

这是完整站点配置中的片段。Slurm、cluster 字段继承默认目标；传入 transport 时整个
替换，避免把另一个目标的凭据和路径意外带入。构建器、存储预算、认证与服务端配置
属于整个站点，不能按目标覆盖。独立执行环境使用独立远端根目录和 control socket。
多目标要求 Slurm 后端；Docker/local 保持原有单机行为。

目标还可覆盖 `container_image_mode`（默认 `native`）和
`container_prepare_timeout_seconds`（默认 300）。对于每次执行 SIF 都会解包的运行时，
`sandbox` 会在启动各 task 前为整个 Job 准备一份只读目录镜像。
可写临时目录、同用户属主要求与回收边界见[运行时镜像准备](../internals/job-runtime.zh-CN.md#运行时镜像准备)。

目标 ID 使用字母、数字、`_`、`-`，以字母或数字开头，最多 64 字符。ID 与默认目标
的身份应保持稳定。新 Job 保存解析后的 ID，即使调用方没有指定。旧库 NULL 目标
归属原有默认部署。移除仍有未完成 Job 的目标会阻止启动，不会把它们转到其他集群。
目标退役后，终态 Job 历史仍可读取。Service 同样保存目标；旧客户端更新时省略此字段
会保留原选择，切换目标前需先停止服务。SSH 执行仍不支持原有的本地服务代理。

## 提交与查看

多集群站点在 Job drawer 和 Cluster 页面显示选择器。克隆和剪贴板配置保留目标。
Cluster 的任务列表包含所有目标上的 Magnus Job；“我负责的活跃任务”仍按当前用户
及下属筛选。分页前先排选中目标，再保留原有状态、优先级和时间排序。
外部 Slurm 任务和资源卡只覆盖选中节点池；运行列表中 Magnus Job 仍排在外部任务前。
页面只实时查询选中的 Slurm 连接，其他 Magnus Job 使用调度器同步到数据库的状态。

```python
targets = client.get_execution_targets()
job_id = client.submit_job(
    task_name="example",
    entry_command="python main.py",
    execution_target="secondary",
)
stats = client.get_cluster_stats(execution_target="secondary")
```

```bash
magnus cluster --targets --format json
magnus cluster --execution-target secondary
magnus submit --task-name example --entry-command 'python main.py' --execution-target secondary
```

`GET /api/cluster/targets` 返回目标及资源配置；`GET /api/cluster/stats` 和
`/api/cluster/my-active-jobs` 支持可选 `execution_target` 查询参数。Job 提交接受
同名字段。未知 ID 拒绝，省略时使用默认目标。SDK 新参数只能按关键字传入，旧位置
参数不变；显式选目标前会检查服务端能力，避免旧服务端悄悄忽略这个字段。源码更新
不代表已发布 PyPI 版本。

状态同步、取消与快照按目标区分 Slurm ID。Cluster 匹配会合并使用同一 SSH host、
账号和 control socket（或本地控制器）的分区目标；独立控制器上的同号 Slurm Job
不会被认成同一个 Magnus Job。每个调度 worker
最多同时有一个同步轮次和一个提交轮次；一个目标的慢远端 I/O 不阻塞其他目标。
本地工作区回收仍按全站预算运行，并保护所有目标的在途工作。Cluster 查询缓存和
认证锁分别按目标、control socket 隔离。

同一集群的不同分区可以继承相同 transport 和远端根目录。远端构建镜像缓存的锁与
保护集合覆盖使用同一 SSH host、账号和规范化根目录的所有目标，包括历史 Job 和
仍在释放资源的 Job；不同 control socket 不拆分这份缓存。共享目录应统一配置同一个
主机名和根路径，Magnus 不推断不同主机别名或远端目录符号链接是否指向同一位置。
独立远端缓存仍各自保有传输锁和保护集合。

## 官方接入客户端与续期

管理员可以把认证委托给已安装的接入客户端：

```yaml
transport:
  mode: ssh
  ssh:
    # control_path、host、user、remote_root 仍为必填。
    auto_connect:
      command: [/home/operator/bin/cluster-connect, primary]
      timeout_seconds: 90
      control_persist: 8h
```

`command` 是直接执行的 argv 数组，不经过 shell。它属于管理员配置，不能由 Job
传入；与原有 password/TOTP 设置互斥，原认证方式继续支持。Magnus 提供以下环境：

| 环境变量 | 含义 |
| --- | --- |
| `MAGNUS_SSH_CONTROL_PATH` | 需要建立的 OpenSSH control socket |
| `MAGNUS_SSH_HOST` | 配置中的执行主机身份 |
| `MAGNUS_SSH_USER` | 配置中的账号 |
| `MAGNUS_SSH_CONTROL_PERSIST` | 要求的 master 空闲存活时长 |

连接器应调用服务商支持的客户端与认证方式、严格验证固定的主机公钥、建立指定
socket，然后成功退出。Magnus 还会探活 master，单独返回零退出码不算成功。
SSH/SCP 只复用既有 master；socket 消失时不会回落到另一条连接路径。

仅在 master 不存在时调用连接器。同一 socket 的认证单飞，遵守当前操作的剩余时限，
超时终止连接器进程组（Windows 上为进程树）。连接器不得把未完成的认证 helper
脱离出去。连接器输出可能包含认证材料，不写入 Job 日志。凭据保存在项目外的仅
所属用户可读文件或凭据库中；服务商共享登录锁、token 续期由连接器负责。默认
连接器预算 45 秒，若命令或传输的剩余预算更短则优先使用后者。`ControlPersist`
是空闲期限，不保证服务商会话不会提前过期。

使用 module 的环境可以设置 `execution.slurm.module_init`，在 `module_loads`
之前 source 指定初始化脚本。初始化或 module 加载失败时，在启动 wrapper 之前
退出。没有 module 设置时保持原有 batch 环境。

部分集群还要求在登录节点提交 Job 前初始化环境，例如导出计算节点识别用户所需的
身份信息。SSH 命令会话不会自动加载登录配置；可选的 `transport.ssh.shell_init`
指定服务商初始化脚本的远端绝对路径。Magnus 在每次远端命令前用 Bash source
该脚本，覆盖 `sbatch`、查询和流式文件操作；导出的变量经 Slurm 正常继承到作业。
初始化失败时不执行命令。脚本不接收 stdin，stdout 重定向到 stderr，避免吞掉
batch 输入或污染调度器输出及二进制传输。脚本必须非交互，并在当前操作的既有
超时预算内完成。此项由管理员配置，不改变 SSH 认证或 SFTP/SCP 会话；省略时
保持原有行为。`module_init` 仍单独在 batch Job 内执行。
