> **Language / 语言**: **English** · [简体中文](CONTRIBUTING.zh-CN.md)

# Contributing to Magnus

We welcome contributions from the community. Before submitting a pull request, please read through this guide.

## Contributor License Agreement (CLA)

All external contributors must sign our [Contributor License Agreement](CLA.md) before their pull request can be merged.

**Why?** Magnus is dual-licensed. The AGPL-3.0 license ensures openness for academic and open-source use, while a separate commercial license is available for organizations that need different terms. To maintain the ability to offer both licenses, Rise-AGI must have sufficient rights over all contributed code.

**How?** When you open a pull request, the CLA Assistant bot will automatically check whether you have signed the CLA. If not, it will leave a comment with instructions. Simply follow the prompt — the process takes under a minute.

Rise-AGI team members are covered by their employment agreements and do not need to sign separately.

## Development Setup

```bash
# Backend
cd back_end && uv sync && uv run -m server.main

# Frontend
cd front_end && npm install && npm run dev

# Type check
cd front_end && npx tsc --noEmit
```

## Pull Request Guidelines

- Keep PRs focused — one feature or fix per PR.
- Follow existing code style and conventions (see `CLAUDE.md` for details).
- Include a clear description of what your PR does and why.
- Ensure `npx tsc --noEmit` passes for frontend changes.

## Commit Message Format

```
[module] type: short description
```

- **module**: the area of change (`security`, `explorer`, `metrics`, `jobs`, etc.)
- **type**: `feat` (new feature) or `fix` (bug fix)

## Questions?

Open an issue or reach out to the maintainers.
