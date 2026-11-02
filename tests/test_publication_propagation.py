"""Deterministic propagation races; no real network and no state writes."""
import contextlib
import importlib.util
import io
import json
from pathlib import Path
import unittest
from unittest import mock
from urllib.error import HTTPError, URLError

spec = importlib.util.spec_from_file_location("propagation_state", Path(__file__).resolve().parents[1] / "scripts/publication-state-branch.py")
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

class Clock:
    def __init__(self): self.now = 0
    def monotonic(self): return self.now
    def sleep(self, seconds): self.now += seconds

class Response(io.BytesIO):
    def __init__(self, body, status=200):
        super().__init__(body)
        self.status = status
        self.headers = {"Last-Modified": "Sun, 01 Nov 2026 17:00:26 GMT", "Age": "2"}

class PropagationTests(unittest.TestCase):
    def setUp(self):
        self.bundle = {p: ("fixture:" + p).encode() for p in m.STATE_PATHS}
        self.clock = Clock()
        self.calls = []
    def run_probe(self, behavior, **kwargs):
        def open_url(url, timeout):
            self.calls.append((url, timeout))
            name = url.split("/data/")[-1]
            key = next(p for p in m.PUBLIC_PATHS if p.endswith("/" + name))
            return behavior(key, self.bundle[key])
        stack = contextlib.ExitStack()
        self.addCleanup(stack.close)
        stack.enter_context(mock.patch.object(m, "build_opener", return_value=mock.Mock(open=open_url)))
        stack.enter_context(mock.patch.object(m.time, "monotonic", self.clock.monotonic))
        stack.enter_context(mock.patch.object(m.time, "sleep", self.clock.sleep))
        self.output = io.StringIO()
        stack.enter_context(contextlib.redirect_stdout(self.output))
        return m.verify_public_files("https://reese-max.github.io/taichung-police-intel/", self.bundle, **kwargs)
    def test_delayed_propagation_after_old_retry_window(self):
        def body(key, value):
            return Response(b"old" if self.clock.now < 6 and key.endswith("source-status.json") else value)
        proof = self.run_probe(body)
        self.assertEqual(proof["attempt"], 3)
        self.assertEqual(self.clock.now, 10)
        self.assertEqual(proof["verified_files"], {k:m.digest(self.bundle[k]) for k in m.PUBLIC_PATHS})
        self.assertEqual(len(self.calls), 15)
    def test_old_three_attempt_budget_reproduces_race(self):
        with self.assertRaisesRegex(RuntimeError, "verification failed"):
            self.run_probe(lambda key,value:Response(b"old" if self.clock.now<6 else value),attempts=3,delay=2)
        self.assertEqual(self.clock.now,4)
    def test_permanent_old_bytes_never_pass(self):
        with self.assertRaisesRegex(RuntimeError, "observed_sha256"):
            self.run_probe(lambda key,value:Response(b"old"), attempts=2)
        self.assertEqual(len(self.calls),10)
        self.assertEqual(self.clock.now,5)
    def test_status_503_with_matching_bytes_is_not_success(self):
        with self.assertRaisesRegex(RuntimeError, '"http_status": 503'):
            self.run_probe(lambda key,value:Response(value,503),attempts=1)
    def test_oversized_body_is_rejected(self):
        with mock.patch.object(m,"MAX_FILE_BYTES",4),self.assertRaisesRegex(RuntimeError,'"oversized": true'):
            self.run_probe(lambda key,value:Response(b"12345"),attempts=1)
    def test_error_diagnostics_do_not_copy_server_exception_text(self):
        def error(key,value): raise URLError("PRIVATE_RESPONSE_MARKER")
        with self.assertRaises(RuntimeError) as e:self.run_probe(error,attempts=1)
        self.assertNotIn("PRIVATE_RESPONSE_MARKER",str(e.exception))
        self.assertIn("URLError",str(e.exception))
    def test_http_error_status_is_recorded(self):
        def error(key,value):raise HTTPError("https://example.invalid",404,"missing",{},None)
        with self.assertRaisesRegex(RuntimeError,'"http_status": 404'):self.run_probe(error,attempts=1)
    def test_total_budget_stops_later_attempts(self):
        with self.assertRaisesRegex(RuntimeError,'"max_wait_seconds": 7'):
            self.run_probe(lambda key,value:Response(b"old"),max_wait=7)
        self.assertEqual(self.clock.now,7)
        self.assertEqual(len(self.calls),10)
    def test_network_timeout_uses_remaining_budget(self):
        self.run_probe(lambda key,value:Response(value),max_wait=2)
        self.assertTrue(all(timeout==2 for _,timeout in self.calls))
    def test_all_files_are_rechecked_after_partial_success(self):
        def body(key,value):
            return Response(b"old" if self.clock.now==0 and key==m.PUBLIC_PATHS[-1] else value)
        proof=self.run_probe(body)
        self.assertEqual(proof["attempt"],2)
        self.assertEqual(len(self.calls),10)
    def test_headers_expected_actual_and_size_are_reported(self):
        proof=self.run_probe(lambda key,value:Response(value))
        row=proof["verification_attempts"][0]["files"][0]
        self.assertEqual(row["last_modified"],"Sun, 01 Nov 2026 17:00:26 GMT")
        self.assertEqual(row["age"],"2")
        self.assertEqual(row["expected_sha256"],row["observed_sha256"])
        self.assertGreater(row["received_bytes"],0)
    def test_invalid_budgets_rejected_before_network(self):
        for values in ({"attempts":0},{"attempts":26},{"attempts":True},{"delay":-1},{"max_wait":0},{"max_wait":301},{"max_wait":float("nan")},{"delay":float("inf")},{"max_wait":True}):
            with self.subTest(values=values),self.assertRaisesRegex(RuntimeError,"budget"):
                m.verify_public_files("https://reese-max.github.io/taichung-police-intel",self.bundle,**values)
    def test_unapproved_origin_rejected(self):
        with self.assertRaisesRegex(RuntimeError,"approved Pages"):
            m.verify_public_files("https://unapproved.example.test/",self.bundle)

    def test_body_finishing_after_budget_cannot_pass(self):
        clock = self.clock
        class Slow(Response):
            def read1(self, size):
                clock.now += 3
                return super().read1(size)
        with self.assertRaisesRegex(RuntimeError, '"budget_exceeded": true'):
            self.run_probe(lambda key,value:Slow(value),max_wait=2)
        self.assertEqual(len(self.calls),1)

if __name__=="__main__": unittest.main()

