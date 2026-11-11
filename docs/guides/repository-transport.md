# Repository transport

By default, Slurm uses GitHub SSH URLs and local execution uses HTTPS. Git's
existing `insteadOf` rules and credential helpers still apply.

Set `execution.github_https_url` to a trusted HTTPS origin to route repository
preparation and branch/commit queries through a Git Smart HTTP proxy:

```yaml
execution:
  github_https_url: https://github-proxy.example.org
```

The origin must not contain credentials, a path, query, or fragment. Git owns
credentials: configure the service account's credential helper for the proxy
host. The proxy must forward GitHub authentication securely. An SSH-form input
rewritten to HTTPS does **not** use the account's SSH key. Private repositories
require an HTTPS identity with repository access. There is no automatic direct
SSH fallback on authorization or proxy errors.

This setting also controls subsequent fetches of existing repository caches,
without rewriting their on-disk origin or invalidating their objects. Existing
branch refspecs remain in use. Full immutable commits already cached still avoid
network access. No database migration, SDK change, or image rebuild is needed.
The setting does not configure OCI registry mirrors or BuildKit egress.

Before enabling it, verify public and required private repositories with the
service account's HTTPS identity. Restart the control plane normally to load the
setting; do not rewrite or remove a cache while a preparation owns its lock.
