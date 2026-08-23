#!/usr/bin/env python3
"""計算 Groq ASR 與校對參考稿的正規化中文 CER。"""

from __future__ import annotations

import argparse
import hashlib
import json
import unicodedata
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_ASR = ROOT / "groq-asr-canary-2026-08-14.json"
DEFAULT_REFERENCE = ROOT / "evaluation" / "groq-asr-reference-2026-08-14.json"
DEFAULT_OUTPUT = ROOT / "evaluation" / "groq-asr-cer-2026-08-14.json"


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFKC", text).replace("台", "臺")
    return "".join(char for char in text if not char.isspace() and unicodedata.category(char)[0] not in {"P", "S"})


def levenshtein(reference: str, hypothesis: str) -> int:
    if len(reference) < len(hypothesis):
        reference, hypothesis = hypothesis, reference
    previous = list(range(len(hypothesis) + 1))
    for row, reference_char in enumerate(reference, start=1):
        current = [row]
        for column, hypothesis_char in enumerate(hypothesis, start=1):
            current.append(min(
                current[-1] + 1,
                previous[column] + 1,
                previous[column - 1] + (reference_char != hypothesis_char),
            ))
        previous = current
    return previous[-1]


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def self_check() -> None:
    assert normalize("臺中，警察！") == "臺中警察"
    assert normalize("台中") == "臺中"
    assert levenshtein("警察", "警政") == 1
    assert levenshtein("", "警察") == 2
    print("SELF_CHECK_OK")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--asr", type=Path, default=DEFAULT_ASR)
    parser.add_argument("--reference", type=Path, default=DEFAULT_REFERENCE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--threshold", type=float, default=0.20)
    parser.add_argument("--self-check", action="store_true")
    args = parser.parse_args()
    if args.self_check:
        self_check()
        return
    if not 0 < args.threshold < 1:
        raise ValueError("threshold 必須介於 0 與 1 之間")

    asr = json.loads(args.asr.read_text(encoding="utf-8"))
    reference = json.loads(args.reference.read_text(encoding="utf-8"))
    if asr.get("status") != "PASS":
        raise RuntimeError("ASR canary 尚未通過")
    if reference.get("reference_status") != "CURATED_FROM_OFFICIAL_RECORD":
        raise RuntimeError("參考稿狀態不符合 CER Gate")

    reference_text = "".join(turn["text"] for turn in reference["turns"])
    normalized_reference = normalize(reference_text)
    normalized_hypothesis = normalize(asr["asr"]["text"])
    if not normalized_reference:
        raise RuntimeError("正規化後的參考稿為空")

    edit_distance = levenshtein(normalized_reference, normalized_hypothesis)
    cer = edit_distance / len(normalized_reference)
    result = {
        "status": "PASS" if cer <= args.threshold else "FAIL",
        "checked_at": datetime.now(ZoneInfo("Asia/Taipei")).isoformat(timespec="seconds"),
        "metric": "normalized_zh_character_error_rate",
        "cer": round(cer, 6),
        "cer_percent": round(cer * 100, 2),
        "threshold": args.threshold,
        "threshold_percent": round(args.threshold * 100, 2),
        "edit_distance": edit_distance,
        "reference_characters": len(normalized_reference),
        "hypothesis_characters": len(normalized_hypothesis),
        "normalization": "Unicode NFKC、台→臺、移除空白／標點／符號，保留數字與文字",
        "reference": {
            "status": reference["reference_status"],
            "independent_human_signoff": reference["review"]["independent_human_signoff"],
            "source_is_proofread": reference["source"]["is_proofread"],
            "sha256": sha256(args.reference),
            "path": args.reference.name,
        },
        "asr": {
            "provider": asr["asr"]["provider"],
            "model": asr["asr"]["model"],
            "audio_sha256": asr["audio"]["sha256"],
            "sha256": sha256(args.asr),
            "path": args.asr.name,
        },
        "limitation": reference["review"]["limitation"],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"STATUS={result['status']}")
    print(f"CER={result['cer_percent']:.2f}% THRESHOLD={result['threshold_percent']:.2f}%")
    print(f"EDIT_DISTANCE={edit_distance} REFERENCE_CHARS={len(normalized_reference)} HYPOTHESIS_CHARS={len(normalized_hypothesis)}")
    print(f"OUTPUT={args.output.resolve()}")
    raise SystemExit(0 if result["status"] == "PASS" else 1)


if __name__ == "__main__":
    main()
