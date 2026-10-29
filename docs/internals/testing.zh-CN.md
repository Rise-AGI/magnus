# 隔离回归测试

[English](testing.md)

在仓库根目录使用独立的 Python 3.14 环境：

```sh
uv venv --python 3.14 .venv-tests
uv pip install --python .venv-tests/bin/python -r scripts/requirements-tests.txt
.venv-tests/bin/python scripts/run_tests.py
```

Windows 下将后两条命令中的解释器改为 `./.venv-tests/Scripts/python.exe`。
这个依赖文件只用于隔离测试，不是 Magnus 服务的运行环境。

`--suite backend` 或 `--suite sdk` 选择单个套件。SDK 套件也支持其最低版本
Python 3.10。`--list` 仅列出选中的模块，不导入或运行它们。各套件在独立解释器中
执行，任一套件失败都会令命令失败。

后端套件先用 Ruff 的未定义名称检查（`F821`）扫描 `back_end/server/` 和
`back_end/library/`，不导入这些包。这能发现被测试 fixture 注入变量掩盖的缺失
绑定。静态检查失败同样令命令失败，行为测试仍继续运行并报告结果。
仅运行 SDK 套件或使用 `--list` 时不执行这项检查。

后端回归放在 `back_end/python_scripts/tests/` 的直属目录，SDK 回归放在
`sdks/python/tests/` 的直属目录；新增 `test_*.py` 自动纳入。测试必须使用临时数据
并控制外部边界，不能初始化实际配置的后端、读取个人 SDK 凭证、访问站点、提交
Job 或要求 GPU。允许本地测试子进程和进程内 HTTP 客户端。

入口排除四个现有手动示例（`test_magnus_basic`、`test_github_tools`、
`test_gpu_nvlink`、`test_signal_job`），也不遍历 `test_services/`。
这些路径保持可用，供明确请求的运行时检查使用。不要对混有示例的目录作无限制
测试发现。这是测试约定，不是操作系统沙盒。

GitHub Actions 在 Linux/Python 3.14 运行后端与 SDK 套件，并在 Linux 和
Windows/Python 3.10 运行 SDK 套件。它验证隔离行为，不代表部署、真实
BuildKit/Slurm 环境或生产负载已经验收。

后端回退兼容回归通过 `git show` 读取已提交的 custody 旧实现，需在包含完整
历史的 clone 中运行；GitHub Actions 同样获取历史。仅源码导出或浅克隆不含
该兼容性 fixture。
