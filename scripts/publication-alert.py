#!/usr/bin/env python3
"""Deliver bounded publication alerts to the existing repository issue.

The receipt proves API delivery and exact readback. It never claims that a human
read the alert, that a source recovered, or that formal data admission passed.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
from urllib.request import HTTPRedirectHandler, Request, build_opener

REPOSITORY = "Reese-max/taichung-police-intel"
ISSUE = 20
API_BASE = f"https://api.github.com/repos/{REPOSITORY}/"
SCOPES = {"PRODUCTION_PUBLICATION", "ISOLATED_ARTIFACT_UPLOAD_DRILL"}
PHASES = {"RESTORE", "COLLECT", "V1", "V2", "V2_VERIFY", "SCHEMA_DRIFT", "VERIFY",
          "PRESERVE", "PAGES_UPLOAD", "EVIDENCE", "WORKER_DEPLOY", "PAGES_DEPLOY",
          "PUBLIC_VERIFY", "QUERY_VERIFY"}
STATES = {"success", "failure", "cancelled", "skipped", "unknown"}
META = re.compile(r"<!-- govintel-publication-alert-meta:(\{[^\n]*\}) -->")


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        return None


def api(path, payload=None):
    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        raise ValueError("GITHUB_TOKEN is required for delivery")
    request = Request(API_BASE + path,
        data=json.dumps(payload).encode() if payload is not None else None,
        headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
                 "Content-Type": "application/json", "User-Agent": "GovIntelPublicationAlert/1.0"})
    with build_opener(NoRedirect()).open(request, timeout=30) as response:
        raw = response.read(2 * 1024 * 1024 + 1)
        if len(raw) > 2 * 1024 * 1024:
            raise ValueError("GitHub response exceeds byte budget")
        return json.loads(raw)


def validate_health(health):
    if health.get("kind") != "GOVINTEL_RUNTIME_HEALTH_RECEIPT":
        raise ValueError("a runtime health receipt is required")
    if os.environ.get("GITHUB_REPOSITORY") != REPOSITORY:
        raise ValueError("alert destination does not match the executing repository")
    for name, env_name in (("run_id", "GITHUB_RUN_ID"), ("run_attempt", "GITHUB_RUN_ATTEMPT")):
        value = health.get(name)
        if not isinstance(value, str) or not re.fullmatch(r"[0-9]{1,20}", value) or value != os.environ.get(env_name):
            raise ValueError(f"receipt {name} must match the actual workflow")
    phases, jobs = health.get("phase_results"), health.get("job_results")
    if not isinstance(phases, dict) or set(phases) != PHASES or any(value not in STATES for value in phases.values()):
        raise ValueError("receipt must contain the known publication phases")
    if not isinstance(jobs, dict) or set(jobs) != {"BUILD_RESULT", "DEPLOY_RESULT"} or any(value not in STATES for value in jobs.values()):
        raise ValueError("receipt must contain actual job outcomes")
    instant = datetime.fromisoformat(health["observed_at"].replace("Z", "+00:00"))
    if instant.tzinfo is None:
        raise ValueError("receipt observation must include timezone")


def failure_phases(health):
    result = [key for key, value in {**health["phase_results"], **health["job_results"]}.items()
              if value in {"failure", "cancelled"}]
    if health.get("schema_drift_overall") == "BLOCKED" and "SCHEMA_DRIFT" not in result:
        result.append("SCHEMA_DRIFT")
    return sorted(result)


def recovered(health, scope):
    if failure_phases(health):
        return False
    if scope == "ISOLATED_ARTIFACT_UPLOAD_DRILL":
        return health["phase_results"]["PAGES_UPLOAD"] == "success"
    # A rights-blocked query can still have a verified deployment. Source and
    # governance gates remain separate from infrastructure recovery.
    return health.get("public_data_verified") is True and health.get("query_deployment_verified") is True


def list_comments(request):
    result = []
    for page in range(1, 11):
        rows = request(f"issues/{ISSUE}/comments?per_page=100&page={page}")
        if not isinstance(rows, list):
            raise ValueError("comment list is invalid")
        result.extend(rows)
        if len(rows) < 100:
            return result
    raise ValueError("comment pagination exceeds bounded delivery budget")


def alert_meta(comment):
    match = META.search(comment.get("body", ""))
    if not match:
        return None
    try:
        value = json.loads(match.group(1))
        return value if value.get("scope") in SCOPES and value.get("mode") in {"FAILURE", "RECOVERY"} else None
    except (ValueError, AttributeError):
        return None


def deliver(health, *, scope, request=api):
    validate_health(health)
    if scope not in SCOPES:
        raise ValueError("unknown alert scope")
    if os.environ.get("GITHUB_ACTIONS") != "true":
        raise ValueError("delivery must run in the actual GitHub workflow")
    comments = list_comments(request)
    failures = failure_phases(health)
    mode = "FAILURE" if failures else "RECOVERY"
    marker = f"<!-- govintel-publication-alert:v1:{scope}:{health['run_id']}:{health['run_attempt']}:{mode} -->"
    existing = [comment for comment in comments if marker in comment.get("body", "")]
    if len(existing) > 1:
        raise ValueError("duplicate alert markers; refusing overwrite")
    previous = None
    if not failures:
        if not recovered(health, scope):
            return {"status": "NO_INFRASTRUCTURE_RECOVERY_PROOF", "delivered": False, "human_acknowledged": None}
        annotated = [(comment, alert_meta(comment)) for comment in comments
                     if comment.get("user", {}).get("login") == "github-actions[bot]"]
        failure_comments = [comment for comment, meta in annotated
                            if meta and meta["scope"] == scope and meta["mode"] == "FAILURE"]
        if existing:
            metadata = alert_meta(existing[0]) or {}
            matches = [row for row in failure_comments if row["id"] == metadata.get("recovery_of_comment_id")]
            if len(matches) != 1:
                raise ValueError("existing recovery lacks its original alert")
            previous = matches[0]
        else:
            # A verified recovery supersedes the earlier failures in that scope;
            # otherwise retries of one incident produce endless recovery posts.
            closed_through = max([0, *(meta["recovery_of_comment_id"] for _, meta in annotated
                if meta and meta["scope"] == scope and meta["mode"] == "RECOVERY"
                and type(meta.get("recovery_of_comment_id")) is int)])
            open_alerts = [row for row in failure_comments if row["id"] > closed_through]
            if not open_alerts:
                return {"status": "NO_OPEN_ALERT", "delivered": False, "human_acknowledged": None}
            previous = max(open_alerts, key=lambda row: row["id"])
    metadata = {"scope": scope, "mode": mode, "run_id": health["run_id"],
                "run_attempt": health["run_attempt"], "recovery_of_comment_id": previous["id"] if previous else None}
    run_url = f"https://github.com/{REPOSITORY}/actions/runs/{health['run_id']}/attempts/{health['run_attempt']}"
    lines = [marker, "<!-- govintel-publication-alert-meta:" + json.dumps(metadata, separators=(",", ":")) + " -->",
             "### 發布故障告警" if failures else "### 發布基礎設施恢復紀錄",
             f"範圍：`{scope}`；[實際 workflow]({run_url})。"]
    if scope == "ISOLATED_ARTIFACT_UPLOAD_DRILL":
        lines.append("這是隔離的 artifact 上傳故障／恢復演練；未修改正式 Pages、Worker、來源或資料分支。")
    if failures:
        lines.append("失敗／取消階段：" + "、".join(f"`{phase}`" for phase in failures) + "。")
    else:
        lines.append(f"對應先前告警 comment `{previous['id']}`；只核對上述範圍的恢復。")
    lines.append("API 送達後另行精確回讀；真人確認維持未知。來源、授權、資料時效及真人驗收不由本告警批准。")
    body = "\n\n".join(lines) + "\n"
    if existing and (existing[0].get("body") != body or existing[0].get("user", {}).get("login") != "github-actions[bot]"):
        raise ValueError("existing alert changed or duplicated; refusing overwrite")
    comment = existing[0] if existing else request(f"issues/{ISSUE}/comments", {"body": body})
    identifier = comment.get("id")
    if type(identifier) is not int or identifier <= 0:
        raise ValueError("comment creation lacks an identifier")
    readback = request(f"issues/comments/{identifier}")
    if readback.get("id") != identifier or readback.get("body") != body:
        raise ValueError("alert readback mismatch")
    return {"schema_version": 1, "status": "DELIVERED_READBACK_VERIFIED", "delivered": True,
            "channel": "GITHUB_ISSUE_COMMENT", "repository": REPOSITORY, "issue": ISSUE,
            "comment_id": identifier, "comment_url": f"https://github.com/{REPOSITORY}/issues/{ISSUE}#issuecomment-{identifier}",
            "scope": scope, "mode": mode, "run_id": health["run_id"], "run_attempt": health["run_attempt"],
            "failed_phases": failures, "recovery_of_comment_id": metadata["recovery_of_comment_id"],
            "body_sha256": hashlib.sha256(body.encode()).hexdigest(), "duplicate_prevented": bool(existing),
            "verified_at": datetime.now(timezone.utc).isoformat(), "human_acknowledged": None}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--health", required=True, type=Path)
    parser.add_argument("--scope", choices=sorted(SCOPES), default="PRODUCTION_PUBLICATION")
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    try:
        receipt = deliver(json.loads(args.health.read_text(encoding="utf-8")), scope=args.scope)
        code = 0
    except Exception as error:
        # Do not expose token-bearing requests, server bodies, or arbitrary logs.
        receipt = {"schema_version": 1, "status": "ALERT_DELIVERY_FAILED", "delivered": False,
                   "error_type": type(error).__name__, "human_acknowledged": None}
        code = 1
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(receipt["status"])
    return code


if __name__ == "__main__":
    raise SystemExit(main())
