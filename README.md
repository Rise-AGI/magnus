> **Language / 语言**: **English** · [简体中文](README.zh-CN.md)

# Magnus: An Agentic Infrastructure Automating Scientific Discoveries

**PKU Plasma and Rise-AGI**

Magnus is an open-source platform that turns HPC clusters into an execution backend
where both humans and AI agents submit jobs, run containerized toolchains, and
crystallize validated workflows into reusable artifacts.
It is built around three layers -- execution, sedimentation, and collaboration --
and three design commitments:

- **Human-Agent Symmetry** -- unified abstractions with built-in auditability.
- **Self-Evolving Blueprints** -- skill-supporting computational primitives.
- **Executable Knowledge Graph** -- linked artifacts for reproducible science.

## Architecture

In our experience, the harder part of computational science is not running code on a cluster,
but the cycle of *run, evaluate, revise, rerun* that constitutes real research,
and the sedimentation of hard-won workflows into forms that others -- human or agent --
can reliably reuse.

Magnus provides a single infrastructure layer
that humans and AI agents use through the same abstractions -- Blueprints, Skills, and Jobs.
A researcher can author a Blueprint in the web editor; an agent can author one through the SDK.
The platform does not distinguish between a human clicking a button and an agent calling an API,
and every operation is fully auditable.

Magnus is organized around three layers:

- **Execution.** Jobs run inside Apptainer containers on SLURM-managed clusters, with full filesystem isolation, ephemeral writable storage, and automatic image caching. The platform handles scheduling with four priority tiers and preemption.

- **Sedimentation.** Blueprints and Skills form a directed acyclic knowledge graph that connects back to the execution layer. A Blueprint encodes a validated workflow as a typed Python function; a Skill encodes domain expertise as a portable document package. Together they accumulate institutional knowledge in a structure that both humans and agents can traverse, compose, invoke, and revise -- ensuring reproducibility while enabling continuous improvement.

- **Collaboration.** Shared governance and cross-role coordination across the platform -- under active development.

## Core Concepts

### Blueprints

A Blueprint is a typed Python function that serves as a computational primitive:
its signature defines parameters, its body defines how a job is submitted.
The platform introspects the function to generate a web form, validate inputs,
and execute the workflow.

The following is a production Blueprint from the
[ColliderAgent](https://github.com/rise-agi/collider-agent) project.
It validates a FeynRules model file for syntactic correctness and physical
self-consistency (Hermiticity of the Lagrangian, diagonalization of quadratic terms,
kinetic term normalization):

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

This function simultaneously serves as a configuration file, a web form schema,
a CLI entrypoint, and a programmatic API -- the same Blueprint can be launched by
a researcher through the web UI or by an agent through the SDK.

Blueprints are not static artifacts. Agents create, execute, evaluate, and refine them,
closing the loop between experimentation and sedimentation. A workflow that starts as
a one-off experiment can be crystallized into a Blueprint; an agent can later improve it
based on new results, guided by the domain knowledge encoded in Skills.

See the [Blueprint Crafting Guide](docs/guides/blueprint-crafting.md) for full documentation.

### Skills

A Skill is a directory containing a `SKILL.md` file and optional reference documents,
templates, and examples. Skills encode domain knowledge in a form that is
framework-agnostic and agent-readable -- any LLM-based agent capable of reading files
can use them.

```
feynrules-model-generator/
  SKILL.md                # Trigger conditions, inputs/outputs, workflow
  references/
    syntax-rules.md       # Condensed from official documentation
    naming-conventions.md
  templates/
    skeleton.fr           # Starter model file
```

Skills decouple domain expertise from agent implementation.
You can swap the underlying agent framework without rewriting your domain knowledge.
Blueprints draw on Skills as their knowledge foundation;
Skills, in turn, rely on Blueprints as their execution backbone.

### Jobs and Scheduling

Every computational task runs as a Job: a containerized process on a SLURM cluster.
Jobs are isolated via Apptainer `--containall`, with per-job ephemeral writable storage
that is created on launch and destroyed on completion.

The scheduler operates a four-tier priority system (A1 > A2 > B1 > B2).
A-class jobs can preempt B-class jobs when resources are scarce.
Preempted jobs are paused and re-queued automatically.

## Quickstart

Install the SDK:

```bash
pip install magnus-sdk
magnus login
```

Submit a Blueprint:

```python
import magnus

result = magnus.run_blueprint("validate-feynrules", args={
    "model": "~/models/minimal_Zp.fr",
    "lagrangian": "LSM",
})
print(result)
```

Or from the command line:

```bash
magnus run validate-feynrules --model ~/models/minimal_Zp.fr --lagrangian LSM
magnus logs -1    # view logs of the most recent job
```

Full SDK and CLI reference: [Magnus SDK Guide](docs/guides/sdk-and-cli.md).

## Used In

### [ColliderAgent](https://github.com/rise-agi/collider-agent)

Autonomous collider phenomenology from Lagrangian to exclusion limits.
ColliderAgent uses Magnus as its execution backend, orchestrating a five-stage
Blueprint pipeline across containerized HPC toolchains:

| Blueprint | What it does | Toolchain |
|-----------|-------------|-----------|
| `validate-feynrules` | Check model file for syntactic and physical consistency | Wolfram + FeynRules |
| `generate-ufo` | Compile a FeynRules model into UFO standard output | Wolfram + FeynRules |
| `madgraph-compile` | Enumerate Feynman diagrams, compute matrix elements, compile process directory | MadGraph5 |
| `madgraph-launch` | Run Monte Carlo event generation from a compiled process | MadGraph5 |
| `madanalysis-process` | Produce cross-section plots, cutflow tables, and analysis reports | MadAnalysis5 |

Each stage passes artifacts to the next through Magnus file custody,
and the entire pipeline can be driven by either a human through the web UI
or an agent through the SDK.

## Deployment

Magnus runs on a Linux server with access to a SLURM cluster.

```bash
# Clone and configure
cp configs/magnus_config.yaml.example configs/magnus_config.yaml
# Edit magnus_config.yaml: set server address, SLURM paths, auth credentials

# Backend
cd back_end && uv sync && uv run -m server.main

# Frontend
cd front_end && npm install && npm run dev
```

Requirements: Python >= 3.14, Node.js LTS, SLURM, Apptainer.
See [Job Runtime Documentation](docs/internals/job-runtime.md) for container execution details.

## Documentation

**User guides** — start here if you are building with Magnus:

- [SDK & CLI Guide](docs/guides/sdk-and-cli.md) — Python API, async support, and the full CLI reference
- [Blueprint Crafting Guide](docs/guides/blueprint-crafting.md) — Type annotations, parameter metadata, file transfer
- [Local Mode](docs/guides/local-mode.md) — Run Magnus locally with Docker instead of SLURM
- [Metrics Protocol](docs/guides/metrics.md) — Cross-language metrics reporting protocol for Jobs

<details>
<summary><strong>Internals &amp; references</strong> — for operators and contributors</summary>

- [Job Runtime](docs/internals/job-runtime.md) — Container isolation, environment variables, networking
- [uv Image Notes](docs/internals/uv-image.md) — Building the `magnus-runtime` container image with uv
- [Internal Links Requirements](docs/requirements/internal-links.md) — Design notes for the `magnus:///` URI scheme

</details>

All pages are bilingual; use the language switcher at the top of each document.

## Contributing

Magnus is actively evolving. If you run into rough edges, have ideas for improvement,
or want to contribute code, please open an issue or pull request.
We appreciate every report, suggestion, and patch.

Reach out directly at parkcai@126.com, we would genuinely love to hear from you.

## License

This project is licensed under the [GNU Affero General Public License v3.0](LICENSE).

## Acknowledgments

- [Apptainer](https://github.com/apptainer/apptainer) — Powers Magnus's container execution through its robust runtime for HPC workloads.
- [croc](https://github.com/schollz/croc) — Inspired Magnus's file custody feature through its frictionless approach to moving files.
