# sdks/python/src/magnus/cli/main.py
import sys
import re
import logging
from .commands import app


def _preprocess_argv():
    """
    预处理 sys.argv，让负数索引能被正确解析。
    magnus status -1  →  magnus status -- -1
    magnus kill -2 -f →  magnus kill -- -2 -f
    """
    if len(sys.argv) < 3:
        return

    cmd = sys.argv[1]
    if cmd not in ("status", "kill"):
        return

    first_arg = sys.argv[2]
    if re.match(r"^-\d+$", first_arg):
        sys.argv.insert(2, "--")


def main():
    logging.getLogger("magnus").setLevel(logging.ERROR)
    _preprocess_argv()
    app()


if __name__ == "__main__":
    main()