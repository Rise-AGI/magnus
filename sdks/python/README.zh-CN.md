# Magnus Python SDK

[English](README.md)

[Magnus](https://github.com/Rise-AGI/magnus) 的官方 Python SDK 与 CLI，
用于向本地及超算执行目标提交容器化任务。需要 Python 3.10 或更新版本。

```bash
python -m pip install magnus-sdk==1.0.0
magnus login
magnus --help
```

使用已有 Magnus 站点及其 API 凭据。SDK 提供同步、异步 Python 接口，以及
用于任务、日志、指标、文件 custody、Blueprint 和 Skill 的 `magnus` 命令。

1.0.0 包含 Dockerfile 提交、显式执行目标、多节点 Slurm 参数、custody 就绪查询、
服务端支持时的可续传上传，以及内联类型标记 `py.typed`。原有容器镜像提交和
Python Blueprint 继续支持。新服务端功能需要站点支持；安装 SDK 不会升级站点。

提交任务时，`node_count`、`tasks_per_node`、`dockerfile` 和 `execution_target`
使用关键字参数。已发布 0.8.2 的任务提交与执行接口的位置参数继续支持。

- [SDK 与 CLI 指南](https://github.com/Rise-AGI/magnus/blob/main/docs/guides/sdk-and-cli.zh-CN.md)
- [发布说明](CHANGELOG.zh-CN.md)
- [源码与问题跟踪](https://github.com/Rise-AGI/magnus)

分发包包含仓库原有的[许可证](https://github.com/Rise-AGI/magnus/blob/main/LICENSE)。
