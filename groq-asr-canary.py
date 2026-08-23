#!/usr/bin/env python3
"""轉錄 5 分鐘公開議會影音，驗證 Groq 時間戳與警政詞彙。"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import requests


SOURCE_URL = "https://vod.tccc.gov.tw/wb_news02.asp?url=92&ano=14170&pageno=1"
MEDIA_URL = "https://streamak0128.akamaized.net/vod0128vh-67eb/_definst_/04A07/05_1150427/1150427_1415_5_04_01_3_1.mp4/playlist.m3u8?iMda_seq=144895"
TITLE = "第4屆第7次定期會業務質詢：警消環衛部分"
POLICE_TERMS = ("警察局", "警政", "警察", "員警", "治安", "詐騙", "詐欺", "毒品", "交通執法", "派出所", "警消")
WORD_TIMESTAMP_MAX_BACKTRACK_SECONDS = 2.0


def run(command: list[str]) -> str:
    return subprocess.run(command, check=True, capture_output=True, text=True, encoding="utf-8").stdout.strip()


def valid_timestamps(items: list[dict], duration: float, max_backtrack: float = 0.05) -> bool:
    previous = 0.0
    for item in items:
        start, end = float(item["start"]), float(item["end"])
        if start < 0 or start < previous - max_backtrack or end < start or end > duration + 1:
            return False
        previous = start
    return True


def self_check() -> None:
    assert valid_timestamps([{"start": 0, "end": 1}, {"start": 1, "end": 2}], 2)
    assert valid_timestamps([{"start": 1.5, "end": 1.7}, {"start": 0.4, "end": 0.6}], 2, WORD_TIMESTAMP_MAX_BACKTRACK_SECONDS)
    assert not valid_timestamps([{"start": 2.5, "end": 2.7}, {"start": 0.4, "end": 0.6}], 3, WORD_TIMESTAMP_MAX_BACKTRACK_SECONDS)
    assert not valid_timestamps([{"start": 2, "end": 1}], 2)
    print("SELF_CHECK_OK")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-url", default=SOURCE_URL)
    parser.add_argument("--media-url", default=MEDIA_URL)
    parser.add_argument("--start-seconds", type=float, default=0)
    parser.add_argument("--duration-seconds", type=float, default=300)
    parser.add_argument("--output", type=Path, default=Path("groq-asr-canary-2026-08-14.json"))
    parser.add_argument("--self-check", action="store_true")
    args = parser.parse_args()
    if args.self_check:
        self_check()
        return

    api_key = os.environ.get("GROQ_API_KEY")
    if not api_key:
        raise RuntimeError("缺少 GROQ_API_KEY")
    if args.start_seconds < 0 or not 1 <= args.duration_seconds <= 300:
        raise ValueError("start 必須非負，duration 必須為 1～300 秒")
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        raise RuntimeError("缺少 ffmpeg 或 ffprobe")

    with tempfile.TemporaryDirectory(prefix="groq-asr-") as directory:
        audio = Path(directory) / "canary.flac"
        run([
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-ss", str(args.start_seconds),
            "-i", args.media_url, "-t", str(args.duration_seconds), "-vn", "-ac", "1", "-ar", "16000",
            "-c:a", "flac", "-y", str(audio),
        ])
        audio_duration = float(run([
            "ffprobe", "-v", "error", "-show_entries", "format=duration",
            "-of", "default=nokey=1:noprint_wrappers=1", str(audio),
        ]))
        audio_bytes = audio.read_bytes()
        if len(audio_bytes) > 25 * 1024 * 1024:
            raise RuntimeError(f"音訊 {len(audio_bytes)} bytes，超過 Groq 25 MB 限制")

        preflight = requests.get(
            "https://api.groq.com/openai/v1/models",
            headers={"Authorization": f"Bearer {api_key}", "User-Agent": "groq-asr-canary/1"},
            timeout=60,
        )
        if preflight.status_code != 200:
            result = {
                "status": "BLOCKED_NETWORK",
                "checked_at": datetime.now(ZoneInfo("Asia/Taipei")).isoformat(timespec="seconds"),
                "source": {
                    "title": TITLE,
                    "official_page_url": args.source_url,
                    "media_url": args.media_url,
                    "clip_start_seconds": args.start_seconds,
                    "clip_duration_seconds": audio_duration,
                },
                "audio": {
                    "format": "FLAC",
                    "bytes": len(audio_bytes),
                    "sha256": hashlib.sha256(audio_bytes).hexdigest(),
                },
                "asr": {"provider": "Groq", "model": "whisper-large-v3", "executed": False},
                "api_preflight": {"status_code": preflight.status_code, "body": preflight.text[:500]},
                "validation": {
                    "segment_count": 0,
                    "word_count": 0,
                    "segment_timestamps_valid": False,
                    "word_timestamps_valid": False,
                    "detected_police_terms": [],
                },
            }
            args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            print("STATUS=BLOCKED_NETWORK")
            print(f"OUTPUT={args.output.resolve()}")
            print(f"AUDIO_SECONDS={audio_duration:.3f}")
            print(f"GROQ_HTTP={preflight.status_code}")
            return

        started = time.perf_counter()
        with audio.open("rb") as handle:
            response = requests.post(
                "https://api.groq.com/openai/v1/audio/transcriptions",
                headers={"Authorization": f"Bearer {api_key}"},
                files={"file": ("canary.flac", handle, "audio/flac")},
                data=[
                    ("model", "whisper-large-v3"),
                    ("language", "zh"),
                    ("response_format", "verbose_json"),
                    ("temperature", "0"),
                    ("timestamp_granularities[]", "segment"),
                    ("timestamp_granularities[]", "word"),
                ],
                timeout=300,
            )
        if response.status_code != 200:
            raise RuntimeError(f"Groq ASR HTTP {response.status_code}：{response.text[:500]}")
        elapsed = time.perf_counter() - started
        transcript = response.json()

    segments = transcript.get("segments") or []
    words = transcript.get("words") or []
    text = transcript.get("text", "")
    segment_timestamps_valid = bool(segments) and valid_timestamps(segments, audio_duration)
    # ponytail: Groq may locally realign words; segment timestamps remain strict.
    word_timestamps_valid = bool(words) and valid_timestamps(words, audio_duration, WORD_TIMESTAMP_MAX_BACKTRACK_SECONDS)
    detected_terms = [term for term in POLICE_TERMS if term in text]
    result = {
        "status": "PASS" if segment_timestamps_valid and word_timestamps_valid and detected_terms else "FAIL",
        "checked_at": datetime.now(ZoneInfo("Asia/Taipei")).isoformat(timespec="seconds"),
        "source": {
            "title": TITLE,
            "official_page_url": args.source_url,
            "media_url": args.media_url,
            "clip_start_seconds": args.start_seconds,
            "clip_duration_seconds": audio_duration,
        },
        "audio": {
            "format": "FLAC",
            "bytes": len(audio_bytes),
            "sha256": hashlib.sha256(audio_bytes).hexdigest(),
        },
        "asr": {
            "provider": "Groq",
            "model": "whisper-large-v3",
            "language": transcript.get("language", "zh"),
            "elapsed_seconds": round(elapsed, 3),
            "text": text,
            "segments": segments,
            "words": words,
        },
        "validation": {
            "segment_count": len(segments),
            "word_count": len(words),
            "segment_timestamps_valid": segment_timestamps_valid,
            "word_timestamps_valid": word_timestamps_valid,
            "detected_police_terms": detected_terms,
        },
    }
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"STATUS={result['status']}")
    print(f"OUTPUT={args.output.resolve()}")
    print(f"AUDIO_SECONDS={audio_duration:.3f}")
    print(f"SEGMENTS={len(segments)} WORDS={len(words)}")
    print(f"POLICE_TERMS={','.join(detected_terms)}")


if __name__ == "__main__":
    main()
