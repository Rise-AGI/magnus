> **Language / 语言**: **English** · [简体中文](image-building.zh-CN.md)

# Dockerfile Jobs

Jobs accept either `container_image` or `dockerfile`. Magnus prepares that runtime independently of the source repository, then executes the existing `entry_command`. Blueprints use the same Job submission interface.

## Submit

```python
from magnus import submit_job

job_id = submit_job(
    task_name="Python environment",
    entry_command="python -c 'import packaging; print(packaging.__version__)'",
    dockerfile="""FROM python:3.14-slim
RUN python -m pip install packaging==25.0
""",
)
```

Omitting the repository selects `Rise-AGI/magnus`, its default branch, and HEAD. The resolved branch and SHA are recorded for the Job. Explicit repositories and revisions remain supported; a non-default namespace requires a repository name.

```sh
magnus submit --task-name dependency-check --dockerfile-file ./Dockerfile --entry-command "python --version"
magnus job build JOB_ID
magnus job build JOB_ID --logs
```

`--dockerfile-file` reads a local UTF-8 file and sends its text. `--dockerfile` accepts text directly. SDK submission methods retain their positional parameter order and add `dockerfile` as a keyword-only argument. The SDK checks server support before sending Dockerfile text, so an old server cannot silently ignore it.

Explicit image and Dockerfile inputs are mutually exclusive. Omitting both retains the site default image; a supplied Dockerfile must not be blank.

## Build context and cache

The build context is **empty**. The Job repository, commit, entry command, and GPU count are not environment inputs. Local `COPY` and `ADD` have no source files; multi-stage `COPY --from` and explicitly remote sources retain their normal Dockerfile behavior. The repository is mounted when the prepared runtime executes the Job.

BuildKit owns step-level caching. Magnus combines identical concurrent requests and caches ready runtime copies; it does not parse Dockerfiles to decide shell-command equivalence. Different recipes can reuse common preceding steps. Artifacts use actual content identities, so rebuilding a recipe cannot overwrite an older SIF still being used by another Job.

SIF cache hits are checked against the recorded size and SHA-256. The backend remembers validated file metadata during its lifetime to avoid repeatedly hashing unchanged local files; relay validates cached content before reuse. Damaged copies are rebuilt or transferred again. Relay can discard an invalid copy before transfer when no other Job references it; referenced copies stay protected until atomic replacement, which requires space for both copies.

Docker/local loads the built image. HPC converts a Docker archive to SIF and uses existing SLURM/relay execution. No registry or permanent Image asset is required. Temporary archives can be removed after conversion. If a runtime copy is evicted, surviving BuildKit cache can supply it again; if those steps are also gone, it is rebuilt. Floating `FROM` tags and unpinned network dependencies may then change. Deleting a SIF alone does not invalidate BuildKit's cached `RUN` steps.

## State, progress and cancellation

An uncached runtime enters `Building`: waiting for a build slot, executing steps, exporting, and loading/converting are progress phases. `Pending` requires both repository and runtime to be ready. A ready cache hit can skip Building.

Build logs are available in the Job detail page's log panel, with Run logs / Build logs controls at the top right. Status badges in tables and detail pages only display status; they do not open a log viewer. Job lists do not carry Dockerfile text or build logs. Build logs retain roughly the most recent 4 MiB, with a truncation marker when earlier output is discarded. They remain available after failure, separately from execution logs, until the Job workspace is reclaimed. The progress endpoint returns HTTP 410 when historical diagnostics are unavailable, rather than reporting a new waiting build.

```python
from magnus import get_job_build, get_job_build_logs

progress = get_job_build(job_id)
logs = get_job_build_logs(job_id)
```

The existing terminate operation cancels a Job's subscription. Other Jobs waiting for that shared build continue; the last subscriber leaving requests cancellation of the actual solve and conversion. Ready artifacts are protected while queued, transferring, or running. Cache eviction and explicit deletion must honor that protection. Build diagnostics live outside the writable Job mount and share the retained Job artifacts' storage budget and cleanup lifecycle.

## Site setup

Building is opt-in through `execution.image_build`; old configurations leave it disabled. See the [configuration example](../../configs/magnus_config.yaml.example). The backend needs `buildctl` and a Magnus-dedicated BuildKit worker for the configured Linux platform. Each daemon exclusively owns its state directory. Linux can use rootless BuildKit; Docker/local can use a dedicated worker in its Docker Linux environment. Configure required host capabilities and worker CPU/memory limits separately; reserve that capacity if it shares compute hardware. User Jobs do not receive the builder socket.

TCP workers use the configured TLS files. Site credentials do not belong in Dockerfile text, Job commands or logs. The worker needs access to base images and package sources. Offline tenant compute nodes can receive the prepared SIF by relay without contacting the builder.

Configure base-image registry mirrors in the worker's `buildkitd.toml`. The existing `cluster.registry_mirror` only applies to Magnus image pulls; it does not configure BuildKit automatically. Validate both `FROM` pulls and dependency installation with `RUN` in the worker's actual network environment. Host connectivity alone does not prove that builds can reach their sources, especially when IPv4 and IPv6 egress have different access policies.

Rootless mode limits host privileges but does not automatically isolate networking. RootlessKit shares the host network by default, so Dockerfile `RUN` commands can access host loopback and the LAN. If network isolation is required, select a supported network driver and verify its DNS, egress, and package-source access. See the [BuildKit rootless deployment guide](https://github.com/moby/buildkit/blob/master/docs/rootless.md).

After a backend restart, preparation waits up to `timeout_seconds` for a known leftover build process to exit before reclaiming its scratch. On POSIX this includes remaining members of the recorded process group, even after its leader exits. Process lease updates use atomic replacement so an interrupted write preserves the previous complete record. An incomplete process lease, an interrupted spawn without an established process identity, or another live backend using the same root requires operator inspection; Magnus does not delete scratch whose owner it cannot establish.

`scratch_root` optionally selects an absolute parent directory for build exports and SIF conversion. Magnus creates a station-specific child beneath it, so independent instances can use the same parent without sharing scratch or recovery state. Leaving it unset preserves `server.ephemeral_root/image_build_tmp`. The child must be private to the backend account and cannot be a symlink or junction. Parent permissions must prevent another user from replacing it; a root-owned sticky directory such as `/dev/shm` is supported on POSIX. Windows checks owner and access-control entries rather than silently changing existing ACLs. When changing the location, initialization also checks the former default directory for unfinished work before reclaiming it. An arbitrary previous custom location must be drained before changing configuration.

`scratch_size` limits aggregate build scratch and defaults to `cache_size`. It is included in the total cache budget, not an additional allowance. Capacity checks run during conversion and before temporary artifacts are removed; a temporary peak can fail a build even if its final SIF would fit. These are sampled limits: use a bounded filesystem or volume for a hard ceiling. A fast disk or a bounded memory-backed volume can reduce conversion I/O, but tmpfs also consumes host memory. Plan its capacity separately from the BuildKit worker; Magnus does not mount volumes or automatically switch large builds into RAM.

For SIF conversion, `conversion_cpus` (default `2`) and `conversion_memory` (default `1G`) bound mksquashfs processors and working memory. They retain gzip compatibility with older Singularity consumers. They are not whole-process or cgroup limits, and the BuildKit daemon's own CPU/memory limits do not cover the backend's Apptainer subprocess. Configure operating-system limits separately where required. Docker/local still uses `docker load`; these two SIF settings do not change Docker import behavior.

Loading, conversion, hashing and copying do not hold the cache publication lock. A cached environment can be prepared while another recipe converts. In-flight incoming files and pending Docker tags remain protected from cleanup until publication or cancellation finishes; publication, waiter pins and rollback are serialized.

The budget includes build cache, managed runtime copies and export/conversion scratch. Relay applies the same configured capacity and free-space floor independently to its remote built-image cache, reserving space before transfer and protecting active Jobs. Cleanup targets only Magnus-owned files/images, never user-wide Docker pruning or the existing pulled-image cache. BuildKit's periodic GC is not an instantaneous disk quota: a strict physical limit requires a limited filesystem/volume including scratch, alongside free-space headroom and bounded concurrency. Docker's shared image store also needs capacity planning. If pinned data prevents reclamation, preparation must fail clearly rather than delete an active runtime.

Configure the worker's native GC policy for the retained step cache, including its maximum usage, reserved space and free-space target. Size it for the base image and installed dependencies as well as cache mounts; a large base image can consume nearly the whole allowance. Magnus reclaims worker data only for an actual aggregate-budget excess, retaining the amount that fits alongside local data. A scratch-limit or local-free-space failure does not by itself purge the worker cache. Capacity failures include the observed byte counts and limits; the backend log records cache reclamation before and after. Samples do not reconstruct an unrecorded historical peak.

If cache metadata exceeds BuildKit's RPC message limit, Magnus splits the usage query with disjoint server-side ID filters and reuses successful partitions. This does not remove cache records or change the budget. Queries remain bounded; if usage cannot be read completely, preparation fails instead of treating an unreadable cache as empty.

## Compatibility and acceptance

The upgrade preserves old Job rows and source semantics, adds Building and defers Dockerfile text from list queries. Existing Service submissions retain their image path; their active-Job handling recognizes Building. No special Blueprint is installed.

Production enablement requires runtime acceptance of step reuse, same-recipe concurrency, cancellation, restart, disk pressure, Docker execution, and Apptainer/Singularity consumption including relay. Static inspection cannot establish those runtime properties.

References: [BuildKit](https://docs.docker.com/build/buildkit/), [empty context](https://docs.docker.com/build/concepts/context/#empty-context), [cache invalidation](https://docs.docker.com/build/cache/invalidation/), [GC](https://docs.docker.com/build/cache/garbage-collection/).
