# sdks/python/src/magnus/cli/main.py
import sys
import re
import signal
import logging
from .commands import app


def _restore_default_sigpipe():
    # Python intercepts SIGPIPE and re-raises it as BrokenPipeError + exit
    # code 1, which makes `magnus ... | head` look like a failure under
    # `set -o pipefail`. Restoring SIG_DFL lets the kernel terminate us
    # with the conventional 141, matching git/kubectl/cat behavior.
    # SIGPIPE does not exist on Windows.
    if hasattr(signal, "SIGPIPE"):
        signal.signal(signal.SIGPIPE, signal.SIG_DFL)


def _ensure_utf8_stdio():
    # Banner and many outputs contain non-ASCII (©, ·, CJK). On Windows
    # cp936 terminals, writing such chars raises UnicodeEncodeError. Force
    # UTF-8 with replace so the CLI runs on any legacy codepage.
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        reconfigure(encoding="utf-8", errors="replace")


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
    _restore_default_sigpipe()
    _ensure_utf8_stdio()
    logging.getLogger("magnus").setLevel(logging.ERROR)
    _preprocess_argv()
    app()


if __name__ == "__main__":
    main()