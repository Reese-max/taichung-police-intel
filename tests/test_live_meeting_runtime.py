"""Issue #13: bounded recorded-audio transport and provisional lifecycle regressions."""

from __future__ import annotations

import copy
import base64
import importlib.util
import json
from pathlib import Path
import tempfile
import subprocess
import sys
import time
import unittest
from unittest.mock import Mock, patch
import wave

from intel_v2.live_meeting import (
    add_bookmark, append_segment, budget_stop, formal_candidates,
    reconcile_session, resume_session, start_session, stop_session, validate_session,
)
from intel_v2.role_profiles import load_catalog

ROOT = Path(__file__).resolve().parents[1]
T0 = "2026-10-16T10:00:00+08:00"
PROFILE = load_catalog(ROOT / "docs/govintel/role-profiles.v1.json")["profiles"]["traffic-policy"]


def session(budget=60, provider="fixture"):
    return start_session(
        source_id="S-010", official_source_url="https://www.tccc.gov.tw/",
        stream_url="https://vod.tccc.gov.tw/live/recorded-test.m3u8",
        started_at=T0, max_duration_seconds=1800, budget_seconds=budget,
        transport_enabled=True, provider_authorized=True,
        asr_contract={"provider": provider, "model": "whisper-large-v3", "version": "v1", "language": "zh"},
    )


class LiveMeetingRuntimeTests(unittest.TestCase):
    def test_budget_stop_cannot_be_resumed_or_append_more_text(self):
        state = budget_stop(session(), stopped_at=T0)
        with self.assertRaisesRegex(ValueError, "budget"):
            resume_session(state, resumed_at=T0)
        with self.assertRaisesRegex(ValueError, "budget"):
            append_segment(state, sequence=0, start_seconds=0, end_seconds=1, text="交通", received_at=T0)

    def test_segment_cannot_exceed_authorized_budget(self):
        with self.assertRaisesRegex(ValueError, "budget"):
            append_segment(session(budget=1), sequence=0, start_seconds=0, end_seconds=60, text="交通", received_at=T0)

    def test_bookmark_survives_a_profile_and_speaker_revision(self):
        state = append_segment(session(), sequence=0, start_seconds=0, end_seconds=30,
                               text="交通預算", received_at=T0, profile=PROFILE, speaker_label="SPEAKER_1")
        state = add_bookmark(state, segment_ids=[state["segments"][0]["segment_id"]],
                             reason_code="TOPIC_MATCH", bookmarked_at=T0)
        original_bookmark = copy.deepcopy(state["bookmarks"][0])
        state = append_segment(state, sequence=0, start_seconds=0, end_seconds=30,
                               text="道路安全預算尚待審查", received_at=T0, finalized=True,
                               profile=PROFILE, speaker_label="SPEAKER_2")
        validate_session(state)
        self.assertEqual(state["bookmarks"][0], original_bookmark)
        self.assertEqual(state["segments"][0]["evidence_state"], "LIVE_ASR_PROVISIONAL")
        self.assertEqual(state["segments"][0]["finalized_at"], T0)
        self.assertEqual(state["segments"][0]["revision_history"][0]["speaker_label"], "SPEAKER_1")
        self.assertEqual(formal_candidates(state), [])

    def test_minutes_contradiction_keeps_the_official_after_hash_and_locator(self):
        state = append_segment(session(), sequence=0, start_seconds=0, end_seconds=30,
                               text="預算已通過", received_at=T0, finalized=True)
        state = stop_session(state, ended_at="2026-10-16T10:01:00+08:00")
        state = reconcile_session(state, [{
            "segment_ids": [state["segments"][0]["segment_id"]],
            "outcome": "SUPERSEDED_TRANSCRIPT", "official_source_type": "MINUTES",
            "official_source_id": "S-007", "official_url": "https://yishi.tccc.gov.tw/minutes/fixture",
            "official_locator": "minutes:item-3", "official_document_version": "minutes-v2",
            "official_text_sha256": "b" * 64,
        }], reconciled_at="2026-10-17T10:00:00+08:00")
        receipt = state["reconciliation_receipts"][0]
        self.assertIsNotNone(receipt["official"])
        self.assertEqual(receipt["official"]["text_sha256"], "b" * 64)
        self.assertEqual(receipt["official"]["locator"], "minutes:item-3")
        self.assertEqual(receipt["official"]["source_type"], "MINUTES")
        self.assertEqual(receipt["verification_status"], "PROVISIONAL_UNRESOLVED")
        self.assertEqual(receipt["reconciler"]["parser_version"], "live-meeting-reconcile-v1")
        validate_session(state)
        self.assertEqual(formal_candidates(state), [])

    def test_disabled_or_unconfigured_transport_makes_zero_provider_calls(self):
        from intel_v2.live_asr import ingest_chunk

        for opt_in, key in [(False, "test-only"), (True, None)]:
            with self.subTest(opt_in=opt_in), patch("requests.post") as post:
                state = session(provider="groq")
                with self.assertRaisesRegex(ValueError, "opt-in|key"):
                    ingest_chunk(state, Path("does-not-exist.wav"), start_seconds=0,
                                 requested_at=T0, opt_in=opt_in, api_key=key, save=Mock())
                post.assert_not_called()
                self.assertNotIn("provider_usage", state)

    def test_real_adapter_reserves_before_transport_and_counts_failed_attempts(self):
        from intel_v2.live_asr import ingest_chunk
        import requests

        with tempfile.TemporaryDirectory() as directory:
            audio = Path(directory) / "chunk.wav"
            with wave.open(str(audio), "wb") as handle:
                handle.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
                handle.writeframes(b"\0\0" * 16000 * 30)
            saved = []
            def save(state):
                validate_session(state)
                saved.append(copy.deepcopy(state))
            def timeout(*args, **kwargs):
                self.assertEqual(saved[-1]["provider_usage"]["reserved_seconds"], 30)
                self.assertEqual(saved[-1]["provider_usage"]["calls"][0]["status"], "PENDING")
                raise TimeoutError("synthetic timeout; never persisted")
            with patch("intel_v2.live_asr._groq_transcribe", side_effect=timeout) as post:
                state = ingest_chunk(session(budget=30, provider="groq"), audio,
                                     start_seconds=0, requested_at=T0, opt_in=True,
                                     api_key="test-only", save=save)
                self.assertEqual(post.call_count, 1)
                self.assertEqual(state["status"], "DEGRADED")
                self.assertEqual(state["gap_intervals"][0]["reason"], "ASR_OUTAGE")
                self.assertEqual(state["provider_usage"]["reserved_seconds"], 30)
                self.assertNotIn("test-only", json.dumps(state))
                restored = json.loads(json.dumps(state))
                ingest_chunk(restored, audio, start_seconds=30,
                             requested_at="2026-10-16T10:00:30+08:00", opt_in=True,
                             api_key="test-only", save=save)
                self.assertEqual(post.call_count, 1, "a restart must not reset the budget")

    def test_crash_reservation_becomes_a_gap_before_explicit_reconnect(self):
        from intel_v2.live_asr import ingest_chunk

        with tempfile.TemporaryDirectory() as directory:
            audio = Path(directory) / "chunk.wav"
            with wave.open(str(audio), "wb") as handle:
                handle.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
                handle.writeframes(b"\0\0" * 16000 * 30)
            saved = []
            def crash(state):
                saved.append(copy.deepcopy(state))
                raise KeyboardInterrupt("simulate a process crash after durable reservation")
            with self.assertRaises(KeyboardInterrupt):
                ingest_chunk(session(), audio, start_seconds=0, requested_at=T0,
                             opt_in=True, fixture_response={"text": "交通"}, save=crash)
            restored = json.loads(json.dumps(saved[-1]))
            with patch("requests.post") as post:
                state = ingest_chunk(restored, audio, start_seconds=30,
                                     requested_at="2026-10-16T10:00:30+08:00", opt_in=True,
                                     fixture_response={"text": "交通重新連線"}, save=Mock())
                post.assert_not_called()
            validate_session(state)
            self.assertEqual([row["status"] for row in state["provider_usage"]["calls"]], ["FAILED", "SUCCEEDED"])
            self.assertEqual(state["provider_usage"]["reserved_seconds"], 60)
            self.assertEqual([(gap["start_seconds"], gap["end_seconds"], gap["reason"])
                              for gap in state["gap_intervals"]], [(0, 30, "SESSION_CRASH")])

    def test_budget_depletion_persists_session_crash_gap(self):
        """When budget is depleted, SESSION_CRASH gap should be persisted for PENDING calls."""
        from intel_v2.live_asr import ingest_chunk

        audio_out = Path("/tmp/test_chunk.wav")
        import wave
        with wave.open(str(audio_out), "wb") as handle:
            handle.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
            handle.writeframes(b"\0\0" * 16000 * 30)

        saved = []

        def crash(state):
            saved.append(copy.deepcopy(state))
            raise KeyboardInterrupt("simulate a process crash after durable reservation")

        try:
            ingest_chunk(session(budget=30), audio_out, start_seconds=0, requested_at=T0,
                         opt_in=True, fixture_response={"text": "交通"}, save=crash)
        except KeyboardInterrupt:
            pass

        with patch("requests.post") as post:
            state = json.loads(json.dumps(saved[-1]))
            # Ingest more audio - should record SESSION_CRASH gap before budget stop
            state = ingest_chunk(state, audio_out, start_seconds=35,
                                 requested_at=T0,
                                 opt_in=True,
                                 fixture_response={"text": "超預算"},
                                 save=Mock())
            gap_reasons = [gap["reason"] for gap in state.get("gap_intervals", [])]
            self.assertIn("SESSION_CRASH", gap_reasons,
                          "BUG: SESSION_CRASH gap not recorded after budget depletion!")
            self.assertIn("BUDGET_STOP", gap_reasons)
            self.assertEqual(state["status"], "DEGRADED")

    def test_fixture_cannot_enter_a_paid_session_and_expired_clock_blocks_transport(self):
        from intel_v2.live_asr import ingest_chunk

        with patch("requests.post") as post:
            with self.assertRaisesRegex(ValueError, "fixture"):
                ingest_chunk(session(provider="groq"), Path("missing.wav"), start_seconds=0,
                             requested_at=T0, opt_in=True, fixture_response={"text": "synthetic"}, save=Mock())
            state = ingest_chunk(session(provider="groq"), Path("missing.wav"), start_seconds=60,
                                 requested_at="2026-10-16T10:01:00+08:00", opt_in=True,
                                 api_key="test-only", save=Mock())
            post.assert_not_called()
            self.assertEqual(state["transport_status"], "BUDGET_STOP")

    def test_usage_ledger_rejects_inconsistent_total_even_with_a_recomputed_session_hash(self):
        from intel_v2.live_meeting import _touch
        state = session()
        state["provider_usage"] = {"adapter_version": "recorded-chunk-v1", "mode": "FIXTURE_ONLY",
                                   "reserved_seconds": -1, "calls": [], "estimated_cost_usd": None}
        with self.assertRaisesRegex(ValueError, "usage"):
            validate_session(_touch(state, T0))

    def test_thirty_minute_recorded_audio_replay_runs_the_actual_cli(self):
        with tempfile.TemporaryDirectory() as directory:
            result = subprocess.run([
                sys.executable, "-X", "utf8", str(ROOT / "scripts/live-meeting.py"),
                "replay-recording", "--fixture", str(ROOT / "tests/fixtures/live-meeting/recorded-stream.json"),
                "--out", directory,
            ], capture_output=True, text=True, encoding="utf-8", timeout=60, cwd=ROOT)
            self.assertEqual(result.returncode, 0, result.stderr)
            receipt = json.loads(result.stdout)
            self.assertEqual(receipt["audio_duration_seconds"], 1800)
            self.assertEqual(receipt["network_calls"], 0)
            self.assertEqual(receipt["timeline_coverage_seconds"], 1800)
            self.assertEqual(receipt["gap_seconds"], 150)
            self.assertEqual(receipt["fixture_calls"], 57)
            self.assertEqual(receipt["reserved_seconds"], 1710)
            self.assertEqual(receipt["after_deadline_calls"], 0)
            self.assertTrue(receipt["interim_final_revision"])
            self.assertTrue(receipt["crash_recovered"])
            state = validate_session(json.loads((Path(directory) / "session-reconciled.json").read_text(encoding="utf-8")))
            self.assertEqual([item["outcome"] for item in state["reconciliation_receipts"]], [
                "SOURCE_NOT_YET_AVAILABLE", "CONFIRMED_BY_OFFICIAL_MEDIA", "SUPERSEDED_TRANSCRIPT", "UNRESOLVED",
            ])
            self.assertEqual(formal_candidates(state), [], "synthetic audio never exports official evidence")

    def test_cli_serializes_the_entire_state_transaction_with_a_crash_safe_lock(self):
        spec = importlib.util.spec_from_file_location("live_cli_issue13", ROOT / "scripts/live-meeting.py")
        cli = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cli)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "session.json"
            with cli.session_lock(path):
                with self.assertRaisesRegex(ValueError, "in use"):
                    with cli.session_lock(path):
                        self.fail("concurrent state transaction was admitted")
            with cli.session_lock(path):
                pass

    def test_provider_worker_has_a_real_total_deadline_and_keeps_key_off_command_line(self):
        from intel_v2.live_asr import _groq_transcribe

        started = time.monotonic()
        with patch("intel_v2.live_asr.GROQ_WORKER_CODE", "import time; time.sleep(30)"):
            with self.assertRaises(TimeoutError):
                _groq_transcribe(b"audio", "test-only", session()["asr_contract"], 0.3)
        self.assertLess(time.monotonic() - started, 5)

    def test_worker_caps_response_bytes_and_never_exposes_error_bodies(self):
        from intel_v2.live_asr import _groq_worker
        import contextlib
        import io

        response = Mock(status_code=200)
        response.iter_content.return_value = [b"x" * 16384] * 65
        request = json.dumps({"audio": base64.b64encode(b"audio").decode(), "api_key": "test-only",
                              "contract": session()["asr_contract"], "timeout": 1})
        output = io.StringIO()
        with patch("sys.stdin", io.StringIO(request)), contextlib.redirect_stdout(output), patch("requests.post", return_value=response) as post:
            _groq_worker()
        self.assertEqual(json.loads(output.getvalue()), {"error_code": "ASR_OUTAGE"})
        self.assertFalse(post.call_args.kwargs["allow_redirects"])
        self.assertTrue(post.call_args.kwargs["stream"])
        response.close.assert_called_once()

    def test_worker_enforces_its_deadline_without_a_live_parent(self):
        worker = (
            "from unittest.mock import patch; import time; "
            "from intel_v2.live_asr import _groq_worker; "
            "patch('requests.post', side_effect=lambda *a, **k: time.sleep(30)).start(); "
            "_groq_worker()"
        )
        request = json.dumps({"audio": base64.b64encode(b"audio").decode(), "api_key": "test-only",
                              "contract": session()["asr_contract"], "timeout": 0.2})
        result = subprocess.run([sys.executable, "-X", "utf8", "-c", worker], input=request,
                                capture_output=True, text=True, cwd=ROOT, timeout=3)
        self.assertEqual(result.returncode, 124)


if __name__ == "__main__":
    unittest.main()
