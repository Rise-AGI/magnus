# Magnus Python SDK

[简体中文](https://github.com/Rise-AGI/magnus/blob/main/sdks/python/README.zh-CN.md)

The official Python SDK and CLI for [Magnus](https://github.com/Rise-AGI/magnus),
a platform for submitting containerized jobs to local and HPC execution targets.
Requires Python 3.10 or newer.

```bash
python -m pip install magnus-sdk==1.0.0
magnus login
magnus --help
```

Use an existing Magnus site and its API credentials. The SDK provides synchronous
and asynchronous Python APIs, plus the `magnus` command for jobs, logs, metrics,
file custody, Blueprints and Skills.

Version 1.0.0 includes Dockerfile submission, explicit execution targets,
multi-node Slurm parameters, custody readiness queries, resumable uploads on
supporting servers, and inline typing metadata (`py.typed`). Existing container
image submission and Python Blueprints remain supported. New server features
require a site that implements them; installing the SDK does not upgrade a site.

For job submission, pass `node_count`, `tasks_per_node`, `dockerfile` and
`execution_target` by keyword. The positional arguments of the published 0.8.2
submission and execution APIs remain supported.

- [SDK and CLI guide](https://github.com/Rise-AGI/magnus/blob/main/docs/guides/sdk-and-cli.md)
- [Release notes](https://github.com/Rise-AGI/magnus/blob/main/sdks/python/CHANGELOG.md)
- [Source and issue tracker](https://github.com/Rise-AGI/magnus)

The repository's [license](https://github.com/Rise-AGI/magnus/blob/main/LICENSE)
is included in the distributions.
