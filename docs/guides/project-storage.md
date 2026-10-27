# Project directories on shared storage

This convention applies to Slurm targets whose compute nodes share a POSIX
filesystem. It uses the existing `APPTAINER_BIND` in `system_entry_command`
(`SINGULARITY_BIND` for SingularityCE). It adds no Job field, mount API or SDK
interface. Mounts support reads and writes; only an explicit `:ro` makes a mount
read-only. Existing custody upload and download functions remain available.

## Paths and ownership

The site operator assigns each project a stable host directory:

```text
/shared/account/magnus-projects/<project-id>/
    project.json
    inputs/
    cache/
    runs/<run-id>/
    staging/<writer-id>/
```

Use lowercase letters, digits and hyphens for `project-id`. `project.json` records
the project owner, execution target, host and container paths, and retention
agreement. It contains no passwords, tokens or custody secrets. Access follows
the site's Unix accounts and file permissions; directories belonging to the same
Unix account do not isolate projects from one another.

Keep this root outside Magnus-managed workspace, ephemeral, image-cache and
custody directories. It survives Job completion and workspace reclamation, uses
the cluster's shared-storage quota, and is not covered by `ephemeral_storage`.
Retention is not a backup: deliver important results to independent storage too.

## Mounting and running

Append this Apptainer example after the target's default `system_entry_command`.
A custom value replaces that default, so preserve the site's existing setup.

```bash
project_root=/shared/account/magnus-projects/my-project
test -d "$project_root" || exit 1
existing_bindings="${APPTAINER_BIND:-${SINGULARITY_BIND:-}}"
export APPTAINER_BIND="${existing_bindings:+$existing_bindings,}$project_root:/project:rw"
```

For SingularityCE, append the same mount declaration to `SINGULARITY_BIND`.
Callers can also mount a particular input or cache version as `:ro`, with a
separate `:rw` output directory. Avoid commas, colons and newlines in mount paths:
they delimit bind strings. Use an independent container path such as `/project`.
Do not nest it under `/magnus/workspace`, whose parent mount the wrapper appends
later and which can hide a custom child mount.

Choose a unique `run-id` before each submission and associate it with the Job ID
and target in the submission record. Write retained outputs to
`/project/runs/<run-id>/`; relative paths in the ordinary workspace are not copied
there automatically. Ranks use distinct filenames or an explicit single writer.
Coordinate completion across nodes with communication barriers or a file-ready
protocol; a directory's existence alone does not prove its contents are complete.

## Reusing inputs and assembled data

The project owner defines data compatibility and publication. Published versions
have a complete file list, sizes and SHA-256 hashes, with source, input, format,
numeric precision and relevant environment identities. Cache keys distinguish
conditions that affect compatibility; a previous Job ID alone is insufficient.

1. Produce data in a unique `staging/<writer-id>/` without overwriting a version
   that readers are using.
2. Finish and close writes, synchronize as required, and verify a complete manifest.
3. Acquire an exclusive publication lock per version with atomic `mkdir`, for
   example `staging/.publish-<version>.lock`. A contender that cannot create it
   waits or verifies the published version instead of publishing concurrently.
   Under the lock, check that the destination is absent, rename the complete
   directory to `cache/<version>/` on the same filesystem, then release the lock.
   All publishers must honor the same lock. Use a direct rename operation, not
   the default `mv` behavior that can nest a source inside an existing directory.
   Existing versions may be verified and reused, never overwritten or merged.
   Preserve staging on failure; confirm the original writer has stopped before
   clearing an abandoned lock.
4. Readers use only published versions and verify identity and required files.
   Missing or incompatible data fails explicitly; the project owner decides
   whether to rebuild. Reading a cache need not copy its entire directory again.

A manifest's SHA-256 can name the version directory. Preserve imported manifests
and their original hashes; handle relocated paths through the reader's root-path
mapping. Published versions remain unchanged by convention; updates get a new
version. Mutable intermediate state belongs in the writer's run or staging
directory. This convention does not force the whole project mount to be read-only.

## Retention, cleanup and delivery

The project owner records input/cache versions used by submissions, including
intents that have not received a Job ID. Before deleting data, check queued,
preparing, running and releasing Jobs and other readers. Preserve data when usage
is uncertain. Magnus workspace reclamation does not clean project directories
or expire their caches by age.

Later Jobs on the same shared filesystem can mount the data directly. For local
downloads, cross-site transfer or delivery, keep using `custody_file`,
`download_file` and their CLI equivalents; authorized SSH/SCP remains available.
SSH remote execution targets support input staging. For their compute nodes
without external network access, include each literal
`magnus-secret:<token>` in the submitted `entry_command` or `system_entry_command`
to let the platform stage the input before execution. A secret obtained only at
runtime is not pre-staged; its download attempts HTTP and needs network access.
Local Slurm targets do not perform this staging and need an HTTP download path.
An output secret from `custody_file` becomes downloadable after the controller
imports its payload and reports ready. See the [SDK and CLI guide](sdk-and-cli.md).
Mounts do not change custody readiness, download counts, expiration or retention
after a Job's terminal state. Verify file manifests and SHA-256 on delivery;
custody receipts do not replace long-term preservation records.

Site acceptance covers two-node access, actual writes and reads, explicit
read-only mounts, retention across Jobs, and existing custody upload/download.
The site handoff supplies concrete host paths and validation results.
