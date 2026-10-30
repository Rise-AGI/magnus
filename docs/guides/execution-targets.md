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

Consolidating existing sites also requires migrating identities, historical Jobs and valid custody
entries; merging configurations or redirecting domains is not sufficient. An offline administrator
migration can retain hashed aliases of the same identity's former trust tokens. They are bound to
the merged primary token and are revoked when it is rotated or replaced. HTTP authentication,
token login and chat authentication use the same rules. Browser JWT sessions are not migrated,
and there is no public endpoint for registering aliases.
An imported Blueprint can retain its originating target in the nullable
`default_execution_target` database column, without changing its Python code.
A returned explicit target other than `default` takes precedence; otherwise the
stored origin precedes the forwarding header and site default. New Blueprints
leave this administrative migration field unset.

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

## Read scheduling decisions and account limits

Two authenticated HTTP endpoints provide read-only observations without an SDK
upgrade:

- `GET /api/jobs/{job_id}/scheduling` reads the Job's stored target and current
  Slurm binding. It returns `scheduler_state`, `reason`, `expected_start_time`
  for pending jobs, `actual_start_time` otherwise, and `observed_at`. All
  timestamps are UTC. The forwarding endpoint's default target cannot change
  the identity of an existing Job.
- `GET /api/cluster/scheduling?execution_target=secondary` returns the configured
  scheduler user/account/QoS, visible `associations`, `qos_limits`,
  `partition_limits`, `usage` (Slurm login), and `account_usage` (configured
  shared account). Policy rows retain Slurm field names and
  TRES/time expressions, including inherited association values. Both job and
  partition QoS definitions are included when visible.

Use the site's existing `Authorization: Bearer <MAGNUS_TOKEN>` credential.
Job reading follows the existing authenticated collaboration policy.
`GET /api/jobs/capabilities` reports `scheduler_observations: true`.

```python
import os
import httpx

with httpx.Client(
    base_url=os.environ["MAGNUS_ADDRESS"].rstrip("/") + "/api/",
    headers={"Authorization": "Bearer " + os.environ["MAGNUS_TOKEN"]},
    timeout=40,
) as client:
    scheduling = client.get("jobs/" + job_id + "/scheduling")
    scheduling.raise_for_status()
    print(scheduling.json())
    policy = client.get(
        "cluster/scheduling",
        params={"execution_target": "secondary"},
    )
    policy.raise_for_status()
    print(policy.json())
```

`availability` is explicit. A Job can have `no_scheduler_id` during preparation,
submission reconciliation, or after its binding has been released. Use the
existing Job detail/build/staging endpoints to distinguish those phases.
`not_in_queue` means an empty successful controller query, not proof of successful
execution or release. `identity_mismatch` prevents a reused Slurm ID from being
reported as this Job; old allocations without a verifiable Magnus comment return
`identity_unverified`. Retired targets return `target_retired`; Docker/local
returns `unsupported` without invoking Slurm.

Cluster sections report their own availability and observation time. A partial
accounting failure does not discard readable usage. Usage covers this Slurm
login across partitions and accounts, including non-Magnus work; pending
requests and held allocations are separate. Node slots are summed per allocation,
not distinct physical nodes. Null resource counters mean unavailable, while zero
requires a successful empty/zero observation. CPU, memory (MiB), node slots, and
GPU counts come from Slurm TRES. Suspended allocations remain recorded and are
flagged; these values are not real-time consumption. `account_usage` is a separate
account-filtered observation across users. When Slurm hides other users' jobs
(`PrivateData=jobs`) or visibility cannot be verified, account totals are unavailable,
not a silently incomplete sum. Local submission identity comes from the process
executing Slurm, not the container runner.

Partition policies cover the target's default and GPU partitions. Optional QoS
per-account fields unsupported by an older CLI appear in `unavailable_fields`;
other readable limits remain available.

`association_usage` adds a scoped [controller association-cache](https://slurm.schedmd.com/scontrol.html#OPT_assoc_mgr) observation of the
configured/member accounts and their ancestors. Personal observations remain in
`usage` and `associations`. Each row identifies the cluster,
association ID, account, user/partition and parent. `group_tres` maps Slurm TRES
names to `{limit, used}`; `job_limits` does the same for visible `GrpJobs`,
`GrpSubmitJobs`, `GrpJobsAccrue` and corresponding `Max*` counters. For example,
an account's `group_tres.node = {"limit": 32, "used": 32}` can explain a group
node limit even when the user's own usage is zero. Memory uses MiB. These are
controller counters, not a sum of visible jobs or real-time hardware consumption;
do not sum parent and child rows.

A null limit represents Slurm's `N`/unset field at that association, not unlimited
effective capacity. Missing counters are listed in the row's `unavailable_fields`.
Hidden or unverified ancestors appear in `missing_accounts`; the section is
`partial` or `unavailable` with a safe error code. A readable parent remains
available even if a higher ancestor times out. This section does not replace
`account_usage`: controller counters may be visible when other users' queue
records are hidden. Queries follow only configured/member accounts and their
observed parent chain, with at most eight scoped requests within the existing
request budget. Each request selects one account and consumes only its first
matching account record, before the free-text comment. Unexpected ordering or
ignored filters remain unverified. Unrelated accounts and user rows are excluded.

`remaining_resources` is null: visible user limits do not prove that parent
account, partition, or QoS constraints permit more work. Some providers hide those
limits and shared usage. Blank policy values are not an assertion of unlimited
capacity. This API neither reports commercial credits nor reproduces Slurm's
hierarchical limit evaluator. See the official [resource-limit hierarchy](https://slurm.schedmd.com/resource_limits.html)
and [association/QoS field definitions](https://slurm.schedmd.com/sacctmgr.html).
Slurm's [expected start](https://slurm.schedmd.com/squeue.html) is an estimate
available with backfill scheduling; null means no estimate, not immediate start.

Queries have six-second command and thirty-second request budgets, and a 20-second server cache,
separate from execution reconciliation. At most one uncached observation per
controller connection and four site-wide run at a time; excess callers receive
HTTP 503 with `Retry-After: 2`. Other failures return section availability and a
fixed error code, without command output or secrets. Poll no faster than once
every 20 seconds and honor `Retry-After`. No endpoint submits, cancels, changes
priority, or rewrites the Job's execution status.

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

## Submission grants and forwarding endpoints

An optional `allowed_users: [user_id, ...]` on an execution target allows those
users and their descendants to submit. Put it at `execution.allowed_users` for
the base target, or inside an additional target. Omitted/null means unrestricted;
an empty list denies new submissions. Each target specifies its own policy; grants
do not inherit from the base. Job creation (including Blueprint runs) and service
creation/revival enforce it. Existing executions and shared Job reads are unchanged.

A reverse proxy consolidating an old API endpoint may set
`X-Magnus-Default-Execution-Target` to its former target. For incoming Job/Blueprint
submissions, new service selections and cluster queries, an omitted target or the
legacy ID `default` then resolves to that target. Other explicit IDs remain unchanged;
unknown targets are rejected. This header grants no authority: submission grants
still apply. It never changes target resolution for persisted Jobs or services.
Canonical endpoints should omit or clear this header. Redirect the old browser UI
to the canonical site so its target selector uses the consolidated configuration.

Imported Blueprint/Skill IDs that collide must receive distinct canonical IDs;
content must not silently overwrite another site's resource. An offline migration
can record `resource_aliases` for the original endpoint target, resource kind and
old ID. The forwarding header then resolves both path IDs and create/update body
IDs before normal ownership checks. Lists on an old endpoint omit primary resources whose IDs are shadowed by its
aliases, so every listed ID still denotes the same object on subsequent requests;
the canonical endpoint lists both. Aliases do not grant access, are not recursive and have no public registration API.

When unrelated source accounts share the same trust-token bytes, an administrator
must not merge their owners implicitly. A separate primary token plus a scoped
hash alias in `scoped_user_token_aliases` preserves the former endpoint's identity.
The forwarding header selects that scope before primary-token lookup; canonical
requests keep their normal identity. Scoped authentication bypasses the shared
authentication cache. Rotation or deletion leaves a revoked tombstone so a later
request cannot fall through to another account with the same token. The account
owner can retrieve its new primary token through the existing authorized token
endpoint. There is no public alias-registration API.

A shared former token already authorizes both original accounts through their
respective endpoints. Scoped aliases preserve that authority; they do not create
an isolation boundary between prior holders of identical credentials. Rotating
the primary token revokes its trust-token aliases and the previous primary token.
Already-issued JWTs retain the existing session-expiration behavior.
