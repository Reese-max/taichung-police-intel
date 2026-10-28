from __future__ import annotations

import base64
import hashlib
import importlib.util
import json
import re
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

import online_collect
from scripts import schema_drift as drift

ROOT = Path(__file__).resolve().parents[1]
RELAY_DIR = ROOT / "scripts/source-relay"
spec = importlib.util.spec_from_file_location("check_relay", RELAY_DIR / "check_relay.py")
check_relay = importlib.util.module_from_spec(spec)
spec.loader.exec_module(check_relay)


def capture(body: bytes = b"<html>list</html>", **overrides):
    payload = {
        "schema": "govintel-source-relay/v1",
        "source_id": "S-001",
        "requested_url": online_collect.NEWS_LIST_SOURCES["S-001"]["list_url"],
        "final_url": online_collect.NEWS_LIST_SOURCES["S-001"]["list_url"],
        "http_status": 200,
        "content_type": "text/html;charset=UTF-8",
        "observed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "body_base64": base64.b64encode(body).decode("ascii"),
        "body_sha256": hashlib.sha256(body).hexdigest(),
        "captured_by": "taiwan-relay",
    }
    payload.update(overrides)
    return payload


class SourceRelayHostTests(unittest.TestCase):
    def test_the_relay_host_check_matches_the_catalog_and_the_pages_reader(self):
        self.assertEqual(check_relay.SCHEMA, drift.RELAY_SCHEMA)
        self.assertEqual(set(check_relay.URLS), set(drift.RELAY_SOURCES))
        for source_id, urls in check_relay.URLS.items():
            self.assertEqual(urls, drift._relay_urls(source_id))
        self.assertLessEqual(check_relay.MAX_AGE_SECONDS, drift.RELAY_MAX_AGE_SECONDS)
        self.assertEqual(check_relay.MAX_BODY_BYTES, drift.RELAY_MAX_BODY_BYTES)

    def test_a_fresh_capture_passes_both_checks(self):
        payload = capture()
        self.assertIsNone(check_relay.problem(payload))
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "relay.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            self.assertEqual(check_relay.main([str(path)]), 0)
            self.assertEqual(drift.load_relay(path)["S-001"]["observation_origin"], "TAIWAN_RELAY")

    def test_the_relay_host_refuses_what_it_must_not_publish(self):
        now = datetime.now(timezone.utc)
        cases = {
            "fields": capture(extra="x"),
            "schema": capture(source_id="S-007"),
            "http_status": capture(http_status=500),
            "types": capture(captured_by=1),
            "types ": capture(source_id=["S-001"]),
            "url": capture(final_url="https://example.test/"),
            "observed_at": capture(observed_at="2026-10-28T06:00:00"),
            "age": capture(observed_at=(now - timedelta(minutes=30)).isoformat()),
            "body": capture(body_base64="***"),
            "hash": capture(body_sha256="0" * 64),
        }
        for reason, payload in cases.items():
            with self.subTest(reason=reason):
                self.assertEqual(check_relay.problem(payload, now=now), reason.strip())

    def test_an_oversized_capture_is_refused_before_it_is_read(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "relay.json"
            path.write_bytes(b" " * (check_relay.MAX_FILE_BYTES + 1))
            self.assertEqual(check_relay.main([str(path)]), 1)
        self.assertEqual(check_relay.MAX_FILE_BYTES, drift.RELAY_MAX_FILE_BYTES)
        # the relay host and the Pages download cut a capture off at the same size
        script = (RELAY_DIR / "relay-s001.sh").read_text(encoding="utf-8")
        workflow = (ROOT / ".github/workflows/pages.yml").read_text(encoding="utf-8")
        cut = int(re.search(r"head -c (\d+)", script).group(1))
        limit = int(re.search(r"--max-filesize (\d+)", workflow).group(1))
        self.assertGreaterEqual(limit, check_relay.MAX_FILE_BYTES)
        self.assertEqual(cut, limit + 1)

    def test_the_capture_never_falls_back_on_a_tls_failure(self):
        source = (RELAY_DIR / "capture-s001.ps1").read_text(encoding="ascii")
        transient = re.search(r"\$TransientExit = @\(([^)]*)\)", source).group(1)
        codes = {int(code) for code in transient.split(",")}
        self.assertEqual(codes & {35, 51, 53, 54, 58, 59, 60, 64, 66, 77, 80, 82, 83, 90, 91}, set())
        self.assertNotIn("2>$null", source)

    def test_the_capture_bounds_its_download_and_its_read(self):
        source = (RELAY_DIR / "capture-s001.ps1").read_text(encoding="ascii")
        limit = int(re.search(r"\$MaxBytes = (\d+)", source).group(1))
        self.assertEqual(limit, drift.RELAY_MAX_BODY_BYTES)
        self.assertIn("--max-filesize $MaxBytes", source)
        self.assertLess(source.index("$Length -gt $MaxBytes"), source.index("ReadAllBytes"))

    def test_the_capture_script_fetches_exactly_the_catalog_urls_and_stays_ascii(self):
        text = (RELAY_DIR / "capture-s001.ps1").read_bytes()
        self.assertFalse(re.search(rb"[^\x00-\x7f]", text))
        source = text.decode("ascii")
        urls = set(re.findall(r"'(https://[^']+)'", source))
        self.assertEqual(urls, drift._relay_urls("S-001"))
        self.assertIn("'govintel-source-relay/v1'", source)
        self.assertIn("--max-redirs 0", source)

    def test_the_relay_host_script_checks_before_it_publishes(self):
        text = (RELAY_DIR / "relay-s001.sh").read_text(encoding="utf-8")
        self.assertIn("set -euo pipefail", text)
        self.assertLess(text.index("check_relay.py"), text.index("git -C \"$REPO\" add"))
        self.assertIn("StrictHostKeyChecking=yes", text)
        self.assertNotIn("--force", text)


if __name__ == "__main__":
    unittest.main()
