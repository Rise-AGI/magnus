> **Language / 语言**: [English](image-building.md) · **简体中文**

# Dockerfile Job

Job 可通过 `container_image` 或 `dockerfile` 描述运行环境。Magnus 并行准备环境和代码仓库，再执行原有 `entry_command`。Blueprint 通过同一 Job 提交接口使用这些参数。

## 提交

```python
from magnus import submit_job

job_id = submit_job(
    task_name="Python environment",
    entry_command="python -c 'import packaging; print(packaging.__version__)'",
    dockerfile="""FROM python:3.14-slim
RUN python -m pip install packaging==25.0
""",
)
```

省略仓库时，选择 `Rise-AGI/magnus` 默认分支的 HEAD，解析出的分支和具体 SHA 会记入 Job。仍可显式指定仓库和版本；非默认 namespace 需要同时提供仓库名。

```sh
magnus submit --task-name dependency-check --dockerfile-file ./Dockerfile --entry-command "python --version"
magnus job build JOB_ID
magnus job build JOB_ID --logs
```

`--dockerfile-file` 读取本地 UTF-8 文件并发送文本，`--dockerfile` 直接接收文本。SDK 保留原位置参数顺序，将 Dockerfile 添加为 keyword-only 参数。发送前检查后端能力，避免旧服务器静默忽略 Dockerfile。

显式镜像与 Dockerfile 互斥；两者都省略时仍使用站点默认镜像。已提供的 Dockerfile 不允许为空白。

## 构建 context 与缓存

构建 context 为**空**。Job 仓库、commit、运行命令和 GPU 数量不参与环境输入。普通本地 `COPY`、`ADD` 没有文件来源；多阶段 `COPY --from` 和明确的远程来源沿用 Dockerfile 的正常行为。准备好的环境运行 Job 时，再挂入代码仓库。

步骤缓存由 BuildKit 管理。Magnus 合并相同并发请求并缓存可运行成品，不自行解析 Dockerfile 判断 shell 等价性。不同配方可以复用相同的前置步骤。成品按实际内容标识命名，重建配方不会覆盖其他 Job 仍在使用的旧 SIF。

SIF 缓存命中时校验记录的大小与 SHA-256。后端在本次进程内记住已验证文件的元数据，避免反复计算未变化的本地文件哈希；relay 复用前校验远端内容。损坏的副本会重新构建或传输。没有其他 Job 引用时，relay 可在传输前丢弃损坏副本；仍有引用时保留旧副本直到原子替换，因此需要容纳两份副本的空间。

Docker/local 加载构建结果；HPC 将 Docker archive 转为 SIF，再沿用既有 SLURM/relay 链路。不需要 registry 或永久 Image 资产。转换后可删除临时归档。运行副本被淘汰时，可从仍存在的 BuildKit 缓存重新导出；步骤也被淘汰时重新构建。浮动 `FROM` 和未锁定的网络依赖可能因此变化。仅删除 SIF 不会使 BuildKit 已缓存的 `RUN` 步骤失效。

## 状态、进度和取消

环境缓存未命中时进入 `Building`，等待构建槽位、执行步骤、导出和加载/转换都作为该状态的阶段。环境和仓库都就绪后进入 `Pending`；成品命中可跳过 Building。

构建日志在 Job 详情页的日志容器内查看，右上角可切换“运行日志”与“构建日志”。列表和详情页的状态徽章仅展示状态，不打开日志浮层。Dockerfile 与构建日志不进入 Job 列表。构建日志保留最近约 4 MiB，丢弃早期输出时显示截断标记；失败后仍可查看，与运行日志分开保留，直到 Job 工作区被回收。历史诊断不可用时，进度接口返回 HTTP 410，不会误报为新一次构建正在等待。

```python
from magnus import get_job_build, get_job_build_logs

progress = get_job_build(job_id)
logs = get_job_build_logs(job_id)
```

原有终止操作撤销该 Job 的构建订阅，其他等待同一环境的 Job 可以继续；最后一个订阅者离开时，取消实际构建和转换。成品在排队、传输和运行期间受到使用保护，缓存淘汰和显式删除都必须遵守保护。构建诊断保存在 Job 可写挂载之外，与保留的 Job 产物共用存储预算和清理生命周期。

## 站点配置

通过 `execution.image_build` 按需启用，旧配置默认关闭。字段见[配置示例](../../configs/magnus_config.yaml.example)。后端需要 `buildctl` 和面向目标 Linux 平台的 Magnus 专属 BuildKit worker。每个 daemon 独占自己的 state 目录。Linux 可使用 rootless BuildKit；Docker/local 可在 Docker Linux 环境中使用专属 worker。部署时配置宿主能力及 worker CPU/内存限额；与计算任务共用硬件时预留对应资源。普通 Job 不获得 builder socket。

TCP worker 使用配置的 TLS 文件。站点凭据不进入 Dockerfile、Job 命令或日志。Worker 需要访问基础镜像和软件源；无网 tenant 计算节点通过 relay 接收 SIF，无需直接访问 builder。

基础镜像的 registry mirror 在 worker 的 `buildkitd.toml` 中配置；现有 `cluster.registry_mirror` 只作用于 Magnus 的镜像拉取，不会自动配置 BuildKit。上线时应在 worker 的实际网络环境中验证 `FROM` 拉取和 `RUN` 安装依赖，宿主机能访问软件源并不足以证明构建可用，尤其在 IPv4/IPv6 出口权限不同时。

Rootless 限制宿主权限，但不自动提供网络隔离。RootlessKit 默认共享宿主网络，构建中的 `RUN` 可以访问宿主 loopback 和局域网；需要隔离时另选受支持的网络驱动，并核验其 DNS、出口和软件源访问。参见 [BuildKit rootless 部署说明](https://github.com/moby/buildkit/blob/master/docs/rootless.md)。

后端重启后，资源准备会等待已知的遗留构建进程退出，再清理其临时目录，等待上限为 `timeout_seconds`。进程租约不完整，或另一个后端仍在使用同一根目录时，需要运维检查；Magnus 不会删除无法确认归属的临时目录。

可选的 `scratch_root` 为构建导出和 SIF 转换指定绝对路径的父目录。Magnus 在其下创建按站点区分的子目录，多个实例可共用父目录而不共享临时数据或恢复状态。省略时保留 `server.ephemeral_root/image_build_tmp`。子目录必须由后端账号私有控制，不允许符号链接或 junction；父目录权限必须防止其他用户替换它，POSIX 支持 `/dev/shm` 这样的 root 所有且带 sticky bit 的目录。Windows 检查所有者与 ACL，不静默修改现有权限。切换位置时，初始化仍会检查原默认目录内未完成的操作，再处理遗留数据；从一个自定义目录切到另一个前，应先让原目录中的操作结束。

`scratch_size` 限制构建临时数据的总量，默认继承 `cache_size`；它包含在总缓存预算内，不是额外额度。容量检查在转换期间以及删除临时产物之前执行，因此即使最终 SIF 能放下，瞬时峰值仍可能使构建失败。这些是采样检查，物理硬上限仍需有配额的文件系统或卷。快速磁盘或有容量约束的内存卷可减少转换 I/O，但 tmpfs 同时占用宿主内存，需在 BuildKit worker 之外独立规划；Magnus 不挂载卷，也不会自动把大镜像切到内存中构建。

SIF 转换可用 `conversion_cpus`（默认 `2`）和 `conversion_memory`（默认 `1G`）限制 mksquashfs 的处理器数量及工作内存，同时保留 gzip 格式对旧 Singularity 的兼容。这不是整个进程或 cgroup 的硬限制，BuildKit daemon 的资源限额也不覆盖后端启动的 Apptainer 子进程；需要时另配操作系统级限额。Docker/local 仍用 `docker load`，这两个 SIF 配置项不改变 Docker 导入行为。

加载、转换、哈希和复制期间不持有缓存发布锁，因此一个配方转换时，另一个已缓存的环境仍可准备。正在写入的文件和临时 Docker tag 在发布或取消收尾完成前受到回收保护；发布、等待者使用保护与回滚保持串行。

预算覆盖构建缓存、受管运行副本和导出/转换 scratch。relay 对远端构建镜像缓存独立应用同样的容量和剩余空间配置，在传输前预留空间并保护活跃 Job。只清理 Magnus 管理的文件/镜像，不执行用户级全局 Docker prune，也不回收原有拉取镜像缓存。BuildKit 周期 GC 不是瞬时磁盘配额：严格物理上限需要受限文件系统/卷，包括 scratch，并配合剩余空间和并发限制。Docker 共享 image store 也要规划容量。使用保护导致无法回收时应明确失败，不能删除活跃运行环境。

步骤缓存的保留策略使用 worker 原生 GC，配置最大占用、保留空间和剩余空间目标。预算应容纳基础镜像、已安装依赖和 cache mount；大底座可能占掉几乎全部额度。Magnus 仅在实际总预算超限时回收 worker 数据，保留本地数据之外仍可容纳的部分；仅 scratch 超限或本地剩余空间不足不会清空 worker 缓存。容量错误包含观测到的字节数和阈值，后端日志记录回收前后占用；采样不能重建未记录的历史峰值。

缓存元数据超过 BuildKit 的 RPC 消息上限时，Magnus 使用互不重叠的 ID 过滤器在服务端分批查询容量，并复用成功的分组；此操作不删除缓存，也不改变预算。查询次数与耗时有界，无法完整读取时准备失败，不把无法读取的缓存当作空缓存。

## 兼容与验收

升级保留旧 Job 和镜像来源语义，增加 Building，并在列表查询中延迟加载 Dockerfile。Service 仍走原有镜像输入，其活跃 Job 判断识别 Building。不安装专属构建 Blueprint。

生产启用前需验收步骤复用、同配方并发、取消、重启、磁盘压力、Docker 执行及 Apptainer/Singularity 与 relay 消费。静态检查不能证明这些运行时性质。

参考：[BuildKit](https://docs.docker.com/build/buildkit/)、[空 context](https://docs.docker.com/build/concepts/context/#empty-context)、[缓存失效](https://docs.docker.com/build/cache/invalidation/)、[GC](https://docs.docker.com/build/cache/garbage-collection/)。
