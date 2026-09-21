import importlib.util
from pathlib import Path
from types import SimpleNamespace
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "canary-s026-s029.py"
spec = importlib.util.spec_from_file_location("canary_s026_s029", SCRIPT)
module = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(module)


class Response:
    def __init__(self, url, *, status_code=200, location=None, content=b"ok"):
        self.url = url
        self.status_code = status_code
        self.headers = {"location": location} if location else {}
        self.content = content
        self.request = SimpleNamespace(url=url)
        self.closed = False

    def close(self):
        self.closed = True

    def raise_for_status(self):
        return None


class Session:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.responses.pop(0)


class LiveCanaryTransportTests(unittest.TestCase):
    HOSTS = {"official.test"}

    def test_external_redirect_is_rejected_before_following(self):
        session = Session([
            Response(
                "https://official.test/start",
                status_code=302,
                location="https://example.invalid/redirected",
            )
        ])
        with self.assertRaisesRegex(RuntimeError, "非白名單來源"):
            module.get(session, "https://official.test/start", self.HOSTS)
        self.assertEqual(len(session.calls), 1)

    def test_same_host_redirect_keeps_original_request_evidence(self):
        session = Session([
            Response("https://official.test/start", status_code=302, location="/next"),
            Response("https://official.test/next"),
        ])
        response = module.get(session, "https://official.test/start", self.HOSTS)
        evidence = module.response_evidence(response)
        self.assertEqual(evidence["requested_url"], "https://official.test/start")
        self.assertEqual(evidence["final_url"], "https://official.test/next")
        self.assertEqual(len(session.calls), 2)


if __name__ == "__main__":
    unittest.main()
