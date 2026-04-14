> **Language / 语言**: [English](CONTRIBUTING.md) · **简体中文**

# 为 Magnus 贡献代码

我们欢迎来自社区的贡献。在提交 pull request 之前，请通读本指南。

## 贡献者许可协议（CLA）

所有外部贡献者必须先签署我们的 [Contributor License Agreement](CLA.md)（[简体中文参考译本](CLA.zh-CN.md)），其 pull request 才能被合并。

**为什么？** Magnus 采用双重许可。AGPL-3.0 许可证确保其对学术与开源使用保持开放，与此同时，我们也为需要不同条款的组织提供单独的商业许可证。为了保持同时提供两种许可证的能力，Rise-AGI 必须对所有被贡献的代码拥有足够的权利。

**如何签署？** 当你发起一个 pull request 时，CLA Assistant 机器人会自动检查你是否已经签署 CLA。如果没有，它会留下一条带操作说明的评论。只需按提示操作即可——整个过程不到一分钟。

Rise-AGI 团队成员由其雇佣协议覆盖，无需单独签署。

## 开发环境搭建

```bash
# Backend
cd back_end && uv sync && uv run -m server.main

# Frontend
cd front_end && npm install && npm run dev

# Type check
cd front_end && npx tsc --noEmit
```

## Pull Request 规范

- 保持 PR 聚焦——每个 PR 只做一个特性或一个修复。
- 遵循既有的代码风格与约定（详情见 `CLAUDE.md`）。
- 清晰描述你的 PR 做了什么以及为什么要这样做。
- 对于前端改动，确保 `npx tsc --noEmit` 通过。

## 提交信息格式

```
[module] type: short description
```

- **module**：改动所在领域（`security`、`explorer`、`metrics`、`jobs` 等）
- **type**：`feat`（新功能）或 `fix`（bug 修复）

## 有问题？

提交一个 issue 或直接联系维护者。
