"""Report actual CI phases without equating action success with live verification."""
from __future__ import annotations
import os
from pathlib import Path
from typing import Mapping
from urllib.parse import urlsplit

PHASES = ("COLLECT", "V1", "V2", "V2_VERIFY", "VERIFY", "PRESERVE", "PAGES_UPLOAD", "EVIDENCE")
OUTCOMES = {"success", "failure", "cancelled", "skipped"}


def report(env: Mapping[str, str]) -> tuple[str, int]:
    def outcome(key: str) -> str:
        value = env.get(key, "")
        return value if value in OUTCOMES else "unknown"

    build, deploy = outcome("BUILD_RESULT"), outcome("DEPLOY_RESULT")
    passed = build == deploy == "success"
    state = "DEPLOY_ACTION_SUCCEEDED_UNVERIFIED_HTTP" if passed else "PUBLICATION_NOT_CONFIRMED"
    lines = ["## Publication outcome", f"State: `{state}`", "", "| Phase | Result |", "|---|---|",
             f"| build | {build} |", f"| deploy | {deploy} |"]
    lines.extend(f"| {key.lower()} | {outcome(key)} |" for key in PHASES)
    url = env.get("EVIDENCE_URL", "")
    parts = urlsplit(url)
    if outcome("EVIDENCE") == "success" and parts.scheme == "https" and parts.hostname == "github.com" and "/actions/runs/" in parts.path and "/artifacts/" in parts.path and not parts.query and not parts.fragment:
        lines.append(f"\nRetained evidence: <{url}>")
    else:
        lines.append("\nNo successful evidence-upload receipt is available. Inspect the job logs; do not assume an artifact exists.")
    lines.append("\nAction success alone is not anonymous HTTP/version/hash validation. No new published-state receipt is asserted here.")
    if not passed:
        lines.append("The current served version is unverified; do not report zero new events or assume deployment succeeded. Keep the last verified snapshot and its age visible.")
    return "\n".join(lines) + "\n", 0 if passed else 1


def main() -> int:
    text, code = report(os.environ)
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
