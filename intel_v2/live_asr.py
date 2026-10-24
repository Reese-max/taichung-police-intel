"""Opt-in, bounded chunks of operator-authorized public recordings.

No stream discovery, remote URL fetching, automatic retries, or publication.
The same Groq contract as groq-asr-canary.py is used for paid invocations;
fixture responses require a separate, visibly synthetic session contract.
"""

from __future__ import annotations

import copy
import base64
from datetime import datetime, timedelta
import hashlib
import io
import json
import os
from pathlib import Path
import time
import tempfile
import subprocess
import sys
import threading
from typing import Callable
import wave

from intel_v2.live_meeting import (
    _number, _stamp, _touch, add_bookmark, append_segment, budget_stop, build_timeline,
    record_gap, reconcile_session, resume_session, start_session, stop_session, validate_session,
)
from intel_v2.role_profiles import load_catalog
from intel_v2.semantics import canonical_sha256

ADAPTER_VERSION = "recorded-chunk-v1"
MAX_CHUNK_SECONDS = 60
GROQ_WORKER_CODE = "from intel_v2.live_asr import _groq_worker; _groq_worker()"


def _groq_worker() -> None:
    """Child-process transport: fixed URL, bounded body, secret-free errors."""
    import requests

    response = None
    deadline = None
    result = {"error_code": "ASR_OUTAGE"}
    try:
        request = json.load(sys.stdin)
        timeout = float(request["timeout"])
        if not 0 < timeout <= MAX_CHUNK_SECONDS:
            raise ValueError("invalid transport deadline")
        # The worker must also expire if its parent crashes during transport.
        deadline = threading.Timer(timeout, lambda: os._exit(124))
        deadline.daemon = True
        deadline.start()
        contract = request["contract"]
        response = requests.post(
            "https://api.groq.com/openai/v1/audio/transcriptions",
            headers={"Authorization": f"Bearer {request['api_key']}"},
            files={"file": ("chunk.wav", base64.b64decode(request["audio"]), "audio/wav")},
            data={"model": contract["model"], "language": contract["language"],
                  "response_format": "verbose_json", "temperature": "0"},
            timeout=(min(5, request["timeout"]), request["timeout"]),
            allow_redirects=False, stream=True,
        )
        if response.status_code == 200:
            body = bytearray()
            for chunk in response.iter_content(chunk_size=16384):
                body.extend(chunk)
                if len(body) > 1_048_576:
                    raise ValueError("response exceeds the byte limit")
            result = json.loads(body)
        elif response.status_code == 429:
            result = {"error_code": "ASR_RATE_LIMIT"}
    except (requests.RequestException, ValueError, KeyError, TypeError):
        pass
    finally:
        if deadline is not None:
            deadline.cancel()
        if response is not None:
            response.close()
    print(json.dumps(result, ensure_ascii=False))


def _groq_transcribe(audio_bytes: bytes, api_key: str, contract: dict, timeout: float) -> dict:
    # requests read timeouts are not total deadlines. Kill the isolated HTTP
    # process at the session deadline, including a continuously trickling body.
    try:
        response = subprocess.run(
            [sys.executable, "-X", "utf8", "-c", GROQ_WORKER_CODE],
            input=json.dumps({"audio": base64.b64encode(audio_bytes).decode("ascii"),
                              "api_key": api_key, "contract": contract, "timeout": timeout}),
            text=True, encoding="utf-8", stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            timeout=timeout, cwd=Path(__file__).resolve().parents[1],
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        )
    except subprocess.TimeoutExpired:
        raise TimeoutError("bounded ASR request expired") from None
    if response.returncode != 0:
        raise ValueError("ASR transport failed")
    return json.loads(response.stdout)


def ingest_chunk(
    state: dict, audio: Path, *, start_seconds: float, requested_at: str,
    opt_in: bool = False, api_key: str | None = None,
    fixture_response: dict | None = None, profile: dict | None = None,
    save: Callable[[dict], None],
) -> dict:
    """Reserve before the single provider call; failures still consume budget.

    ``save`` must atomically persist the session. A crash after reservation is
    an uncertain, charged attempt: the next invocation records its gap and
    never retries it. Local PCM WAV avoids hidden HLS redirects or downloads.
    """
    operation_started = time.monotonic()
    validate_session(state)
    if opt_in is not True:
        raise ValueError("recorded public audio requires explicit opt-in")
    fixture = fixture_response is not None
    contract = state["asr_contract"]
    if fixture:
        if contract["provider"] != "fixture":
            raise ValueError("fixture responses require a fixture provider session")
    else:
        if not isinstance(api_key, str) or not api_key.strip():
            raise ValueError("Groq key is required before transport")
        if contract["provider"].lower() != "groq" or contract["model"] != "whisper-large-v3":
            raise ValueError("only the configured Groq whisper-large-v3 contract is allowed")
    if state["status"] not in {"LIVE", "DEGRADED", "FAILED"}:
        raise ValueError("start-live-session must explicitly activate transport first")
    if state["budget_seconds"] is None:
        raise ValueError("an explicit duration budget is required before transport")
    at = _stamp(requested_at)
    elapsed = (datetime.fromisoformat(at) - datetime.fromisoformat(state["started_at"])).total_seconds()
    if elapsed < 0:
        raise ValueError("request cannot precede session start")
    result = copy.deepcopy(state)
    usage = result.setdefault("provider_usage", {
        "adapter_version": ADAPTER_VERSION,
        "mode": "FIXTURE_ONLY" if fixture else "GROQ_RECORDED_AUDIO",
        "reserved_seconds": 0.0, "calls": [], "estimated_cost_usd": None,
    })
    if usage["mode"] != ("FIXTURE_ONLY" if fixture else "GROQ_RECORDED_AUDIO"):
        raise ValueError("fixture and paid usage cannot share a session")
    if state["transport_status"] == "BUDGET_STOP":
        # Record SESSION_CRASH gaps for any PENDING calls before budget stop
        for previous in usage["calls"]:
            if previous["status"] == "PENDING":
                if result["status"] == "FAILED":
                    result = resume_session(_touch(result, at), resumed_at=at)
                result = record_gap(_touch(result, at), start_seconds=previous["start_seconds"],
                                    end_seconds=previous["end_seconds"], reason="SESSION_CRASH", detected_at=at)
                previous["status"] = "FAILED"
        return result
    if elapsed >= state["budget_seconds"] or usage["reserved_seconds"] >= state["budget_seconds"]:
        # Record SESSION_CRASH gaps for any PENDING calls before budget exhaustion
        for previous in usage["calls"]:
            if previous["status"] == "PENDING":
                if result["status"] == "FAILED":
                    result = resume_session(_touch(result, at), resumed_at=at)
                result = record_gap(_touch(result, at), start_seconds=previous["start_seconds"],
                                    end_seconds=previous["end_seconds"], reason="SESSION_CRASH", detected_at=at)
                previous["status"] = "FAILED"
        if result["status"] == "FAILED":
            result = resume_session(_touch(result, at), resumed_at=at)
        result = budget_stop(_touch(result, at), stopped_at=at)
        save(result)
        return result

    # Validate the actual recording, not a caller-provided billable duration.
    if not isinstance(audio, Path) or audio.stat().st_size > 2_000_000:
        raise ValueError("audio must be a local WAV chunk under 2 MB")
    audio_bytes = audio.read_bytes()
    with wave.open(io.BytesIO(audio_bytes), "rb") as handle:
        if (handle.getnchannels(), handle.getsampwidth(), handle.getframerate()) != (1, 2, 16000):
            raise ValueError("audio must be mono PCM16 WAV at 16000 Hz")
        frames = handle.getnframes()
        duration = frames / handle.getframerate()
        if not 1 <= duration <= MAX_CHUNK_SECONDS or len(handle.readframes(frames)) != frames * 2:
            raise ValueError("audio is truncated or exceeds the 60-second chunk bound")
    start = _number(start_seconds, "start_seconds")
    end = round(start + duration, 3)
    if end > state["budget_seconds"] or usage["reserved_seconds"] + duration > state["budget_seconds"]:
        raise ValueError("chunk exceeds the remaining authorized budget")
    # Do this before any network call; transcript and gap ranges never overlap.
    for row in [*result["segments"], *result["gap_intervals"]]:
        if start < row["end_seconds"] and end > row["start_seconds"]:
            raise ValueError("chunk overlaps an existing segment or gap")
    for previous in usage["calls"]:
        if previous["status"] == "PENDING":
            if result["status"] == "FAILED":
                result = resume_session(result, resumed_at=at)
            result = record_gap(_touch(result, at), start_seconds=previous["start_seconds"],
                                end_seconds=previous["end_seconds"], reason="SESSION_CRASH", detected_at=at)
            previous["status"] = "FAILED"
    if result["status"] == "FAILED":
        result = resume_session(_touch(result, at), resumed_at=at)
    last_end = max([row["end_seconds"] for row in [*result["segments"], *result["gap_intervals"]]] + [0])
    if start < last_end:
        raise ValueError("chunk cannot backfill a prior attempt")
    if start > last_end:
        result = record_gap(_touch(result, at), start_seconds=last_end, end_seconds=start,
                            reason="STREAM_DISCONNECT", detected_at=at)
    usage = copy.deepcopy(usage)
    call = {
        "call_id": len(usage["calls"]) + 1, "start_seconds": start, "end_seconds": end,
        "audio_sha256": hashlib.sha256(audio_bytes).hexdigest(), "requested_at": at,
        "status": "PENDING", "latency_seconds": None,
    }
    usage["calls"].append(call)
    usage["reserved_seconds"] = round(usage["reserved_seconds"] + duration, 3)
    result["provider_usage"] = usage
    result["session_revision"] += 1
    result = _touch(result, at)
    save(result)  # Durable reservation precedes transport, including failed calls.

    started = time.perf_counter()
    reason = "ASR_OUTAGE"
    try:
        if fixture:
            payload = fixture_response
        else:
            remaining = state["budget_seconds"] - elapsed - (time.monotonic() - operation_started)
            if remaining <= 0:
                raise TimeoutError("session budget expired before transport")
            payload = _groq_transcribe(audio_bytes, api_key, contract, min(60, remaining))
            if payload.get("error_code") in {"ASR_RATE_LIMIT", "ASR_OUTAGE"}:
                reason = payload["error_code"]
                raise ValueError("provider response rejected")
        # A chunk is one stable navigation interval. Only fixture mode can
        # supply interim/final updates; Groq returns one final transcription.
        updates = payload.get("updates") if fixture else None
        if updates is None:
            updates = [{"text": payload.get("text"), "finalized": True}]
        if not isinstance(updates, list) or not 1 <= len(updates) <= 20:
            raise ValueError("invalid bounded transcript updates")
        sequence = max([row["sequence"] for row in result["segments"]] + [-1]) + 1
        working = result
        received = (datetime.fromisoformat(at) + timedelta(seconds=time.perf_counter() - started)).isoformat()
        for update in updates:
            if api_key and api_key in str(update.get("text", "")):
                raise ValueError("provider text cannot contain the configured credential")
            working = append_segment(
                working, sequence=sequence, start_seconds=start, end_seconds=end,
                text=update["text"], received_at=received,
                finalized=update.get("finalized", False), partial=update.get("partial", False),
                speaker_label=update.get("speaker_label"), confidence=update.get("confidence"), profile=profile,
            )
        result = working
        result["provider_usage"]["calls"][-1]["status"] = "SUCCEEDED"
    except (TimeoutError, OSError, ValueError, TypeError, KeyError, AttributeError):
        # Never store provider messages, response bodies, or credentials.
        result = record_gap(result, start_seconds=start, end_seconds=end, reason=reason, detected_at=at)
        result["provider_usage"]["calls"][-1]["status"] = "FAILED"
    latency = round(time.perf_counter() - started, 3)
    result["provider_usage"]["calls"][-1]["latency_seconds"] = latency
    completed = (datetime.fromisoformat(at) + timedelta(seconds=latency)).isoformat()
    result = _touch(result, completed)
    save(result)
    return result


def replay_recording(fixture: dict, out: Path, *, save: Callable[[Path, dict], None]) -> dict:
    """Run 30 minutes of owned test audio through the real chunk/persistence path.

    The media clock is accelerated; measured latency is local processing only.
    Silence and supplied ASR responses cannot establish recognition accuracy.
    """
    if (fixture.get("scope"), fixture.get("duration_seconds"), fixture.get("chunk_seconds")) != (
        "OWNED_SYNTHETIC_AUDIO_FIXTURE", 1800, 30,
    ):
        raise ValueError("only the bounded owned 30-minute recording fixture is supported")
    out.mkdir(parents=True, exist_ok=True)
    state_path = out / "session-live.json"
    state = start_session(
        source_id="S-010", official_source_url="https://www.tccc.gov.tw/",
        stream_url="https://vod.tccc.gov.tw/live/owned-synthetic-fixture.m3u8",
        started_at=fixture["started_at"], max_duration_seconds=1800, budget_seconds=1800,
        transport_enabled=True, provider_authorized=True, session_id="LM-RECORDED-FIXTURE",
        asr_contract={"provider": "fixture", "model": "synthetic-chunk-responses", "version": "v1", "language": "zh"},
    )
    profile = load_catalog(Path(__file__).resolve().parents[1] / "docs/govintel/role-profiles.v1.json")["profiles"]["traffic-policy"]
    def stamp(seconds):
        return (datetime.fromisoformat(fixture["started_at"]) + timedelta(seconds=seconds)).isoformat()
    def persist(value):
        save(state_path, value)
    with tempfile.TemporaryDirectory(prefix="live-recording-") as directory:
        recording = Path(directory) / "recording.wav"
        pcm = b"\0\0" * 16000 * 30
        with wave.open(str(recording), "wb") as handle:
            handle.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
            for _ in range(60):
                handle.writeframesraw(pcm)
        chunk = Path(directory) / "chunk.wav"
        with wave.open(str(recording), "rb") as stream:
            for start in range(0, 1800, 30):
                frames = stream.readframes(16000 * 30)
                if fixture["stream_disconnect"][0] <= start < fixture["stream_disconnect"][1]:
                    continue
                with wave.open(str(chunk), "wb") as handle:
                    handle.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
                    handle.writeframes(frames)
                response = {"text": f"交通公開議題 fixture {start}"}
                if start == 0:
                    response = {"updates": [
                        {"text": fixture["interim_text"], "speaker_label": "SPEAKER_1", "partial": True},
                        {"text": fixture["final_text"], "speaker_label": "SPEAKER_2", "finalized": True},
                    ]}
                if start == fixture["asr_outage_at"]:
                    response = {"text": None}
                def save_or_crash(value):
                    persist(value)
                    if start == fixture["crash_at"]:
                        raise InterruptedError("owned fixture: crash after reservation")
                try:
                    state = ingest_chunk(state, chunk, start_seconds=start, requested_at=stamp(start),
                                         opt_in=True, fixture_response=response, profile=profile, save=save_or_crash)
                except InterruptedError:
                    state = validate_session(json.loads(state_path.read_text(encoding="utf-8")))
        calls_before = len(state["provider_usage"]["calls"])
        state = ingest_chunk(state, chunk, start_seconds=1800, requested_at=stamp(1800),
                             opt_in=True, fixture_response={"text": "must never ingest"}, save=persist)
    for segment in state["segments"][:3]:
        state = add_bookmark(state, segment_ids=[segment["segment_id"]], reason_code="TOPIC_MATCH", bookmarked_at=stamp(1800))
    persist(state)
    state = stop_session(state, ended_at=stamp(1800))
    save(out / "session-ended.json", state)
    bookmarks = state["bookmarks"]
    state = reconcile_session(state, [{"bookmark_id": bookmarks[0]["bookmark_id"], "outcome": "SOURCE_NOT_YET_AVAILABLE"}], reconciled_at=stamp(1801))
    official = {
        "official_source_id": "S-010", "official_url": "https://vod.tccc.gov.tw/owned-fixture-vod",
        "official_locator": "timestamp:0-30", "official_document_version": "owned-fixture-v1",
        "official_text_sha256": canonical_sha256(fixture["final_text"]),
    }
    state = reconcile_session(state, [
        {"bookmark_id": bookmarks[0]["bookmark_id"], "outcome": "CONFIRMED_BY_OFFICIAL_MEDIA", **official},
        {"bookmark_id": bookmarks[1]["bookmark_id"], "outcome": "SUPERSEDED_TRANSCRIPT", **{
            **official, "official_source_id": "S-007", "official_url": "https://yishi.tccc.gov.tw/owned-fixture-minutes",
            "official_source_type": "MINUTES",
            "official_locator": "minutes:item-3", "official_text_sha256": canonical_sha256(fixture["minutes_text"]),
        }},
        {"bookmark_id": bookmarks[2]["bookmark_id"], "outcome": "UNRESOLVED"},
    ], reconciled_at=stamp(86400))
    save(out / "session-reconciled.json", state)
    timeline = build_timeline(state)
    receipt = {
        "scope": fixture["scope"], "audio_duration_seconds": 1800, "network_calls": 0,
        "timeline_coverage_seconds": sum(row["end_seconds"] - row["start_seconds"] for row in timeline),
        "gap_seconds": sum(row["end_seconds"] - row["start_seconds"] for row in state["gap_intervals"]),
        "fixture_calls": len(state["provider_usage"]["calls"]),
        "reserved_seconds": state["provider_usage"]["reserved_seconds"],
        "after_deadline_calls": len(state["provider_usage"]["calls"]) - calls_before,
        "interim_final_revision": state["segments"][0]["revision"] == 2,
        "crash_recovered": any(gap["reason"] == "SESSION_CRASH" for gap in state["gap_intervals"]),
        "latency_scope": "LOCAL_FIXTURE_PROCESSING_ONLY",
        "max_processing_seconds": max(call["latency_seconds"] or 0 for call in state["provider_usage"]["calls"]),
    }
    (out / "runtime-receipt.json").write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    return receipt
