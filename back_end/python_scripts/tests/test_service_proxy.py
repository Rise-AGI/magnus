"""The upstream application receives business data, never Magnus credentials."""
import importlib.util
from pathlib import Path
import unittest

import httpx
from starlette.requests import Request


spec = importlib.util.spec_from_file_location("isolated_service_proxy", Path(__file__).resolve().parents[2] / "server/_service_proxy.py")
proxy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(proxy)


class ServiceCredentialTests(unittest.TestCase):
    def test_all_platform_auth_carriers_are_consumed(self):
        request = Request({
            "type": "http",
            "method": "POST",
            "path": "/service/run",
            "query_string": b"token=canary&choice=one&token=second-canary&choice=two",
            "headers": [
                (b"host", b"magnus.example"),
                (b"authorization", b"Bearer canary"),
                (b"proxy-authorization", b"canary"),
                (b"cookie", b"access_token=canary; preference=dark; token=canary"),
                (b"cookie", b"business_session=value"),
                (b"referer", b"https://magnus.example/service?token=canary"),
                (b"x-option", b"one"),
                (b"x-option", b"two"),
            ],
        })
        downstream = httpx.Request(
            "POST",
            "http://127.0.0.1:12345/run",
            headers=proxy.service_request_headers(request),
            params=proxy.service_request_params(request),
        )
        self.assertNotIn("canary", str(downstream.headers) + str(downstream.url))
        self.assertEqual(downstream.url.params.get_list("choice"), ["one", "two"])
        self.assertEqual(downstream.headers.get_list("x-option"), ["one", "two"])
        self.assertEqual(downstream.headers.get_list("cookie"), ["preference=dark", "business_session=value"])
        self.assertEqual(downstream.headers["host"], "127.0.0.1:12345")


if __name__ == "__main__":
    unittest.main()
