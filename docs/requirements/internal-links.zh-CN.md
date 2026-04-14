> **Language / 语言**: [English](internal-links.md) · **简体中文**

# 需求文档：Magnus 站内链接渲染

## 背景

Magnus 的 Skill 系统存储结构化知识文档（Markdown 格式），其中需要引用 Magnus 平台内部的资源——Job、Blueprint、Skill 等。

当前的问题是：Magnus 有多个访问入口：
- **内网**：`http://<intranet-host>:3011`
- **公网（如 Cloudflare）**：`https://magnus.example.com`

如果在 Skill 文档中硬编码某个地址的链接，从另一个入口访问的用户点击后会跳到错误的地址。

## 需求

### 1. 站内链接格式

定义 `magnus:///` 伪协议，用于表示"本站资源"。格式：

```
magnus:///jobs/{job_id}
magnus:///blueprints/{blueprint_id}
magnus:///skills/{skill_id}
magnus:///explorer/{session_id}
```

三斜杠 `///` 是标准 URI 写法（authority 为空 = "本站"）。

### 2. 前端 Markdown 渲染

在所有渲染 Skill Markdown 内容的地方（目前是 Skill 详情页），对 `magnus:///` 链接做运行时替换：

```tsx
// 伪代码
const resolveMagnusLink = (href: string) => {
  if (href.startsWith('magnus:///')) {
    const path = href.replace('magnus:///', '/');
    return `${window.location.origin}${path}`;
  }
  return href;
};
```

这样：
- 内网用户点击 → `http://<intranet-host>:3011/jobs/abc123`
- 公网用户点击 → `https://magnus.example.com/jobs/abc123`

### 3. 影响范围

- **后端**：无改动。`magnus:///` 链接作为纯文本存储在 Skill 文件中
- **前端**：仅需修改 Markdown 渲染组件的 link transformer
- **蓝图**：已在 `distill-knowledge` 元蓝图的 prompt 中要求内部 AI 使用 `magnus:///` 格式

### 4. 具体使用场景

Skill 中新增了 `RUNS.md` 文件，用于审计蓝图的验证运行记录：

```markdown
# Run History

## Verification Runs

| Date | Blueprint | Job | Status | Notes |
|---|---|---|---|---|
| 2026-03-05 | fulop2006-threshold | [Job 16afd185](magnus:///jobs/16afd18525d37650) | Success | Figures match |
```

点击 "Job 16afd185" 应跳转到当前站点的 Job 详情页。

### 5. 未来扩展

`magnus:///` 不仅限于 Skill 文档，未来可以在所有 Magnus 渲染 Markdown 的地方（Explorer 消息、Blueprint 描述等）统一支持。

### 6. 优先级

低。目前 `magnus:///` 链接在前端显示为纯文本，不影响功能——只是不可点击。实现后体验更好。
