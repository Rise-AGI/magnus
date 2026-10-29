# Multiple execution clusters

One Magnus site can drive several Slurm clusters while sharing its users, local
image builder, bounded caches and Job database. Each Job selects one execution
target. Magnus does not automatically move a Job between clusters or submit it
to several clusters.

If resource accounting is unavailable, `/api/cluster/stats` returns its existing
HTTP 503 response. The backend log records the target, exception type, numeric
process/OS status and code locations, at most once per target per minute. Command
arguments, exception messages, raw output and traceback locals are excluded from
this diagnostic. This distinguishes transport failures from parsing/validation
failures without exposing credentials or treating unknown capacity as zero.

The existing top-level `transport`, `execution.slurm`, `execution.container_runtime`
and `cluster` describe the default target. Old configurations need no changes.
Additional targets are optional overrides:

```yaml
execution:
  backend: slurm
  target_id: primary
  target_name: Primary
  # Existing runtime, builder and Slurm settings remain here.
  targets:
    secondary:
      name: Secondary
      transport:
        mode: ssh
        ssh:
          control_path: /home/operator/.ssh/secondary.sock
          host: secondary.example.org
          user: account
          remote_root: /shared/home/account/magnus-data
      slurm:
        partition: compute
        account: project
        mem_mode: per_cpu
        mem_per_cpu_mb: 4000
        module_init: /etc/profile.d/modules.sh
        module_loads: [singularity/3.11]
        mpi_type: pmi2
      container_runtime: singularity
      cluster:
        max_cpu_count: 64
        max_memory_demand: 256G
        max_node_count: 8
        default_runner: account
        scheduling: {mode: tenant}
        gpus: []
```

This is a fragment to merge into a complete site configuration. Slurm and cluster
fields inherit from the base target; a supplied transport replaces that whole
section so credentials and remote paths cannot accidentally carry across targets.
The site-wide builder, storage budgets, authentication and server settings cannot
be overridden per target. Use separate remote roots and control sockets for
independent execution environments. Multiple targets require the Slurm backend;
Docker/local keeps its existing single-host behavior.

Targets may also override `container_image_mode` (`native` by default) and
`container_prepare_timeout_seconds` (300 by default). On runtimes that extract
every SIF invocation, `sandbox` prepares one read-only directory image per Job
before launching its tasks. See [runtime image preparation](../internals/job-runtime.md#runtime-image-preparation)
for writable temporary directories, same-user ownership, storage and cleanup requirements.

Target IDs use letters, digits, `_` and `-`, start with a letter or digit, and are
at most 64 characters. Keep IDs and the default target identity stable. New Jobs
store the resolved ID, including when the caller omits it. Legacy rows with a
NULL target belong to the base deployment. Removing a target with unfinished Jobs
causes startup to fail rather than route them elsewhere. Finished Job history
remains readable after a target is retired. Services retain their target too; old clients that omit the field on an update
keep that selection. Stop a service before changing its target. SSH execution
still does not support the existing local service proxy.

## Submit and inspect

The Job drawer and Cluster page show a selector only on sites with multiple
targets. Job clones and clipboard payloads retain their execution target.
Cluster task lists include Magnus Jobs from every target. My active Jobs still
follow the current user's reporting hierarchy. The selected target sorts first,
then the existing lifecycle, priority and time order applies before pagination.
External Slurm tasks and resource cards cover only the selected node pool; Magnus
running rows precede external rows. Only the selected Slurm connection is queried
live; other Magnus Jobs use their scheduler-synchronized database state.

```python
targets = client.get_execution_targets()
job_id = client.submit_job(
    task_name="example",
    entry_command="python main.py",
    execution_target="secondary",
)
stats = client.get_cluster_stats(execution_target="secondary")
```

```bash
magnus cluster --targets --format json
magnus cluster --execution-target secondary
magnus submit --task-name example --entry-command 'python main.py' --execution-target secondary
```

`GET /api/cluster/targets` lists targets and resource settings.
`GET /api/cluster/stats` and `/api/cluster/my-active-jobs` accept an optional
`execution_target` query parameter. Job submissions accept the same field.
An unknown ID is rejected. Omission keeps the default-target behavior. New SDK
arguments are keyword-only and do not change existing positional calls. Explicit
target selection checks server capabilities before submission so an old server
cannot silently ignore the field. SDK source changes do not imply a PyPI release.

Slurm IDs are scoped by target in reconciliation, cancellation and snapshots.
Cluster matching groups partition targets that share a configured SSH host,
account and control socket (or the local controller). Independent controllers'
Slurm IDs never identify the same Magnus Job. Each scheduler worker has at most one
reconciliation and one submission pass in flight. Slow remote I/O on one target
does not block another. Local workspace reclamation remains site-wide and protects
in-flight work from every target. Cluster query caches and authentication locks
are independent per target/control socket.

Targets for partitions of the same cluster may inherit the same transport and
remote root. Remote built-image cache locking and protection cover all targets
using the same SSH host, account and normalized root, including legacy Jobs and
Jobs still releasing resources. Different control sockets do not split that
cache. Configure a single canonical host name and root for a shared directory;
Magnus does not resolve equivalent host aliases or remote directory symlinks.
Independent remote caches retain independent transfer locks and protection sets.

## Official access clients and renewal

An administrator can delegate authentication to an installed access client:

```yaml
transport:
  mode: ssh
  ssh:
    # control_path, host, user and remote_root are still required.
    auto_connect:
      command: [/home/operator/bin/cluster-connect, primary]
      timeout_seconds: 90
      control_persist: 8h
```

`command` is an argv array, executed without a shell. It is administrator-owned
configuration, never a Job-supplied command. It is mutually exclusive with the
existing password/TOTP settings, which remain supported. Magnus supplies:

| Environment variable | Meaning |
| --- | --- |
| `MAGNUS_SSH_CONTROL_PATH` | Required OpenSSH control socket |
| `MAGNUS_SSH_HOST` | Configured execution host identity |
| `MAGNUS_SSH_USER` | Configured account |
| `MAGNUS_SSH_CONTROL_PERSIST` | Requested master idle lifetime |

The connector must establish that socket using the provider's supported client
and authentication, strictly verify pinned host keys, and exit successfully.
Magnus checks the master before using it. A zero exit code alone is insufficient.
SSH/SCP reuse only the established master and never fall back to a different
connection path when it disappears.

Magnus invokes the connector only when the master is absent. Authentication is
single-flight per socket, respects the calling operation's remaining deadline,
and terminates the connector process group on timeout (the process tree on
Windows). The connector must not detach unfinished authentication helpers.
Connector output is not copied to Job logs because it may contain credentials.
Keep credentials in owner-only files or a credential store outside the repository;
the connector owns any shared provider login lock and token renewal. The default
connector timeout is 45 seconds; a shorter command/transfer deadline takes
precedence. `ControlPersist` is an idle timeout, not a promise that a provider
session cannot expire earlier.

For module-based runtimes, `execution.slurm.module_init` optionally names the
shell initialization file to source before `module_loads`. Both initialization
and module-load failures stop submission execution before the wrapper starts.
With no module settings, the original batch environment is unchanged.

Some clusters also require login-node initialization before submitting a Job,
for example to export the identity information used on compute nodes. SSH command
sessions do not automatically run login profiles. Set the optional
`transport.ssh.shell_init` to the absolute path of the provider's initialization
script. Magnus sources it with Bash before each remote command, including `sbatch`,
queries and streamed file operations; exported variables reach Slurm through its
normal environment inheritance. An initialization failure stops that command.
The script receives no stdin, and its stdout is redirected to stderr to preserve
batch input, scheduler output and binary transfers. It must be noninteractive and
finish within the operation's existing timeout. This administrator-owned setting
does not change SSH authentication or SFTP/SCP sessions. Omit it to retain the
existing behavior; `module_init` still runs separately inside the batch Job.
