#!/usr/bin/env python3
"""Run an explicit free-model diagnostic on project-authored synthetic seeds.

No live records, expected labels, or corpus files are sent. This is a development
diagnostic, not a holdout benchmark, product-arm execution, or human study.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import time

ROOT = Path(__file__).resolve().parents[1]
MODEL = "opencode/mimo-v2.6-flash-free"
TASKS = {"event_pair", "material_change", "claim_support"}
PROMPT = '''Evaluate fictional, project-authored GovIntel examples using only the supplied input. Do not call tools or search. Return exactly a JSON array with one object per case: {"case_id":"...","prediction":{...}}. No markdown or explanations.
event_pair: {"same_event":boolean}. Same name alone is insufficient; conflicting dates or places indicate distinct events. A documented repost of the same upstream is the same event, not independent evidence.
material_change: {"material_change":boolean,"fields":string[]}. Time changes use start_time, location changes use location; punctuation/layout changes are immaterial with empty fields.
claim_support: {"support_status":"SUPPORTED"|"PARTIAL"|"CONFLICT"|"INSUFFICIENT"|"STALE"}. Every material clause needs evidence. A supported time plus an invented cause is PARTIAL; disagreeing evidence is CONFLICT; absent evidence is INSUFFICIENT; an assertion about current state based only on stale evidence is STALE. Missing data never proves safety, no events, or a cause.
Inputs:
'''


def prepare(manifest_path: Path):
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("dataset_type") != "SYNTHETIC_REGRESSION_SEED":
        raise ValueError("only synthetic regression seeds are permitted")
    path = (manifest_path.parent / manifest["case_file"]).resolve()
    if not path.is_relative_to(manifest_path.parent.resolve()):
        raise ValueError("case file must stay within its manifest directory")
    all_cases = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if not all_cases or any(case.get("synthetic") is not True for case in all_cases):
        raise ValueError("every case must explicitly be synthetic before transport")
    cases = [case for case in all_cases if case.get("task") in TASKS]
    if not cases:
        raise ValueError("no supported synthetic cases")
    inputs = [{key:case[key] for key in ("case_id", "task", "input")} for case in cases]
    prompt = PROMPT + json.dumps(inputs, ensure_ascii=False, separators=(",", ":"))
    selected = dict(manifest, task_types=sorted(TASKS & {case["task"] for case in cases}),
                    evaluation_scope="LIVE_MODEL_SYNTHETIC_DIAGNOSTIC_NOT_PRODUCTION")
    return selected, cases, prompt, [case["case_id"] for case in all_cases if case.get("task") not in TASKS]


def parse_response(stdout: str, exit_code: int):
    events = [json.loads(line) for line in stdout.splitlines() if line.strip()]
    if exit_code != 0 or any(event.get("type") == "error" for event in events):
        raise ValueError("CLI or provider returned an error; no evaluation result")
    if any(event.get("type") == "tool_use" or event.get("part", {}).get("type") == "tool" for event in events):
        raise ValueError("unexpected tool use; diagnostic cannot be accepted")
    text = "".join(event.get("part", {}).get("text", "") for event in events if event.get("type") == "text")
    predictions = json.loads(text)
    if not isinstance(predictions, list) or not predictions:
        raise ValueError("model must return a nonempty prediction array")
    steps = [{key:event["part"].get(key) for key in ("reason", "tokens", "cost")} for event in events if event.get("type") == "step_finish"]
    if not steps or any(step["reason"] != "stop" for step in steps):
        raise ValueError("model response is incomplete")
    return predictions, steps


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=ROOT / "eval/gold/v1/manifest.json")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    manifest, cases, prompt, excluded = prepare(args.manifest)
    output = args.output.resolve()
    if output.exists():
        raise ValueError("use a new output directory; prior attempts must be retained")
    output.mkdir(parents=True)
    isolated = output / "isolated"
    isolated.mkdir()
    (output / "prompt.txt").write_text(prompt, encoding="utf-8")
    (output / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    corpus = "".join(json.dumps(case, ensure_ascii=False) + "\n" for case in cases)
    (output / "cases.jsonl").write_text(corpus, encoding="utf-8")
    env = dict(os.environ)
    # The official free tier rejects clients which remove its built-in tools.
    # Keep the standard CLI schema; noninteractive permission requests reject.
    env["OPENCODE_CONFIG_CONTENT"] = json.dumps({"permission":{"*":"ask"}})
    env["OPENCODE_PERMISSION"] = json.dumps({"*":"ask"})
    command = ["opencode", "run", "--pure", "--agent", "build", "--model", MODEL,
               "--format", "json", "--title", "GovIntel synthetic diagnostic", "--dir", str(isolated), prompt]
    receipt = {"schema_version":1, "status":"STARTED", "evaluation_scope":manifest["evaluation_scope"],
               "model_requested":MODEL, "prompt_version":2, "case_count":len(cases), "excluded_case_ids":excluded,
               "prompt_sha256":hashlib.sha256(prompt.encode()).hexdigest(), "case_file_sha256":hashlib.sha256(corpus.encode()).hexdigest(),
               "started_at":datetime.now(timezone.utc).isoformat(), "official_records_transmitted":0,
               "human_participants":0, "production_provider_enabled":False, "provider_network_request_count":None}
    started = time.monotonic()
    try:
        result = subprocess.run(command, env=env, capture_output=True, text=True, timeout=110)
        (output / "private-cli-events.jsonl").write_text(result.stdout, encoding="utf-8")
        (output / "private-cli-stderr.txt").write_text(result.stderr, encoding="utf-8")
        receipt.update(exit_code=result.returncode, events_sha256=hashlib.sha256(result.stdout.encode()).hexdigest())
        predictions, steps = parse_response(result.stdout, result.returncode)
        spec = importlib.util.spec_from_file_location("govintel_seed_evaluator", ROOT / "scripts/evaluate-govintel.py")
        evaluator = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(evaluator)
        report = evaluator.evaluate(manifest, cases, evaluator.index_predictions(predictions))
        (output / "predictions.jsonl").write_text("".join(json.dumps(row, ensure_ascii=False)+"\n" for row in predictions), encoding="utf-8")
        (output / "evaluation-report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        receipt.update(status="EVALUATED", evaluated_cases=report["evaluated_cases"],
                       exact_case_matches=report["exact_case_match_count"], tool_calls=0, provider_reported_steps=steps)
    except subprocess.TimeoutExpired as error:
        (output / "private-cli-events.jsonl").write_bytes(error.stdout or b"")
        (output / "private-cli-stderr.txt").write_bytes(error.stderr or b"")
        receipt.update(status="TIMED_OUT")
    except (ValueError, OSError) as error:
        receipt.update(status="FAILED", failure_type=type(error).__name__, failure_reason=str(error))
    receipt.update(finished_at=datetime.now(timezone.utc).isoformat(), elapsed_seconds=round(time.monotonic()-started, 3))
    (output / "live-model-receipt.json").write_text(json.dumps(receipt, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(receipt, ensure_ascii=False))
    return 0 if receipt["status"] == "EVALUATED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
