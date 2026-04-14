> **Language / 语言**: [English](README.md) · **简体中文**

# Magnus：自动化科学发现的智能体基础设施

**PKU Plasma 与 Rise-AGI**

Magnus 是一个开源平台，将 HPC 集群转化为执行后端，
在这里人类与 AI 智能体共同提交任务、运行容器化工具链，
并将经过验证的工作流沉淀为可复用的制品。
它围绕三个层次——执行、沉淀与协作——以及三项设计承诺构建：

- **Human-Agent Symmetry（人机对称）** —— 统一的抽象层，内建可审计性。
- **Self-Evolving Blueprints（自演化蓝图）** —— 支持技能的计算原语。
- **Executable Knowledge Graph（可执行知识图谱）** —— 为可复现科学而互联的制品。

## 架构

根据我们的经验，计算科学的难点并不在于把代码跑在集群上，
而在于构成真实研究的*运行、评估、修订、再运行*的循环，
以及将来之不易的工作流沉淀成其他人——无论是人还是智能体——
都能可靠复用的形式。

Magnus 提供一个统一的基础设施层，
让人类和 AI 智能体通过相同的抽象——Blueprints、Skills 与 Jobs——使用它。
研究者可以在网页编辑器里编写 Blueprint；智能体可以通过 SDK 编写 Blueprint。
平台不区分人类点击按钮与智能体调用 API，
并且每一次操作都完全可审计。

Magnus 围绕三个层次组织：

- **Execution（执行）。** Jobs 运行在 SLURM 管理的集群上的 Apptainer 容器内部，具备完整的文件系统隔离、临时可写存储以及自动镜像缓存。平台处理四级优先级的调度与抢占。

- **Sedimentation（沉淀）。** Blueprints 与 Skills 构成一个有向无环的知识图谱，回连到执行层。一个 Blueprint 将经过验证的工作流编码为带类型的 Python 函数；一个 Skill 将领域专长编码为可移植的文档包。两者共同以一种人类和智能体都可以遍历、组合、调用和修订的结构积累机构知识——在保证可复现性的同时支持持续改进。

- **Collaboration（协作）。** 平台层面的共享治理与跨角色协调——正在积极开发中。

## 核心概念

### Blueprints

Blueprint 是一个带类型的 Python 函数，作为计算原语：
其函数签名定义参数，其函数体定义如何提交任务。
平台内省该函数以生成网页表单、校验输入并执行工作流。

以下是来自 [ColliderAgent](https://github.com/rise-agi/collider-agent) 项目的一个生产 Blueprint。
它用于校验一个 FeynRules 模型文件的语法正确性与物理
自洽性（Lagrangian 的 Hermiticity、二次项的对角化、
动能项的归一化）：

```python
from magnus import submit_job, JobType, FileSecret
from typing import Annotated

Model = Annotated[FileSecret, {
    "placeholder": "114514-apple-banana-cat",
    "description": "Transfer secret for the FeynRules model file",
}]

Lagrangian = Annotated[str, {
    "placeholder": "LSM",
    "description": "Lagrangian variable name (e.g. LSM, LmZp, Lag)",
}]

def blueprint(
    model: Model,
    lagrangian: Lagrangian,
):
    safe_secret = model.replace("'", "'\\''")
    safe_symbol = lagrangian.replace("'", "'\\''")

    submit_job(
        task_name = "[Blueprint] Validate FeynRules",
        namespace = "HET-AGI",
        repo_name = "ColliderAgent",
        commit_sha = "HEAD",
        entry_command = f"python3 scripts/run_feynrules_validation.py"
                        f" --secret '{safe_secret}' --symbol '{safe_symbol}'",
        container_image = "docker://git.pku.edu.cn/het-agi/mma-het:latest",
        job_type = JobType.A2,
        memory_demand = "10G",
        cpu_count = 10,
    )
```

这个函数同时充当配置文件、网页表单 schema、
CLI 入口和编程式 API——同一个 Blueprint 可以被
研究者通过网页 UI 启动，也可以被智能体通过 SDK 启动。

Blueprints 不是静态制品。智能体创建、执行、评估并精化它们，
闭合实验与沉淀之间的循环。一个最初作为一次性实验的工作流
可以被结晶为一个 Blueprint；智能体随后可以基于新的结果
在 Skills 所编码的领域知识引导下对其进行改进。

完整文档请参阅 [Blueprint Crafting Guide](docs/guides/blueprint-crafting.zh-CN.md)。

### Skills

Skill 是一个目录，包含一个 `SKILL.md` 文件以及可选的参考文档、
模板和示例。Skills 以与框架无关且可被智能体读取的形式编码领域知识
——任何能够读取文件的基于 LLM 的智能体都可以使用它们。

```
feynrules-model-generator/
  SKILL.md                # Trigger conditions, inputs/outputs, workflow
  references/
    syntax-rules.md       # Condensed from official documentation
    naming-conventions.md
  templates/
    skeleton.fr           # Starter model file
```

Skills 将领域专长与智能体实现解耦。
你可以替换底层的智能体框架而无需重写你的领域知识。
Blueprints 以 Skills 为其知识基础；
反过来，Skills 依赖 Blueprints 作为其执行骨干。

### Jobs 与调度

每一个计算任务都作为一个 Job 运行：SLURM 集群上的一个容器化进程。
Jobs 通过 Apptainer `--containall` 进行隔离，每个 Job 拥有在启动时
创建、在完成时销毁的临时可写存储。

调度器运行一个四级优先级系统（A1 > A2 > B1 > B2）。
当资源紧张时，A 类 Jobs 可以抢占 B 类 Jobs。
被抢占的 Jobs 会自动暂停并重新排队。

## 快速上手

安装 SDK：

```bash
pip install magnus-sdk
magnus login
```

提交一个 Blueprint：

```python
import magnus

result = magnus.run_blueprint("validate-feynrules", args={
    "model": "~/models/minimal_Zp.fr",
    "lagrangian": "LSM",
})
print(result)
```

或者从命令行：

```bash
magnus run validate-feynrules --model ~/models/minimal_Zp.fr --lagrangian LSM
magnus logs -1    # view logs of the most recent job
```

完整的 SDK 与 CLI 参考：[Magnus SDK Guide](docs/guides/sdk-and-cli.zh-CN.md)。

## 应用案例

### [ColliderAgent](https://github.com/rise-agi/collider-agent)

从 Lagrangian 到排除限的自主对撞机唯象学。
ColliderAgent 使用 Magnus 作为其执行后端，在容器化的 HPC 工具链上
编排一个五阶段的 Blueprint 流水线：

| Blueprint | 功能 | 工具链 |
|-----------|-------------|-----------|
| `validate-feynrules` | 检查模型文件的语法与物理自洽性 | Wolfram + FeynRules |
| `generate-ufo` | 将 FeynRules 模型编译为 UFO 标准输出 | Wolfram + FeynRules |
| `madgraph-compile` | 枚举费曼图、计算矩阵元、编译 process 目录 | MadGraph5 |
| `madgraph-launch` | 从已编译的 process 运行蒙特卡洛事件生成 | MadGraph5 |
| `madanalysis-process` | 生成截面图、cutflow 表格与分析报告 | MadAnalysis5 |

每个阶段通过 Magnus 的文件托管（file custody）将制品传递给下一个阶段，
整条流水线既可以由人类通过网页 UI 驱动，
也可以由智能体通过 SDK 驱动。

## 部署

Magnus 运行在一台可访问 SLURM 集群的 Linux 服务器上。

```bash
# Clone and configure
cp configs/magnus_config.yaml.example configs/magnus_config.yaml
# Edit magnus_config.yaml: set server address, SLURM paths, auth credentials

# Backend
cd back_end && uv sync && uv run -m server.main

# Frontend
cd front_end && npm install && npm run dev
```

环境要求：Python >= 3.14、Node.js LTS、SLURM、Apptainer。
容器执行细节请参阅 [Job Runtime Documentation](docs/internals/job-runtime.zh-CN.md)。

## 文档

**用户指南** —— 如果你正在使用 Magnus 构建应用，从这里开始：

- [SDK & CLI Guide](docs/guides/sdk-and-cli.zh-CN.md) —— Python API、异步支持，以及完整的 CLI 参考
- [Blueprint Crafting Guide](docs/guides/blueprint-crafting.zh-CN.md) —— 类型标注、参数元数据、文件传输
- [Local Mode](docs/guides/local-mode.zh-CN.md) —— 用 Docker 代替 SLURM 在本地运行 Magnus
- [Metrics Protocol](docs/guides/metrics.zh-CN.md) —— Jobs 的跨语言指标上报协议

<details>
<summary><strong>内部实现与参考</strong> —— 面向运维者与贡献者</summary>

- [Job Runtime](docs/internals/job-runtime.zh-CN.md) —— 容器隔离、环境变量、网络
- [uv Image Notes](docs/internals/uv-image.zh-CN.md) —— 用 uv 构建 `magnus-runtime` 容器镜像
- [Internal Links Requirements](docs/requirements/internal-links.zh-CN.md) —— `magnus:///` URI 方案的设计说明

</details>

所有文档均为双语；使用每篇顶部的语言切换链接即可切换。

## 贡献

Magnus 正在持续演进。如果你遇到粗糙之处、有改进的想法，
或者想要贡献代码，请提交一个 issue 或 pull request。
我们感激每一份报告、建议与补丁。

请直接通过 parkcai@126.com 联系我们，我们真的非常希望听到你的声音。

## 许可证

本项目基于 [GNU Affero General Public License v3.0](LICENSE) 发布。

## 致谢

- [Apptainer](https://github.com/apptainer/apptainer) —— 通过其面向 HPC 工作负载的健壮运行时，为 Magnus 的容器执行提供动力。
- [croc](https://github.com/schollz/croc) —— 以其无摩擦的文件移动方式启发了 Magnus 的 file custody 特性。
