> **Language / 语言**: **English** · [简体中文](internal-links.zh-CN.md)

# Requirements Document: Magnus Internal Link Rendering

## Background

Magnus's Skill system stores structured knowledge documents (in Markdown format), in which it is necessary to reference resources internal to the Magnus platform — Jobs, Blueprints, Skills, etc.

The current problem is: Magnus has multiple access entrypoints:
- **Intranet**: `http://<intranet-host>:3011`
- **Public (e.g. via Cloudflare)**: `https://magnus.example.com`

If a link to a specific address is hard-coded in a Skill document, a user accessing from another entrypoint will be sent to the wrong address when clicking it.

## Requirements

### 1. Internal Link Format

Define the `magnus:///` pseudo-protocol to represent "a resource on this site". Format:

```
magnus:///jobs/{job_id}
magnus:///blueprints/{blueprint_id}
magnus:///skills/{skill_id}
magnus:///explorer/{session_id}
```

The triple slash `///` is standard URI notation (empty authority = "this site").

### 2. Frontend Markdown Rendering

In all places that render Skill Markdown content (currently the Skill detail page), perform runtime substitution on `magnus:///` links:

```tsx
// pseudo code
const resolveMagnusLink = (href: string) => {
  if (href.startsWith('magnus:///')) {
    const path = href.replace('magnus:///', '/');
    return `${window.location.origin}${path}`;
  }
  return href;
};
```

This way:
- Intranet user click → `http://<intranet-host>:3011/jobs/abc123`
- Public user click → `https://magnus.example.com/jobs/abc123`

### 3. Scope of Impact

- **Backend**: no changes. `magnus:///` links are stored as plain text inside Skill files
- **Frontend**: only the link transformer of the Markdown rendering component needs to be modified
- **Blueprint**: the prompt of the `distill-knowledge` meta-blueprint already requires the internal AI to use the `magnus:///` format

### 4. Specific Use Cases

A new `RUNS.md` file has been added to Skills, used to audit verification run records of blueprints:

```markdown
# Run History

## Verification Runs

| Date | Blueprint | Job | Status | Notes |
|---|---|---|---|---|
| 2026-03-05 | fulop2006-threshold | [Job 16afd185](magnus:///jobs/16afd18525d37650) | Success | Figures match |
```

Clicking "Job 16afd185" should jump to the Job detail page of the current site.

### 5. Future Extension

`magnus:///` is not limited to Skill documents; in the future it can be uniformly supported in all places where Magnus renders Markdown (Explorer messages, Blueprint descriptions, etc.).

### 6. Priority

Low. Currently `magnus:///` links are displayed as plain text in the frontend and do not affect functionality — they are just not clickable. The experience will be better once implemented.
