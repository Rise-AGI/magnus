"""Interpret the literal mount-array subset supported by Docker/local mode."""

import re


_MOUNT_ARRAY_START = re.compile(
    r"\bmounts\s*=\s*\(",
)
_HOME = re.compile(
    r"\$(?:\{HOME\}|HOME(?![a-zA-Z0-9_]))",
)
_WINDOWS_DRIVE = re.compile(
    r"^[a-zA-Z]:[\\/]",
)


def _literal_mount_entries(
    command: str,
) -> list[str]:
    array = _MOUNT_ARRAY_START.search(
        command,
    )
    if array is None:
        return []
    entries = []
    quoted_start = None
    # A single pass also bounds malformed arrays containing many openers.
    for position in range(
        array.end(),
        len(command),
    ):
        character = command[position]
        if character == '"':
            if quoted_start is None:
                quoted_start = position + 1
            else:
                entries.append(
                    command[quoted_start:position],
                )
                quoted_start = None
        elif character == ")" and quoted_start is None:
            return entries
    return []


def docker_bind_mounts(
    system_entry_command: str,
    *,
    host_home: str,
) -> list[str]:
    """Read double-quoted entries without executing the surrounding shell.

    Only HOME is expanded. Other shell expressions remain literal; this is
    deliberately not a Bash evaluator or an APPTAINER_BIND environment reader.
    """
    binds = []
    for entry in _literal_mount_entries(
        system_entry_command,
    ):
        # A Windows drive colon belongs to the source, not the mount delimiter.
        drive = _WINDOWS_DRIVE.match(
            entry,
        )
        separator = entry.find(
            ":",
            2 if drive else 0,
        )
        if separator < 0:
            continue
        host_path = _HOME.sub(
            lambda match: host_home,
            entry[:separator],
        )
        container_path = _HOME.sub(
            "/root",
            entry[separator + 1:],
        )
        if _WINDOWS_DRIVE.match(
            host_path,
        ):
            suffix = host_path[2:].replace(
                "\\",
                "/",
            )
            host_path = "/" + host_path[0].lower() + suffix
        binds.append(
            f"{host_path}:{container_path}",
        )
    return binds
