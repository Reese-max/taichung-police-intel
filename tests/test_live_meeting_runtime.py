"""Issue #13: bounded recorded-audio transport and provisional lifecycle regressions."""

from __future__ import annotations

import copy
import base64
from contextlib import contextmanager
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
    reconcile_session, record_gap, resume_session, start_session, stop_session, validate_session,
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


@contextmanager
def temporary_file(suffix):
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as handle:
        path = Path(handle.name)
    try:
        yield path
    finally:
        path.unlink()


class LiveMeetingRuntimeTests(unittest.TestCase):
    def test_mock_provider_nested_words_and_top_level_precedence(self):
        from intel_v2.live_asr import ingest_chunk

        words = [{"word": "交通", "start": 0, "end": 1},
                 {"word": "安全", "start": 1, "end": 2}]
        nested = [{"words": [words[0]]}, {"words": [words[1]]}]
        for name, extra, reason in [
            ("nested", {"segments": nested}, None),
            ("top_level_wins", {"words": words, "segments": [{"words": [{"word": "錯誤"}]}]}, None),
            ("null_top_level_wins", {"words": None, "segments": nested}, "MISSING_WORDS"),
            ("empty_top_level_wins", {"words": [], "segments": nested}, "EMPTY_WORDS"),
            ("invalid_top_level_wins", {"words": "invalid", "segments": nested}, "INVALID_WORDS"),
            ("incomplete_nested_batch", {"segments": [nested[0], {}]}, "MISSING_WORDS"),
            ("nested_mismatch", {"segments": [{"words": [words[0]]}]}, "WORDS_TEXT_MISMATCH"),
            ("nested_out_of_order", {"segments": [nested[1], nested[0]]}, "NON_MONOTONIC_WORDS"),
            ("nested_limit", {"segments": [{"words": [words[0]] * 2049}]}, "TOO_MANY_WORDS"),
        ]:
            with self.subTest(case=name), temporary_file(".wav") as audio, temporary_file(".json") as path:
                with wave.open(str(audio), "wb") as handle:
                    handle.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
                    handle.writeframes(b"\0\0" * 16000 * 2)
                def save(value):
                    validate_session(value)
                    path.write_text(json.dumps(value, ensure_ascii=False, allow_nan=False), encoding="utf-8")
                with patch("intel_v2.live_asr._groq_transcribe", return_value={"text": "交通安全", **extra}) as provider, patch("requests.post") as post:
                    state = ingest_chunk(session(provider="groq"), audio, start_seconds=30,
                                         requested_at="2026-10-16T10:00:30+08:00", opt_in=True,
                                         api_key="test-only", save=save)
                provider.assert_called_once()
                post.assert_not_called()
                self.assertEqual(validate_session(json.loads(path.read_text(encoding="utf-8"))), state)
                self.assertEqual(state["provider_usage"]["calls"][-1]["status"], "SUCCEEDED")
                row = state["segments"][0]
                self.assertEqual(row["word_timestamps_reason"], reason)
                self.assertEqual(row["word_timestamps_status"], "AVAILABLE" if reason is None else "UNAVAILABLE")
                self.assertEqual(row["words"], [{"word": "交通", "start": 30.0, "end": 31.0},
                                               {"word": "安全", "start": 31.0, "end": 32.0}] if reason is None else None)

    def test_worker_requests_segment_and_word_granularities(self):
        from intel_v2.live_asr import _groq_worker
        import contextlib
        import io

        payload = {"text": "交通", "words": [{"word": "交通", "start": 0, "end": 1}]}
        response = Mock(status_code=200)
        response.iter_content.return_value = [json.dumps(payload).encode()]
        request = json.dumps({"audio": base64.b64encode(b"audio").decode(), "api_key": "test-only",
                              "contract": session()["asr_contract"], "timeout": 1})
        output = io.StringIO()
        with patch("sys.stdin", io.StringIO(request)), contextlib.redirect_stdout(output), patch("requests.post", return_value=response) as post:
            _groq_worker()
        self.assertEqual(json.loads(output.getvalue()), payload)
        self.assertEqual(post.call_args.kwargs["data"], [
            ("model", "whisper-large-v3"), ("language", "zh"),
            ("response_format", "verbose_json"), ("temperature", "0"),
            ("timestamp_granularities[]", "segment"), ("timestamp_granularities[]", "word"),
        ])
        response.close.assert_called_once()

    def test_mock_provider_words_are_persisted_atomically_in_session_seconds(self):
        from intel_v2.live_asr import ingest_chunk

        valid = {"word": " 交通 ", "start": 0, "end": 1, "unused": "discard"}
        cases = [
            ("valid", [valid, {"word": "安全", "start": 1.12345, "end": 2}], None),
            ("missing", None, "MISSING_WORDS"),
            ("empty", [], "EMPTY_WORDS"),
            ("not_array", {"word": "交通"}, "INVALID_WORDS"),
            ("not_object", [valid, None], "INVALID_WORDS"),
            ("missing_word", [valid, {"start": 0, "end": 1}], "INVALID_WORD_TEXT"),
            ("blank_word", [valid, {"word": " ", "start": 0, "end": 1}], "INVALID_WORD_TEXT"),
        ]
        for name, first, last, reason in [
            ("negative", -0.00001, 1, "WORD_OUT_OF_BOUNDS"),
            ("overflow", 0, 2.00001, "WORD_OUT_OF_BOUNDS"),
            ("absolute_instead_of_relative", 30, 31, "WORD_OUT_OF_BOUNDS"),
            ("text", "0", 1, "NON_NUMERIC_TIMESTAMP"),
            ("none", None, 1, "NON_NUMERIC_TIMESTAMP"),
            ("boolean", False, 1, "NON_NUMERIC_TIMESTAMP"),
            ("nan", float("nan"), 1, "NON_NUMERIC_TIMESTAMP"),
            ("infinity", 0, float("inf"), "NON_NUMERIC_TIMESTAMP"),
            ("huge_integer", 0, 10 ** 400, "NON_NUMERIC_TIMESTAMP"),
            ("reversed", 1, 0.99999, "INVALID_WORD_INTERVAL"),
        ]:
            cases.append((name, [valid, {"word": "安全", "start": first, "end": last}], reason))
        with temporary_file(".wav") as audio, temporary_file(".json") as path:
            with wave.open(str(audio), "wb") as handle:
                handle.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
                handle.writeframes(b"\0\0" * 16000 * 2)
            for name, words, reason in cases:
                with self.subTest(case=name):
                    payload = {"text": "交通安全", "words": words}
                    if name == "missing":
                        del payload["words"]
                    def save(value):
                        validate_session(value)
                        path.write_text(json.dumps(value, ensure_ascii=False, allow_nan=False), encoding="utf-8")
                    with patch("intel_v2.live_asr._groq_transcribe", return_value=payload) as provider, patch("requests.post") as post:
                        state = ingest_chunk(session(provider="groq"), audio, start_seconds=30,
                                             requested_at="2026-10-16T10:00:30+08:00", opt_in=True,
                                             api_key="test-only", save=save)
                    provider.assert_called_once()
                    post.assert_not_called()
                    restored = validate_session(json.loads(path.read_text(encoding="utf-8")))
                    self.assertEqual(restored, state)
                    self.assertEqual(state["provider_usage"]["calls"][-1]["status"], "SUCCEEDED")
                    segment = state["segments"][0]
                    self.assertEqual((segment["start_seconds"], segment["end_seconds"]), (30, 32))
                    self.assertEqual(segment["word_timestamps_status"], "AVAILABLE" if reason is None else "UNAVAILABLE")
                    self.assertEqual(segment["word_timestamps_reason"], reason)
                    self.assertTrue(segment["provisional"])
                    self.assertEqual(segment["evidence_state"], "LIVE_ASR_PROVISIONAL")
                    self.assertEqual(segment["text"], "交通安全")
                    self.assertEqual(segment["words"], [
                        {"word": "交通", "start": 30.0, "end": 31.0},
                        {"word": "安全", "start": 31.123, "end": 32.0},
                    ] if reason is None else None)

    def test_fixture_revisions_use_only_the_words_for_each_update(self):
        from intel_v2.live_asr import ingest_chunk

        with temporary_file(".wav") as audio:
            with wave.open(str(audio), "wb") as handle:
                handle.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
                handle.writeframes(b"\0\0" * 16000 * 2)
            payload = {"words": [{"word": "不得沿用", "start": 0, "end": 1}], "updates": [
                {"text": "交通", "partial": True, "words": [{"word": "交通", "start": 0, "end": 1}]},
                {"text": "道路", "words": [{"word": "道路", "start": 0.5, "end": 2}]},
                {"text": "道路安全", "finalized": True},
            ]}
            with patch("requests.post") as post:
                state = ingest_chunk(session(), audio, start_seconds=30, requested_at=T0,
                                     opt_in=True, fixture_response=payload, save=Mock())
            post.assert_not_called()
        segment = validate_session(json.loads(json.dumps(state)))["segments"][0]
        self.assertEqual(segment["revision"], 3)
        self.assertIsNone(segment["words"])
        self.assertEqual(segment["word_timestamps_status"], "UNAVAILABLE")
        self.assertEqual(segment["word_timestamps_reason"], "MISSING_WORDS")
        self.assertEqual([row["words"] for row in segment["revision_history"]], [
            [{"word": "交通", "start": 30.0, "end": 31.0}],
            [{"word": "道路", "start": 30.5, "end": 32.0}],
        ])

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

    def _crashed_reservation(self, audio, budget):
        from intel_v2.live_asr import ingest_chunk

        reserved = []
        def crash(state):
            reserved.append(copy.deepcopy(state))
            raise KeyboardInterrupt("crash after the reservation reaches disk")
        with self.assertRaises(KeyboardInterrupt):
            ingest_chunk(session(budget=budget), audio, start_seconds=0,
                         requested_at=T0, opt_in=True,
                         fixture_response={"text": "交通"}, save=crash)
        return json.loads(json.dumps(reserved[-1]))

    def test_crashed_reservation_is_persisted_before_budget_stop(self):
        from intel_v2.live_asr import ingest_chunk

        with tempfile.TemporaryDirectory() as directory:
            audio = Path(directory) / "chunk.wav"
            with wave.open(str(audio), "wb") as handle:
                handle.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
                handle.writeframes(b"\0\0" * 16000 * 30)
            for budget, at in [(30, "2026-10-16T10:00:30+08:00"),
                               (1800, "2026-10-16T10:30:01+08:00")]:
                with self.subTest(budget=budget):
                    restored = self._crashed_reservation(audio, budget)
                    before = copy.deepcopy(restored)
                    saved = []
                    with patch("intel_v2.live_asr._groq_transcribe") as provider:
                        result = ingest_chunk(restored, audio, start_seconds=30,
                                              requested_at=at, opt_in=True,
                                              fixture_response={"text": "later"},
                                              save=lambda value: saved.append(copy.deepcopy(value)))
                        provider.assert_not_called()
                    self.assertEqual(restored, before, "recovery must not mutate caller state")
                    self.assertGreaterEqual(len(saved), 2, "recovery must reach disk before budget stop")
                    for value in saved:
                        validate_session(value)
                        self.assertEqual(value["provider_usage"]["calls"][0]["status"], "FAILED")
                        self.assertEqual(value["provider_usage"]["reserved_seconds"], 30)
                        self.assertEqual(value["gap_intervals"][0]["reason"], "SESSION_CRASH")
                    self.assertEqual(result["transport_status"], "BUDGET_STOP")
                    self.assertEqual([(gap["start_seconds"], gap["end_seconds"], gap["reason"])
                                      for gap in result["gap_intervals"]],
                                     [(0, 30, "SESSION_CRASH"), (30, 1800, "BUDGET_STOP")])

    def test_legacy_pending_budget_stop_is_repaired_without_reopening_transport(self):
        from intel_v2.live_asr import ingest_chunk

        with tempfile.TemporaryDirectory() as directory:
            audio = Path(directory) / "chunk.wav"
            with wave.open(str(audio), "wb") as handle:
                handle.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
                handle.writeframes(b"\0\0" * 16000 * 30)
            stopped = budget_stop(self._crashed_reservation(audio, 30),
                                  stopped_at="2026-10-16T10:00:30+08:00")
            saved = []
            result = ingest_chunk(stopped, Path("missing.wav"), start_seconds=30,
                                  requested_at="2026-10-16T10:00:31+08:00", opt_in=True,
                                  fixture_response={"text": "later"},
                                  save=lambda value: saved.append(copy.deepcopy(value)))
            self.assertEqual(len(saved), 1)
            validate_session(result)
            self.assertEqual(result["transport_status"], "BUDGET_STOP")
            self.assertEqual(result["provider_usage"]["calls"][0]["status"], "FAILED")
            self.assertEqual([(gap["start_seconds"], gap["end_seconds"], gap["reason"])
                              for gap in result["gap_intervals"]],
                             [(0, 30, "SESSION_CRASH"), (30, 1800, "BUDGET_STOP")])
            self.assertNotEqual(result["gap_intervals"][1]["gap_id"], stopped["gap_intervals"][0]["gap_id"])
            with self.assertRaisesRegex(ValueError, "budget"):
                resume_session(result, resumed_at="2026-10-16T10:00:32+08:00")
            repeated_save = Mock()
            self.assertEqual(ingest_chunk(result, Path("missing.wav"), start_seconds=30,
                                         requested_at="2026-10-16T10:00:33+08:00", opt_in=True,
                                         fixture_response={"text": "later"}, save=repeated_save), result)
            repeated_save.assert_not_called()

    def test_stopped_crash_gap_recovers_pending_call_without_a_duplicate_gap(self):
        from intel_v2.live_asr import ingest_chunk

        with tempfile.TemporaryDirectory() as directory:
            audio = Path(directory) / "chunk.wav"
            with wave.open(str(audio), "wb") as handle:
                handle.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
                handle.writeframes(b"\0\0" * 16000 * 30)
            restored = self._crashed_reservation(audio, 60)
            # A prior recovery already persisted the SESSION_CRASH gap while
            # the on-disk reservation still reads PENDING.
            restored = record_gap(restored, start_seconds=0, end_seconds=30,
                                  reason="SESSION_CRASH", detected_at="2026-10-16T10:00:05+08:00")
            restored = budget_stop(restored, stopped_at="2026-10-16T10:00:05+08:00")
            saved = []
            result = ingest_chunk(restored, Path("missing.wav"), start_seconds=30,
                                  requested_at="2026-10-16T10:00:06+08:00", opt_in=True,
                                  fixture_response={"text": "later"},
                                  save=lambda value: saved.append(copy.deepcopy(value)))
            self.assertEqual(len(saved), 1)
            validate_session(result)
            self.assertEqual(result, saved[0])
            self.assertEqual(result["transport_status"], "BUDGET_STOP")
            self.assertEqual(result["provider_usage"]["calls"][0]["status"], "FAILED")
            self.assertEqual(result["provider_usage"]["reserved_seconds"], 30)
            self.assertEqual([(gap["start_seconds"], gap["end_seconds"], gap["reason"])
                              for gap in result["gap_intervals"]],
                             [(0, 30, "SESSION_CRASH"), (30, 1800, "BUDGET_STOP")])
            with self.assertRaisesRegex(ValueError, "budget"):
                resume_session(result, resumed_at="2026-10-16T10:00:07+08:00")
            repeated_save = Mock()
            self.assertEqual(ingest_chunk(result, Path("missing.wav"), start_seconds=30,
                                          requested_at="2026-10-16T10:00:08+08:00", opt_in=True,
                                          fixture_response={"text": "later"}, save=repeated_save), result)
            repeated_save.assert_not_called()

    def test_backfill_rejection_first_persists_the_crash_recovery(self):
        from intel_v2.live_asr import ingest_chunk

        with tempfile.TemporaryDirectory() as directory:
            audio = Path(directory) / "chunk.wav"
            with wave.open(str(audio), "wb") as handle:
                handle.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
                handle.writeframes(b"\0\0" * 16000 * 30)
            restored = self._crashed_reservation(audio, 1800)
            saved = []
            with patch("intel_v2.live_asr._groq_transcribe") as provider:
                with self.assertRaisesRegex(ValueError, "overlap|backfill"):
                    ingest_chunk(restored, audio, start_seconds=0,
                                 requested_at="2026-10-16T10:00:05+08:00", opt_in=True,
                                 fixture_response={"text": "retry"},
                                 save=lambda value: saved.append(copy.deepcopy(value)))
                provider.assert_not_called()
            self.assertEqual(len(saved), 1)
            validate_session(saved[0])
            self.assertEqual(saved[0]["provider_usage"]["calls"][0]["status"], "FAILED")
            self.assertEqual(saved[0]["provider_usage"]["reserved_seconds"], 30)
            self.assertEqual([(gap["start_seconds"], gap["end_seconds"], gap["reason"])
                              for gap in saved[0]["gap_intervals"]], [(0, 30, "SESSION_CRASH")])
            # Repeating a rejected request must not duplicate the recovery record.
            with self.assertRaisesRegex(ValueError, "overlap|backfill"):
                ingest_chunk(saved[0], audio, start_seconds=0,
                             requested_at="2026-10-16T10:00:06+08:00", opt_in=True,
                             fixture_response={"text": "retry"}, save=Mock())

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
