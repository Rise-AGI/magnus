# Isolated regression tests

[中文](testing.zh-CN.md)

From the repository root, use a separate Python 3.14 environment:

```sh
uv venv --python 3.14 .venv-tests
uv pip install --python .venv-tests/bin/python -r scripts/requirements-tests.txt
.venv-tests/bin/python scripts/run_tests.py
```

On Windows, use `./.venv-tests/Scripts/python.exe` in the last two commands.
The dependency file is for isolated tests, not for running a Magnus server.

`--suite backend` or `--suite sdk` selects one suite. The SDK suite also runs
on Python 3.10, its minimum supported version. `--list` lists the selected
modules without importing them. Each suite runs in its own interpreter, and
any failing suite makes the command fail.

Backend regressions live directly in `back_end/python_scripts/tests/`; SDK
regressions live directly in `sdks/python/tests/`. New `test_*.py` modules in
these directories join the suite automatically. Tests must use temporary data
and controlled external boundaries. Do not initialize the configured backend,
load personal SDK credentials, contact a site, submit Jobs or require a GPU.
Local fixture processes and in-process HTTP clients are allowed.

The runner excludes four existing manual examples (`test_magnus_basic`,
`test_github_tools`, `test_gpu_nvlink`, `test_signal_job`) and does not traverse
`test_services/`. Their paths remain available for explicitly requested runtime
checks. Do not use unrestricted discovery over the mixed examples directory.
This selection is a testing convention, not an operating-system sandbox.

GitHub Actions runs the backend and SDK suites on Linux/Python 3.14, plus the SDK
suite on Linux and Windows/Python 3.10. This checks isolated behavior; it does
not certify deployment, a real BuildKit/Slurm installation or production load.

The backend rollback-compatibility regression reads committed custody code with
`git show`. Run it from a clone with full history; GitHub Actions also fetches
that history. Source-only exports and shallow clones do not contain this fixture.
