"""Report actual CI phases without equating action success with live verification."""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import importlib.util
import json
import os
from pathlib import Path
import sys
from typing import Mapping
from urllib.parse import urlsplit

PHASES = ("RESTORE", "COLLECT", "V1", "V2", "V2_VERIFY", "SCHEMA_DRIFT", "VERIFY", "PRESERVE", "PAGES_UPLOAD", "EVIDENCE", "PUBLIC_VERIFY")
OUTCOMES = {"success", "failure", "cancelled", "skipped"}
PHASE_TO_HEALTH = {
    "success": "SUCCESS",
    "failure": "FAILED",
    "cancelled": "FAILED",
    "skipped": "SKIPPED",
    "unknown": "UNKNOWN",
}


def phase_value(env: Mapping[str, str], key: str) -> str:
    value = env.get(key, "")
    return value if value in OUTCOMES else "unknown"


def load_system_health():
    path = Path(__file__).with_name("system-health.py")
    spec = importlib.util.spec_from_file_location("publication_runtime_system_health", path)
    if spec is None or spec.loader is None:
        raise ValueError("system health module is unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def runtime_health(env: Mapping[str, str], *, observed_at: str | None = None) -> dict:
    """Build a receipt from workflow outcomes without inventing phase timestamps."""
    observed_at = observed_at or datetime.now(timezone.utc).isoformat()
    generation_id = env.get("GENERATION_ID") or None
    state_commit = env.get("STATE_COMMIT") or None

    def stage(lane: str, name: str, outcome: str, error_class: str | None = None) -> dict:
        row = {"lane": lane, "stage": name, "outcome": PHASE_TO_HEALTH[outcome]}
        if generation_id:
            row["generation_id"] = generation_id
        if state_commit:
            row["state_commit"] = state_commit
        if error_class:
            row["error_class"] = error_class
        return row

    collect = phase_value(env, "COLLECT")
    schema = phase_value(env, "SCHEMA_DRIFT")
    validation = [phase_value(env, key) for key in ("V1", "V2", "V2_VERIFY", "VERIFY")]
    if all(value == "success" for value in validation):
        validation_outcome, validation_error = "success", None
    elif any(value in {"failure", "cancelled"} for value in validation):
        validation_outcome, validation_error = "failure", "CANONICAL_VALIDATION_FAILED"
    elif all(value == "unknown" for value in validation):
        validation_outcome, validation_error = "unknown", "CANONICAL_VALIDATION_NOT_REPORTED"
    else:
        validation_outcome, validation_error = "unknown", "CANONICAL_VALIDATION_INCOMPLETE"

    deploy = phase_value(env, "DEPLOY_RESULT")
    public_verify = phase_value(env, "PUBLIC_VERIFY")
    stages = [
        stage("discovery", "source_contracts", schema, "SCHEMA_DRIFT_NOT_VERIFIED" if schema != "success" else None),
        stage("publication", "collection", collect, "COLLECTION_NOT_RUN" if collect == "skipped" else None),
        stage("publication", "canonical_validation", validation_outcome, validation_error),
        stage("publication", "deployment", deploy, "DEPLOYMENT_NOT_RUN" if deploy == "skipped" else None),
        stage("publication", "public_http_verification", public_verify,
              "PUBLIC_HTTP_NOT_VERIFIED" if public_verify != "success" else None),
        stage("query", "mcp_web_query", "skipped", "NOT_IN_PUBLICATION_WORKFLOW"),
    ]
    health = load_system_health().build_health(stages)
    health.update({
        "kind": "GOVINTEL_RUNTIME_HEALTH_RECEIPT",
        "observed_at": observed_at,
        "run_id": env.get("GITHUB_RUN_ID") or None,
        "run_attempt": env.get("GITHUB_RUN_ATTEMPT") or None,
        "generation_id": generation_id,
        "state_commit": state_commit,
        "public_data_verified": phase_value(env, "BUILD_RESULT") == "success" and deploy == "success" and public_verify == "success",
        "phase_results": {key: phase_value(env, key) for key in PHASES},
    })
    return health


def write_runtime_health(env: Mapping[str, str], target: Path) -> dict:
    receipt = runtime_health(env)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return receipt


def report(env: Mapping[str, str]) -> tuple[str, int]:
    def outcome(key: str) -> str:
        value = env.get(key, "")
        return value if value in OUTCOMES else "unknown"

    build, deploy = outcome("BUILD_RESULT"), outcome("DEPLOY_RESULT")
    passed = build == deploy == "success"
    if "PUBLIC_VERIFY" in env and outcome("PUBLIC_VERIFY") != "success":
        passed = False
    verified = passed and outcome("PUBLIC_VERIFY") == "success"
    state = "PUBLIC_DATA_VERIFIED" if verified else "DEPLOY_ACTION_SUCCEEDED_UNVERIFIED_HTTP" if passed else "PUBLICATION_NOT_CONFIRMED"
    lines = ["## Publication outcome", f"State: `{state}`", "", "| Phase | Result |", "|---|---|",
             f"| build | {build} |", f"| deploy | {deploy} |"]
    lines.extend(f"| {key.lower()} | {outcome(key)} |" for key in PHASES)
    url = env.get("EVIDENCE_URL", "")
    parts = urlsplit(url)
    if outcome("EVIDENCE") == "success" and parts.scheme == "https" and parts.hostname == "github.com" and "/actions/runs/" in parts.path and "/artifacts/" in parts.path and not parts.query and not parts.fragment:
        lines.append(f"\nRetained evidence: <{url}>")
    else:
        lines.append("\nNo successful evidence-upload receipt is available. Inspect the job logs; do not assume an artifact exists.")
    if verified:
        lines.append("\nThe exact public data files passed anonymous HTTP/hash verification and checkpoint acknowledgement. This does not attest every frontend asset or upstream freshness.")
    else:
        lines.append("\nAction success alone is not anonymous HTTP/version/hash validation. No new published-state receipt is asserted here.")
    if not passed:
        lines.append("The current served version is unverified; do not report zero new events or assume deployment succeeded. Keep the last verified snapshot and its age visible.")
    return "\n".join(lines) + "\n", 0 if passed else 1


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--health-output", type=Path)
    args = parser.parse_args(argv)
    text, code = report(os.environ)
    if args.health_output:
        try:
            write_runtime_health(os.environ, args.health_output)
        except (OSError, ValueError, json.JSONDecodeError) as error:
            print(f"HEALTH_RECEIPT_FAIL {error}", file=sys.stderr)
            code = 1
    target = os.environ.get("GITHUB_STEP_SUMMARY")
    if target:
        with Path(target).open("a", encoding="utf-8") as handle:
            handle.write(text)
    print(text)
    if code:
        print("::warning title=Publication not confirmed::Check build, preserve, artifact and deploy outcomes; this is not a zero-event result.")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
