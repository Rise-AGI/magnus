"""Remove terminal Job scratch, including read-only image directories."""
import os
import shutil
import stat


def remove_job_path(
    path,
):
    if not os.path.lexists(path):
        return
    if os.path.islink(path) or not os.path.isdir(path):
        os.unlink(path)
        return

    def make_traversable(
        parent_fd,
        name,
    ):
        metadata = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        if not stat.S_ISDIR(metadata.st_mode):
            return
        directory_fd = os.open(
            name,
            os.O_PATH | os.O_DIRECTORY | os.O_NOFOLLOW,
            dir_fd=parent_fd,
        )
        try:
            metadata = os.fstat(directory_fd)
            permissions = stat.S_IMODE(metadata.st_mode) | 0o700
            if metadata.st_uid == os.geteuid() and permissions != stat.S_IMODE(metadata.st_mode):
                # O_PATH pins even mode-000 directories. Older compute-node
                # Python cannot combine chmod(dir_fd=...) with no-follow;
                # procfs addresses this pinned inode without resolving job links.
                os.chmod("/proc/self/fd/" + str(directory_fd), permissions)
            readable_fd = os.open(".", os.O_RDONLY | os.O_DIRECTORY, dir_fd=directory_fd)
            try:
                for child in os.listdir(readable_fd):
                    make_traversable(readable_fd, child)
            finally:
                os.close(readable_fd)
        finally:
            os.close(directory_fd)

    if hasattr(os, "O_PATH") and os.path.isdir("/proc/self/fd"):
        absolute = os.path.abspath(path)
        parent_fd = os.open(os.path.dirname(absolute), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            make_traversable(parent_fd, os.path.basename(absolute))
        finally:
            os.close(parent_fd)
    shutil.rmtree(path)
