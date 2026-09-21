import importlib.util
from pathlib import Path
from unittest import mock
import unittest


ROOT = Path(__file__).resolve().parents[1]


def load_module(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / filename)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


audit = load_module("build_seven_day_source_audit", "build-seven-day-source-audit.py")
inventory = load_module("inventory_ey_questions", "inventory-ey-questions.py")


class Response:
    def __init__(self, url, *, status_code=200, location=None, content=b"ok"):
        self.url = url
        self.status_code = status_code
        self.headers = {"Location": location} if location else {}
        self.content = content
        self.apparent_encoding = "utf-8"
        self.closed = False

    def close(self):
        self.closed = True

    def raise_for_status(self):
        return None


class RequestsTransport:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        return self.responses.pop(0)


class Session:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.responses.pop(0)


class SourceTransportTests(unittest.TestCase):
    def test_audit_rejects_external_redirect_before_following(self):
        transport = RequestsTransport([
            Response("https://official.test/start", status_code=302, location="https://evil.test/next")
        ])
        with mock.patch.object(audit.requests, "request", side_effect=transport.request):
            with self.assertRaisesRegex(ValueError, "不允許"):
                audit.get("https://official.test/start", allowed_hosts={"official.test"})
        self.assertEqual(len(transport.calls), 1)
        self.assertFalse(transport.calls[0][2]["allow_redirects"])

    def test_audit_follows_same_host_redirect(self):
        transport = RequestsTransport([
            Response("https://official.test/start", status_code=302, location="/next"),
            Response("https://official.test/next"),
        ])
        with mock.patch.object(audit.requests, "request", side_effect=transport.request):
            response = audit.get("https://official.test/start", allowed_hosts={"official.test"})
        self.assertEqual(response.url, "https://official.test/next")
        self.assertEqual(len(transport.calls), 2)

    def test_inventory_rejects_external_redirect_before_following(self):
        session = Session([
            Response("https://query.ey.gov.tw/start", status_code=302, location="https://evil.test/next")
        ])
        with self.assertRaisesRegex(ValueError, "不允許"):
            inventory.fetch(session, "https://query.ey.gov.tw/start")
        self.assertEqual(len(session.calls), 1)
        self.assertFalse(session.calls[0][1]["allow_redirects"])

    def test_inventory_follows_same_host_redirect(self):
        session = Session([
            Response("https://query.ey.gov.tw/start", status_code=302, location="/next"),
            Response("https://query.ey.gov.tw/next"),
        ])
        response = inventory.fetch(session, "https://query.ey.gov.tw/start")
        self.assertEqual(response.url, "https://query.ey.gov.tw/next")
        self.assertEqual(len(session.calls), 2)


if __name__ == "__main__":
    unittest.main()
