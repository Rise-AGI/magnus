# 共享盘上的项目目录

本约定适用于所有计算节点能访问同一 POSIX 共享盘的 Slurm 执行目标。沿用
`system_entry_command` 中的 `APPTAINER_BIND`（SingularityCE 使用
`SINGULARITY_BIND`），不增加 Job 字段、挂载 API 或 SDK 接口。
挂载可以读写；只有调用方指定 `:ro` 时才只读。custody 上传、下载保持原有功能。

## 路径与责任

站点维护者给每个项目分配一个稳定的宿主目录，例如：

```text
/shared/account/magnus-projects/<project-id>/
    project.json
    inputs/
    cache/
    runs/<run-id>/
    staging/<writer-id>/
```

`project-id` 使用小写字母、数字和连字符。`project.json` 记录项目负责人、执行目标、
宿主路径、容器路径和保留约定，不存密码、token 或取件码。实际访问权限由站点的
Unix 账号和文件权限决定；共享同一 Unix 账号的项目目录不提供彼此的权限隔离。

这个根目录必须在 Magnus 的 workspace、ephemeral、镜像缓存和 custody 管理目录
之外。Job 结束或工作区回收后，项目目录仍然保留；它占用超算共享盘配额，不计入
Job 的 `ephemeral_storage` 限额。保留不等于备份，重要结果仍应交付到独立存储。

## 挂载与运行

下面是追加到目标站点默认 `system_entry_command` 后的 Apptainer 示例。
提交自定义 `system_entry_command` 会替换默认值，因此调用方应先保留默认准备内容。

```bash
project_root=/shared/account/magnus-projects/my-project
test -d "$project_root" || exit 1
existing_bindings="${APPTAINER_BIND:-${SINGULARITY_BIND:-}}"
export APPTAINER_BIND="${existing_bindings:+$existing_bindings,}$project_root:/project:rw"
```

使用 SingularityCE 时向 `SINGULARITY_BIND` 追加相同的挂载声明。调用方也可以把
某个输入或缓存版本单独挂为 `:ro`，同时让输出目录保持 `:rw`。
挂载源和目标避免逗号、冒号及换行，因为它们是 bind 字符串的分隔符。
容器路径使用 `/project` 这样的独立位置，避免嵌套在 `/magnus/workspace` 下；
wrapper 后追加的 workspace 父挂载可能遮蔽其内部的自定义挂载。

每次提交前生成独立 `run-id`，在提交回执中关联 Magnus Job ID 和执行目标。
程序把需要跨 Job 保留的输出写入 `/project/runs/<run-id>/`；普通工作区中的
相对路径不会自动转存到这里。各 rank 使用不同文件名，或由程序明确指定单写者。
跨节点程序使用通信屏障或文件就绪协议协调写入完成，不能只凭目录存在判定结果完整。

## 输入与装配缓存复用

项目负责人负责应用数据的兼容性判断和发布。一个已发布版本至少应有完整文件清单、
大小与 SHA-256，并记录生成源码、输入、格式、数值精度及相关运行环境身份。
缓存键必须区分影响数据兼容性的条件；不能仅用上一次 Job ID 判断可复用性。

发布顺序如下：

1. 在独立 `staging/<writer-id>/` 中生成文件，不覆盖读者正在使用的版本。
2. 完成文件写入、关闭和必要的同步，生成清单并逐项验证。
3. 按版本用原子 `mkdir` 获取独占发布锁，例如 `staging/.publish-<version>.lock`；
   创建失败者等待或核验已发布版本，不能同时发布。持锁者检查最终路径不存在，
   再用同文件系统的目录重命名将完整目录发布到 `cache/<version>/`，最后释放锁。
   所有发布者必须遵守同一个锁；使用直接的 rename 操作，不用会把源目录嵌入现有
   目标的 `mv` 默认行为。目标已存在时只能核验复用，不能覆盖或合并。失败时保留
   staging；清理遗留锁前须确认原写者已结束。
4. 读者只使用已发布版本，核验身份和所需文件；缺失或不兼容时明确失败，是否重建由
   项目负责人决定。不要在每次打开缓存时重复复制整个目录。

可以用清单文件的 SHA-256 命名版本目录。导入已有缓存时保留原清单字节与 SHA；
路径变化由读取端的根目录映射处理，不为搬迁改写原清单。已发布版本按约定不再修改，
更新使用新版本；需要继续写入的中间状态放在自己的 run 或 staging 目录。
这是一项协作约定，不会把整个项目挂载强制改成只读。

## 保留、清理与文件交付

项目负责人记录每个提交使用的输入和缓存版本，包括尚未获得 Job ID 的提交意图。
清理前核对排队、准备、运行及仍在释放资源的 Job，以及其他读者；无法确认无人使用
时保留。Magnus Job 回收器不负责清理这些项目目录，也不按缓存年龄自动删除它们。

站内后续 Job 可以直接挂载复用数据。需要本机下载、跨站传递或交付他人时，继续使用
现有 `custody_file` / `download_file` 及对应 CLI；也可按运维授权使用 SSH/SCP。
SSH 远端执行目标支持输入预置：无外网的计算节点需要在提交的 `entry_command` 或
`system_entry_command` 中包含字面量 `magnus-secret:<token>`，让平台在启动前预置输入；运行时才动态取得的取件码
未被预置，下载会尝试 HTTP，需要相应网络。本机 Slurm 目标不执行这项预置，
其下载同样需要可用的 HTTP 通路。输出 `custody_file` 生成的取件码需等
控制器导入完成、状态为 ready 后才能领取。参见[文件传输指南](sdk-and-cli.zh-CN.md)。
挂载不会改变 custody 的就绪状态、下载次数、期限或任务终态后的保留语义。
重要交付仍需核对文件清单和 SHA-256，custody 回执不能代替长期保全记录。

站点接入验收应覆盖两节点访问同一项目目录、实际读写、显式只读挂载、跨 Job 保留，
以及原有 custody 上传和下载。具体宿主路径和验收结果由站点交接文档提供。
