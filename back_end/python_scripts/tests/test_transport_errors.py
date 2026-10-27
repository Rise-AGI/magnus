"""Transport diagnostics retain rejection evidence without injected credentials."""
import os
import subprocess
from types import SimpleNamespace
import unittest

from test_transport_timeouts import load_transport


class TransportErrorTests(unittest.TestCase):
    def transports(self, namespace):
        yield namespace["_LocalTransport"]()
        for initialization in (None, "/private/site-env.sh"):
            yield namespace["_SshControlMasterTransport"]("socket", "host", "user", shell_init=initialization)

    def test_nonzero_result_and_exception_do_not_expose_injected_token(self):
        namespace = load_transport()
        namespace["subprocess"] = SimpleNamespace(**vars(subprocess))

        def rejected(command, **kwargs):
            return subprocess.CompletedProcess(command, 1, "", "sbatch: limit exceeded; token-canary")

        namespace["subprocess"].run = rejected
        for transport in self.transports(namespace):
            with self.subTest(transport=transport):
                environment = dict(os.environ, MAGNUS_TOKEN="token-canary")
                result = transport.run(["sbatch"], env=environment)
                self.assertNotIn("token-canary", repr(result))
                with self.assertRaises(subprocess.CalledProcessError) as caught:
                    transport.run(["sbatch"], env=environment, check=True)
                self.assertEqual(caught.exception.returncode, 1)
                self.assertIn("limit exceeded", caught.exception.stderr)
                self.assertNotIn("token-canary", str(caught.exception) + repr(vars(caught.exception)))

    def test_timeout_retains_duration_and_redacts_byte_diagnostics(self):
        namespace = load_transport()
        namespace["subprocess"] = SimpleNamespace(**vars(subprocess))

        def timeout(command, **kwargs):
            raise subprocess.TimeoutExpired(command, 7, output=b"token-canary", stderr=b"token-canary")

        namespace["subprocess"].run = timeout
        for transport in self.transports(namespace):
            with self.subTest(transport=transport), self.assertRaises(subprocess.TimeoutExpired) as caught:
                transport.run(["sbatch"], env=dict(os.environ, MAGNUS_TOKEN="token-canary"))
            self.assertEqual(caught.exception.timeout, 7)
            self.assertNotIn("token-canary", str(caught.exception) + repr(vars(caught.exception)))
            self.assertTrue(caught.exception.__suppress_context__)


if __name__ == "__main__":
    unittest.main()
