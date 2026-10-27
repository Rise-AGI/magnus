"""Exercise remote shell initialization locally without an SSH endpoint."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest

from test_transport_timeouts import load_transport


@unittest.skipUnless(os.name != "nt" and shutil.which("bash"), "remote Bash integration uses POSIX paths")
class RemoteShellInitializationTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.init = self.root / "site's initialization.sh"
        self.init.write_text(
            "export MAGNUS_TEST_REMOTE_CONTEXT=ready\n"
            "read -r ignored && exit 91\n"
            "printf 'site startup banner\\n'\n",
        )
        self.namespace = load_transport()
        self.namespace["subprocess"] = SimpleNamespace(**vars(subprocess))
        self.namespace["subprocess"].run = self.run_remote_locally
        self.transport = self.namespace["build_transport"]({
            "mode": "ssh",
            "ssh": {
                "control_path": "unused.sock",
                "host": "unused.invalid",
                "user": "test",
                "shell_init": str(self.init),
            },
        })

    def run_remote_locally(
        self,
        command,
        **kwargs,
    ):
        return subprocess.run(
            ["bash", "-c", command[-1]],
            **kwargs,
        )

    def test_initialized_context_preserves_script_input_and_literal_arguments(self):
        argument = "spaces ' quotes; $(echo injected)\nsecond line"
        script = "#!/bin/bash\nprintf 'a Job script\\n'\n"
        environment = dict(os.environ, MAGNUS_TEST_INJECTED="literal value")
        result = self.transport.run(
            [
                sys.executable,
                "-c",
                "import json,os,sys; print(json.dumps([os.environ['MAGNUS_TEST_REMOTE_CONTEXT'],os.environ['MAGNUS_TEST_INJECTED'],sys.argv[1],sys.stdin.read()]))",
                argument,
            ],
            input=script,
            env=environment,
            check=True,
            timeout=5,
        )
        self.assertEqual(json.loads(result.stdout), ["ready", "literal value", argument, script])
        self.assertEqual(result.stderr, "site startup banner\n")

    def test_failed_or_missing_initialization_never_executes_command(self):
        marker = self.root / "unexpected-command"
        self.init.write_text("return 17\n")
        command = [sys.executable, "-c", "import pathlib,sys; pathlib.Path(sys.argv[1]).touch()", str(marker)]
        with self.assertRaises(subprocess.CalledProcessError) as failure:
            self.transport.run(command, check=True, timeout=5)
        self.assertEqual(failure.exception.returncode, 17)
        self.assertFalse(marker.exists())
        self.init.unlink()
        result = self.transport.run(command, timeout=5)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(marker.exists())

    def test_file_input_preserves_header_and_all_binary_bytes(self):
        stream = self.namespace["_stream_file_to_command"]

        def stream_locally(
            command,
            *args,
            **kwargs,
        ):
            return stream(["bash", "-c", command[-1]], *args, **kwargs)

        self.namespace["_stream_file_to_command"] = stream_locally
        payload = bytes(range(256)) * 4096
        prefix = b"header\x00\n"
        source = self.root / "source.bin"
        source.write_bytes(payload)
        result = self.transport.run_with_file_input(
            [
                sys.executable,
                "-c",
                "import hashlib,os,sys; assert os.environ['MAGNUS_TEST_REMOTE_CONTEXT']=='ready'; print(hashlib.sha256(sys.stdin.buffer.read()).hexdigest())",
            ],
            str(source),
            prefix=prefix,
            timeout=5,
        )
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout.strip(), hashlib.sha256(prefix + payload).hexdigest())
        self.assertEqual(result.stderr, "site startup banner\n")

    def test_bounded_download_is_initialized_without_banner_corruption(self):
        receive = self.namespace["receive_file"]

        def receive_locally(
            command,
            *args,
            **kwargs,
        ):
            # The initialized PATH supplies the remote Python executable.
            return receive(["bash", "-c", command[-1]], *args, **kwargs)

        self.namespace["receive_file"] = receive_locally
        executable = self.root / "python3"
        executable.write_text(
            "#!/bin/bash\n"
            "test \"${MAGNUS_TEST_REMOTE_CONTEXT:-}\" = ready || exit 93\n"
            "exec " + shlex.quote(sys.executable) + ' "$@"\n',
        )
        executable.chmod(0o700)
        with self.init.open("a") as file:
            file.write("export PATH=" + shlex.quote(str(self.root)) + ':"$PATH"\n')
        payload = bytes(range(256)) * 4096
        source = self.root / "remote's file.bin"
        destination = self.root / "received.bin"
        source.write_bytes(payload)
        self.transport.fetch_bounded(
            str(source),
            str(destination),
            size=len(payload),
            timeout=5,
        )
        self.assertEqual(destination.read_bytes(), payload)

    def test_unconfigured_transport_keeps_direct_command_and_no_bash_requirement(self):
        commands = []
        self.namespace["subprocess"].run = lambda command, **kwargs: commands.append(command)
        transport = self.namespace["build_transport"]({
            "mode": "ssh",
            "ssh": {"control_path": "unused.sock", "host": "unused.invalid", "user": "test"},
        })
        transport.run(["squeue", "--noheader"], timeout=5)
        self.assertEqual(commands[0][-1], "squeue --noheader")


if __name__ == "__main__":
    unittest.main()
