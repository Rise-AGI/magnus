# Magnus SDK changelog

[简体中文](CHANGELOG.zh-CN.md)

## 1.0.0

Changes since the published 0.8.2 release:

- Submit Dockerfile text as an alternative to `container_image`, with capability
  checks and dedicated build-status and build-log APIs. Builds use an empty
  context independent of the job repository.
- Select `execution_target`, discover configured targets, and supply multi-node
  Slurm topology through `node_count` and `tasks_per_node`.
- Query custody readiness without consuming a download, and optionally wait for
  readiness before downloading. Supporting controllers import outputs while a
  job runs and apply the job-associated retrieval window after execution ends.
- Negotiate resumable uploads for payloads of at least 8 MiB on supporting
  controllers. Older controllers retain multipart uploads. Resume applies within
  one SDK invocation, not across client process exits.
- Report uncertain upload outcomes as `UploadOutcomeUnknown` rather than blindly
  publishing duplicates. Preserve previous download destinations on transfer or
  extraction failures, and reject unsafe archive entries on Python 3.10 as well.
- Ship `py.typed` for type checkers and retain source-only SDK imports.
- Split the CLI implementation by responsibility while retaining its entry point;
  correct numeric job-ID handling and timestamp interpretation.

### Compatibility

Python 3.10 remains the minimum. Existing container images, Python Blueprints and
SDK configuration remain supported. New features require server support; explicit
Dockerfile or target requests fail clearly on unsupported servers.

The published 0.8.2 positional order is preserved for `submit_job`, `execute_job`
and their asynchronous and module-level variants. New submission fields are
keyword-only. Users of an unreleased source checkout that passed `node_count` or
`tasks_per_node` positionally must name those two arguments; keyword calls keep
their existing meaning.

## 0.8.2 — 2026-06-21

Previous PyPI release; see the repository history for earlier changes.
