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

合并既有站点时，用户身份、历史 Job 和有效 custody 需单独迁移，不能只合并配置或
重定向域名。管理员可在离线迁移中为同一身份保留原 trust token 的哈希别名；它们
绑定迁移后的主 token，刷新或更换主 token 时一并撤销。HTTP、token 登录和聊天
鉴权使用相同规则。此机制不迁移浏览器 JWT 会话，不提供在线添加别名的接口。

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

## 查看排队原因与账号限制

新增两个需认证的只读 HTTP 接口，无需先升级 SDK：

- `GET /api/jobs/{job_id}/scheduling` 使用 Job 保存的执行目标和当前 Slurm 编号，
  返回 `scheduler_state`、`reason`、排队任务的 `expected_start_time`、其余任务的
  `actual_start_time` 以及 `observed_at`，时间统一为 UTC。旧转发入口的默认目标
  不会改变既有 Job 的执行身份。
- `GET /api/cluster/scheduling?execution_target=secondary` 返回配置的调度用户、
  account、QoS，以及可见的 `associations`、`qos_limits`、`partition_limits` 和
  `usage`（Slurm 登录用户）及 `account_usage`（配置的共享 account）。策略行保留 Slurm 字段名、TRES/时间表达式及其继承值；可见时同时包含
  Job QoS 与分区 QoS 定义。

认证沿用 `Authorization: Bearer <MAGNUS_TOKEN>`。Job 读取保持既有的已登录协作
权限；`GET /api/jobs/capabilities` 返回 `scheduler_observations: true`。

```python
import os
import httpx

with httpx.Client(
    base_url=os.environ["MAGNUS_ADDRESS"].rstrip("/") + "/api/",
    headers={"Authorization": "Bearer " + os.environ["MAGNUS_TOKEN"]},
    timeout=40,
) as client:
    scheduling = client.get("jobs/" + job_id + "/scheduling")
    scheduling.raise_for_status()
    print(scheduling.json())
    policy = client.get(
        "cluster/scheduling",
        params={"execution_target": "secondary"},
    )
    policy.raise_for_status()
    print(policy.json())
```

`availability` 明确区分可用性。`no_scheduler_id` 可能是准备中、提交结果确认中，
也可能已经释放编号，结合既有 detail/build/staging 接口判断。
`not_in_queue` 仅表示成功查询控制器后没有记录，不证明执行成功或资源已释放。
`identity_mismatch` 防止编号复用后串到其他任务；没有可验证 Magnus comment 的
旧分配返回 `identity_unverified`。已退役目标返回 `target_retired`；
Docker/local 返回 `unsupported`，不调用 Slurm。

集群每个子项都有自己的可用性和观测时间。部分记账查询失败不丢弃仍可读的用量。
`usage` 覆盖该 Slurm 登录用户在各分区、各 account 的任务，包括非 Magnus 作业；
排队请求与持有的分配分开记录。节点槽位按各作业求和，不是去重后的物理节点数。
资源计数为 null 表示不可得；只有成功查询的空集合或零记录才返回零。CPU、内存
（MiB）、节点槽位和 GPU 均取 Slurm TRES；挂起作业的分配仍记入并单独标记，不是
实时使用率。`account_usage` 按配置 account 跨用户查询；Slurm 设置
`PrivateData=jobs` 或无法核实可见性时，账号总量标为不可得，不静默少计。
本地提交身份取实际执行 Slurm 的进程用户，不取容器运行用户。

分区策略覆盖目标的默认及 GPU 分区。旧 CLI 不支持的可选 QoS 账号限制字段放入
`unavailable_fields`，仍保留其他可读限制。

`remaining_resources` 为 null：看到当前用户的限制，不代表上级 account、分区或
QoS 允许继续投放；有些平台不公开这些限制及共享用量。空策略值不表示无限额。
接口不查询商业余额，也不自行复刻 Slurm 的分层额度判定。依据见官方
[资源限制层级](https://slurm.schedmd.com/resource_limits.html)及
[association/QoS 字段](https://slurm.schedmd.com/sacctmgr.html)。

`association_usage` 新增限定范围的[控制器 association 缓存](https://slurm.schedmd.com/scontrol.html#OPT_assoc_mgr)观测，包含配置/成员账号
及其父链；个人观测保留在 `usage` 和 `associations`。每行标明集群、association ID、账号、用户/分区与父级。
`group_tres` 将 Slurm TRES 名称映射为 `{limit, used}`；`job_limits` 对可见的
`GrpJobs`、`GrpSubmitJobs`、`GrpJobsAccrue` 及对应 `Max*` 计数采用同一结构。例如父账号
`group_tres.node = {"limit": 32, "used": 32}` 能解释个人用量为零时仍遇到组节点限额。
内存单位为 MiB。这些是控制器计数，不是可见作业求和或硬件实时使用量；父子行不能相加。

limit 为 null 表示该 association 中 Slurm 的 `N`/未设置值，不代表有效配额无限。
未读到的计数列于行内 `unavailable_fields`；隐藏或未核实的祖先列于 `missing_accounts`，
该节返回 `partial` 或 `unavailable` 及安全错误码。上级查询超时仍保留已读到的父级信息。
本节不替换 `account_usage`：其他用户作业不可见时，控制器的账号计数仍可能可见。
查询仅沿配置/成员账号及实际观测到的父链，在既有总期限内最多执行八次限定账号的请求，
每次只查一个账号，仅消费首条匹配账号记录中自由文本 Comment 之前的字段。
记录次序异常或过滤未生效时保持未核实；不返回无关账号或用户行。

[预计开始时间](https://slurm.schedmd.com/squeue.html)来自 backfill 调度器，是可变化的
估计；null 表示没有估计，不表示马上运行。

每条命令最多 6 秒、整体最多 30 秒，独立于执行状态同步，服务端缓存 20 秒。每条控制器连接
至多一个未缓存查询，全站至多四个；超额返回 HTTP 503 和 `Retry-After: 2`。
其余故障以子项可用性和固定错误码返回，不泄露命令输出或秘密。建议轮询间隔至少
20 秒，并遵守 `Retry-After`。接口不提交、取消、调整优先级或改写 Job 状态。

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

## 提交授权与转发入口

迁入的 Blueprint 可在数据库可空字段 `default_execution_target` 保留来源目标，
不改 Python 代码。返回值中非 `default` 的显式目标优先；否则依次使用保存的来源、
转发请求头、站点默认值。新建 Blueprint 不设置此运维迁移字段。

执行目标可选配 `allowed_users: [user_id, ...]`，允许列出的用户及其下属提交。
默认目标放在 `execution.allowed_users`，其他目标放在各自配置内。省略/null 为不限制，
空列表禁止新提交；各目标独立配置，不继承默认目标的授权。Job 创建（包括 Blueprint）
及 Service 创建/唤醒执行该校验；已在执行的任务和全员可读的 Job 历史不变。

合并旧 API 入口的反向代理可设置 `X-Magnus-Default-Execution-Target` 为原目标。
收到的 Job/Blueprint 提交、新 Service 目标选择和集群查询，在省略目标或使用旧 ID
`default` 时按此值解析；其他显式 ID 不变，未知目标拒绝。该请求头不提供权限，
仍执行目标提交授权，也不改变数据库中已有 Job/Service 的目标解析。
统一入口应省略或清除此头。旧浏览器入口应重定向到统一站点，使选择器使用合并后的配置。

迁入的 Blueprint/Skill 若 ID 冲突，须分配不同的统一 ID，不能静默覆盖另一站内容。
离线迁移可在 `resource_aliases` 登记来源入口目标、资源类型、旧 ID 的映射。
转发请求头会在原有所有权校验前解析路径 ID 及创建/更新正文 ID。旧入口列表排除被其
别名遮蔽的原站资源，确保列表 ID 与后续操作指向同一对象；统一入口仍显示两份资源。
别名不授权、不递归解析，也没有公开登记接口。

如果不同账号在原站点使用了相同的 trust token，管理员不能据此悄悄合并负责人。
离线迁移可给导入账号分配独立主 token，并在 `scoped_user_token_aliases` 登记旧入口
范围内的哈希别名。转发头先选择这个范围，统一入口仍按原来的身份识别；范围内的
鉴权不使用共享鉴权缓存。更新 token 或删除用户后保留撤销记录，避免旧凭据意外
落到另一个同 token 账号。负责人可通过既有授权 token 接口取得新的主 token。
不提供公开别名登记接口。

相同旧 token 在原入口本就能操作各自账号；范围别名保留这份权限，不能用来隔离
此前已经共享同一凭据的持有人。主 token 轮换会同时撤销它的别名和旧主 token；
已经签发的 JWT 继续遵循既有会话有效期语义。
