#!/usr/bin/env python3
"""Single-checkout GovIntel release-candidate end-to-end verification.

Every enabled module is imported from THIS checkout only. The historical
pinned-SHA replay (scripts/verify-backbone-runtime.py, PR #46) is a separate
lane and its PASS never substitutes for current-code checks. Verification
uses a temporary serve directory and loopback HTTP only: it writes no
production data and persists no credentials.

Usage:
    python -X utf8 scripts/verify-current-checkout.py [--mode full|core]
        [--output DIR] [--skip-browser] [--skip-sabotage] [--chrome-path PATH]
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlsplit
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "apps" / "web" / "public" / "data"
CATALOG = ROOT / "docs" / "govintel" / "source-catalog.v2.json"
LOCKFILES = ("requirements.txt", "package-lock.json", "apps/web/package-lock.json")
MODULES = {
    "query_store": "scripts/query-store.py",
    "system_health": "scripts/system-health.py",
    "source_policy": "scripts/source-policy.py",
}
PUBLICATION_VERIFIERS = ("scripts/verify-publication-bundle.py", "scripts/verify-v2-publication.py")
SABOTAGE_FILES = (
    [path for path in MODULES.values()]
    + list(PUBLICATION_VERIFIERS)
    + ["scripts/verify-current-checkout.py",
       "docs/govintel/source-catalog.v2.json",
       "state/v2-shadow-state.json",
       "requirements.txt"]
)
UNAVAILABLE_CAPABILITIES = (
    "event_fusion", "entity_registry", "answer_evidence_gate",
    "gold_evaluation", "chat_mcp", "live_collection", "persistent_handoff",
)
MAX_SABOTAGE_SECONDS = 300


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def canonical_json(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def run(args: list[str], cwd: Path, timeout: int = 600) -> subprocess.CompletedProcess:
    return subprocess.run(args, cwd=cwd, capture_output=True, text=True, encoding="utf-8", timeout=timeout)


def hash_lockfiles(paths: list[Path]) -> str:
    material = []
    for path in sorted(paths, key=lambda p: str(p)):
        material.append({"file": path.name, "sha256": sha256_bytes(path.read_bytes())})
    return sha256_bytes(canonical_json(material))


def collect_identity(root: Path) -> dict[str, Any]:
    head = run(["git", "-C", str(root), "rev-parse", "HEAD"], root, timeout=30)
    code_sha = head.stdout.strip() if head.returncode == 0 else None
    dirty = run(["git", "-C", str(root), "status", "--porcelain", "--untracked-files=no"], root, timeout=30)
    lock_files = [name for name in LOCKFILES if (root / name).is_file()]
    return {
        "code_sha": code_sha,
        "worktree_dirty": bool(dirty.returncode == 0 and dirty.stdout.strip()),
        "dependency_lock_hash": hash_lockfiles([root / name for name in lock_files]) if lock_files else None,
        "dependency_lock_files": lock_files,
        "missing_lock_files": [name for name in LOCKFILES if name not in lock_files],
    }


def load_checkout_modules(root: Path) -> dict[str, Any]:
    modules = {}
    for name, relative in MODULES.items():
        path = root / relative
        spec = importlib.util.spec_from_file_location(f"checkout_{name}", path)
        if spec is None or spec.loader is None:
            raise ValueError(f"module not found: {relative}")
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        resolved = Path(module.__file__).resolve()
        if not resolved.is_relative_to(root.resolve()):
            raise ValueError(f"module escaped checkout: {name} -> {resolved}")
        modules[name] = module
    return modules


def build_candidate_context(root: Path, modules: dict[str, Any]) -> dict[str, Any]:
    qs = modules["query_store"]
    sp = modules["source_policy"]
    feed, feed_hash = qs.load_json(root / "apps" / "web" / "public" / "data" / "intelligence-feed.json")
    status, status_hash = qs.load_json(root / "apps" / "web" / "public" / "data" / "source-status.json")
    brief, brief_hash = qs.load_json(root / "apps" / "web" / "public" / "data" / "v2-daily-brief.json")
    store = qs.build_store(feed, status, brief, {"feed": feed_hash, "status": status_hash, "brief": brief_hash})
    rebuilt = qs.build_store(feed, status, brief, {"feed": feed_hash, "status": status_hash, "brief": brief_hash})
    if rebuilt != store:
        raise ValueError("query store rebuild is not deterministic")
    catalog = sp.load_catalog(root / "docs" / "govintel" / "source-catalog.v2.json")
    policy = sp.compile_policy(catalog)
    if policy["active_source_ids"] != sorted(qs.EXPECTED_SOURCES):
        raise ValueError("source policy active set does not match the pinned P0 query scope")
    supported = sorted(row["capability_id"] for row in policy["capabilities"] if row["supported"])
    unsupported = [
        {"capability_id": row["capability_id"], "status": "CAPABILITY_NOT_AVAILABLE",
         "note": row["coverage_limitation"]}
        for row in policy["capabilities"] if not row["supported"]
    ]
    for capability in UNAVAILABLE_CAPABILITIES:
        unsupported.append({"capability_id": capability, "status": "CAPABILITY_NOT_AVAILABLE",
                            "note": "not enabled in this M1 candidate checkout"})
    identity = collect_identity(root)
    candidate_id = f"rc-{(identity['code_sha'] or 'unversioned')[:12]}-{utcnow().strftime('%Y%m%dT%H%M%SZ')}"
    return {
        "root": root, "modules": modules,
        "artifacts": {"feed": feed, "status": status, "brief": brief},
        "hashes": {"feed": feed_hash, "status": status_hash, "brief": brief_hash},
        "store": store, "policy": policy,
        "enabled_capabilities": supported + ["canonical_query_index", "static_site_http", "loopback_query_api"],
        "unavailable_capabilities": unsupported,
        "candidate_id": candidate_id,
        "identity": identity,
        "test_mode": "CURRENT_CHECKOUT_LOOPBACK_HTTP",
    }


def candidate_manifest(ctx: dict[str, Any], data_status: str) -> dict[str, Any]:
    store = ctx["store"]
    return {
        "schema_version": 1,
        "kind": "GOVINTEL_CANDIDATE_MANIFEST",
        "candidate_id": ctx["candidate_id"],
        "code_sha": ctx["identity"]["code_sha"],
        "query_generation_id": store["generation_id"],
        "projection_version": store["projection_version"],
        "policy_version": ctx["policy"]["policy_version"],
        "policy_hash": ctx["policy"]["policy_hash"],
        "data_status": data_status,
        "enabled_capabilities": ctx["enabled_capabilities"],
        "unavailable_capabilities": ctx["unavailable_capabilities"],
        "test_mode": ctx["test_mode"],
    }


def prepare_serve_dir(ctx: dict[str, Any], dest: Path, site_dir: Path | None = None) -> Path:
    dest.mkdir(parents=True, exist_ok=True)
    if site_dir and site_dir.is_dir():
        for entry in site_dir.iterdir():
            target = dest / entry.name
            if entry.is_dir():
                shutil.copytree(entry, target, dirs_exist_ok=True)
            else:
                shutil.copy2(entry, target)
    else:
        (dest / "data").mkdir(parents=True, exist_ok=True)
        for artifact in DATA_DIR.iterdir() if DATA_DIR.is_dir() else []:
            if artifact.is_file():
                shutil.copy2(artifact, dest / "data" / artifact.name)
        (dest / "index.html").write_text(
            "<!doctype html><html lang=\"zh-Hant-TW\"><head><meta charset=\"utf-8\">"
            "<title>GovIntel candidate placeholder</title></head><body>"
            "<main id=\"candidate-placeholder\">GovIntel 候選版靜態預留頁；完整站台由 next build 產生。</main>"
            "</body></html>\n", encoding="utf-8")
    qs = ctx["modules"]["query_store"]
    qs.atomic_write_json(dest / "data" / "query-store.json", ctx["store"])
    data_status, _ = qs.assess_scope(store=ctx["store"], source_id=None, now=utcnow())
    write_json(dest / "data" / "candidate.json", candidate_manifest(ctx, data_status))
    return dest


def _json_response(handler: SimpleHTTPRequestHandler, value: Any, status: int = 200) -> None:
    body = canonical_json(value)
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.send_header("Cache-Control", "no-store")
    handler.end_headers()
    handler.wfile.write(body)


def make_handler(ctx: dict[str, Any], serve_dir: Path):
    qs = ctx["modules"]["query_store"]
    health = ctx["modules"]["system_health"]
    sp = ctx["modules"]["source_policy"]
    store_path = serve_dir / "data" / "query-store.json"

    def served_store() -> dict[str, Any]:
        store, _ = qs.load_json(store_path)
        qs.validate_store(store)
        return store

    class Handler(SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=str(serve_dir), **kwargs)

        def log_message(self, fmt, *args):  # keep evidence without stdout noise
            with self.server.request_lock:
                self.server.request_log.append(fmt % args)

        def _query_down(self) -> bool:
            return bool(getattr(self.server, "query_down", False))

        def _api_query(self, params: dict[str, list[str]]) -> None:
            if self._query_down():
                return _json_response(self, {"status": "QUERY_UNAVAILABLE",
                                             "error": "query service unavailable"}, 503)
            try:
                store = served_store()
            except Exception as error:
                return _json_response(self, {"status": "QUERY_UNAVAILABLE",
                                             "error": f"query index unavailable: {type(error).__name__}"}, 503)
            text = params.get("q", [None])[0]
            source_id = params.get("source_id", [None])[0]
            change_type = params.get("change_type", [None])[0]
            cursor = params.get("cursor", [None])[0]
            expected = params.get("expected_generation", [None])[0]
            try:
                limit = int(params.get("limit", ["20"])[0])
            except (TypeError, ValueError):
                return _json_response(self, {"code": "INVALID_QUERY", "error": "limit must be an integer"}, 400)
            try:
                result = qs.query_store(store, text=text, source_id=source_id, change_type=change_type,
                                        limit=limit, cursor=cursor, expected_generation=expected)
            except ValueError as error:
                message = str(error)
                code = "GENERATION_MISMATCH" if ("generation" in message or "cursor" in message) else "INVALID_QUERY"
                return _json_response(self, {"code": code, "error": message}, 409 if code == "GENERATION_MISMATCH" else 400)
            _json_response(self, result)

        def _api_version(self) -> None:
            if self._query_down():
                generation = None
            else:
                try:
                    generation = served_store()["generation_id"]
                except Exception:
                    generation = None
            _json_response(self, {
                "schema_version": 1, "candidate_id": ctx["candidate_id"],
                "code_sha": ctx["identity"]["code_sha"],
                "generation_id": generation,
                "projection_version": ctx["store"]["projection_version"],
                "policy_version": ctx["policy"]["policy_version"],
                "policy_hash": ctx["policy"]["policy_hash"],
                "test_mode": ctx["test_mode"],
                "canonical_artifact_hashes": ctx["hashes"],
                "collection_run_id": ctx["artifacts"]["feed"].get("collection_run_id"),
                "generated_at": ctx["artifacts"]["feed"].get("generated_at"),
            })

        def _api_health(self) -> None:
            status_doc = ctx["artifacts"]["status"]
            brief = ctx["artifacts"]["brief"]
            stages = health.current_publication_stages(status_doc, brief)
            stages = [s for s in stages if s["stage"] not in ("deployment", "public_http_verification", "query_index")]
            try:
                generation = served_store()["generation_id"]
            except Exception:
                generation = None
            down = self._query_down() or generation is None
            stages.append({
                "lane": "publication", "stage": "deployment",
                "outcome": "SUCCESS",
                "generation_id": generation,
                "detail": "LOOPBACK_HTTP_NOT_PUBLIC_DEPLOYMENT",
            })
            stages.append({
                "lane": "publication", "stage": "public_http_verification",
                "outcome": "SUCCESS" if not down else "UNKNOWN",
                "generation_id": generation,
                "detail": "LOOPBACK_HASH_VERIFIED" if not down else "QUERY_INDEX_UNREADABLE",
            })
            stages.append({
                "lane": "query", "stage": "query_index",
                "outcome": "FAILED" if down else "SUCCESS",
                "generation_id": generation,
                "error_class": "QUERY_DOWN_INJECTED" if down else None,
            })
            _json_response(self, health.build_health(stages))

        def _api_capability(self, params: dict[str, list[str]]) -> None:
            capability_id = params.get("id", [None])[0]
            if not capability_id:
                return _json_response(self, {"code": "INVALID_QUERY", "error": "id required"}, 400)
            try:
                store = served_store()
                source_states = {
                    row["source_id"]: {
                        "source_health": row.get("source_health"),
                        "window_completeness": row.get("window_completeness"),
                        "freshness": row.get("freshness_status"),
                    }
                    for row in store["sources"]
                }
            except Exception:
                source_states = None
            _json_response(self, sp.assess_query(ctx["policy"], capability_id, source_states))

        def do_GET(self):
            split = urlsplit(self.path)
            params = parse_qs(split.query)
            if split.path == "/api/query":
                return self._api_query(params)
            if split.path == "/api/version":
                return self._api_version()
            if split.path == "/api/health":
                return self._api_health()
            if split.path == "/api/capability":
                return self._api_capability(params)
            return super().do_GET()

    return Handler


def start_server(ctx: dict[str, Any], serve_dir: Path, port: int = 0):
    server = ThreadingHTTPServer(("127.0.0.1", port), make_handler(ctx, serve_dir))
    server.query_down = False
    server.request_log = []
    server.request_lock = threading.Lock()
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, f"http://127.0.0.1:{server.server_address[1]}"


def http_get(url: str) -> tuple[int, bytes]:
    request = urllib.request.Request(url, headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as error:
        return error.code, error.read()


def http_json(url: str) -> tuple[int, Any]:
    status, body = http_get(url)
    try:
        return status, json.loads(body.decode("utf-8"))
    except ValueError:
        return status, None


def check_record(check_id: str, ok: bool, detail: str, **extra) -> dict[str, Any]:
    return {"id": check_id, "status": "PASS" if ok else "FAIL", "detail": detail, **extra}


def check_served_store_consistency(ctx: dict[str, Any], base: str) -> dict[str, Any]:
    status, doc = http_json(base + "/data/query-store.json")
    if status != 200 or not isinstance(doc, dict):
        return check_record("served_store_consistency", False, f"served query-store HTTP {status}")
    try:
        ctx["modules"]["query_store"].validate_store(doc)
    except ValueError as error:
        return check_record("served_store_consistency", False, f"served store invalid: {error}")
    served = doc.get("generation_id")
    expected = ctx["store"]["generation_id"]
    return check_record(
        "served_store_consistency", served == expected,
        "served generation matches built candidate" if served == expected
        else f"HTTP 200 but stale generation {served[:12]}… != expected {expected[:12]}…",
        served_generation=served, expected_generation=expected)


def run_http_checks(ctx: dict[str, Any], base: str) -> list[dict[str, Any]]:
    qs = ctx["modules"]["query_store"]
    store = ctx["store"]
    generation = store["generation_id"]
    checks = []

    status, body = http_get(base + "/")
    checks.append(check_record("http_static_index", status == 200 and b"<html" in body.lower(),
                               f"GET / -> {status}"))

    status, body = http_get(base + "/data/intelligence-feed.json")
    feed_ok = status == 200 and sha256_bytes(body) == ctx["hashes"]["feed"]
    checks.append(check_record("http_canonical_bytes_match", feed_ok,
                               f"GET /data/intelligence-feed.json -> {status}, sha256 {'matches' if feed_ok else 'MISMATCH'}"))

    status, doc = http_json(base + "/api/version")
    checks.append(check_record(
        "http_version_generation", status == 200 and isinstance(doc, dict)
        and doc.get("generation_id") == generation and doc.get("policy_hash") == ctx["policy"]["policy_hash"],
        f"GET /api/version -> {status}, generation {'matches' if isinstance(doc, dict) and doc.get('generation_id') == generation else 'MISMATCH'}"))

    checks.append(check_served_store_consistency(ctx, base))

    status, doc = http_json(base + "/api/query?q=S-004&limit=3")
    ok = (status == 200 and isinstance(doc, dict) and doc.get("query_generation_id") == generation
          and doc.get("result_count", 99) <= 3 and all(r["source_id"] == "S-004" for r in doc.get("results", [])))
    checks.append(check_record("http_query_same_generation_bounded", ok,
                               f"GET /api/query?q=S-004&limit=3 -> {status}, {doc.get('result_count') if isinstance(doc, dict) else 'n/a'} results"))

    status, doc = http_json(base + "/api/query?expected_generation=" + "0" * 64)
    checks.append(check_record("negative_foreign_generation_refused",
                               status == 409 and isinstance(doc, dict) and doc.get("code") == "GENERATION_MISMATCH",
                               f"expected_generation=<foreign> -> {status}", negative_case="old_generation_refused"))

    status, doc = http_json(base + "/api/query?source_id=S-999")
    checks.append(check_record("negative_source_unavailable_distinct",
                               status == 200 and isinstance(doc, dict) and doc.get("data_status") == "SOURCE_NOT_AVAILABLE",
                               f"GET /api/query?source_id=S-999 -> {status} data_status={doc.get('data_status') if isinstance(doc, dict) else 'n/a'}",
                               negative_case="capability_or_source_unavailable_distinct"))

    status, doc = http_json(base + "/api/query?q=")
    expected_status, _ = qs.assess_scope(store, None, utcnow())
    checks.append(check_record("distinguishable_data_status",
                               status == 200 and isinstance(doc, dict)
                               and doc.get("data_status") == expected_status
                               and doc.get("total_matches", -1) >= 0
                               and "source_gaps" in doc,
                               f"data_status={doc.get('data_status') if isinstance(doc, dict) else 'n/a'} (expected {expected_status}), not collapsed into 'no events'",
                               negative_case="stale_or_partial_not_no_events"))

    status, doc = http_json(base + "/api/query?limit=0")
    first = status
    status2, _ = http_json(base + "/api/query?limit=500")
    checks.append(check_record("http_query_bounds_enforced",
                               first == 400 and status2 == 400,
                               f"limit=0 -> {first}, limit=500 -> {status2}"))

    status, doc = http_json(base + "/api/capability?id=traffic_events")
    checks.append(check_record("capability_not_available_typed",
                               status == 200 and isinstance(doc, dict) and doc.get("status") == "CAPABILITY_NOT_AVAILABLE",
                               f"GET /api/capability?id=traffic_events -> {status} {doc.get('status') if isinstance(doc, dict) else 'n/a'}",
                               negative_case="capability_not_available"))

    status, doc = http_json(base + "/api/health")
    checks.append(check_record("http_health_lanes",
                               status == 200 and isinstance(doc, dict)
                               and doc.get("lanes", {}).get("query") in ("HEALTHY", "DEGRADED", "BLOCKED", "UNKNOWN")
                               and doc.get("lanes", {}).get("publication") != "BLOCKED",
                               f"GET /api/health -> {status} lanes={doc.get('lanes') if isinstance(doc, dict) else 'n/a'}"))

    status, doc = http_json(base + "/api/version")
    before = doc.get("generation_id") if isinstance(doc, dict) else None
    mutated = dict(ctx["artifacts"]["feed"])
    mutated["items"] = mutated["items"][:-1]
    alt = qs.build_store(mutated, ctx["artifacts"]["status"], ctx["artifacts"]["brief"],
                         {"feed": "f" * 64, "status": ctx["hashes"]["status"], "brief": ctx["hashes"]["brief"]})
    store_file = ctx["serve_dir"] / "data" / "query-store.json"
    original = store_file.read_bytes()
    try:
        shutil.copy2(store_file, ctx["serve_dir"] / "data" / "query-store.archive.json")
        qs.atomic_write_json(store_file, alt)
        status, doc = http_json(base + "/api/query?expected_generation=" + generation)
        refused = status == 409 and isinstance(doc, dict) and doc.get("code") == "GENERATION_MISMATCH"
        status2, body = http_get(base + "/data/query-store.archive.json")
        archive_ok = status2 == 200 and json.loads(body).get("generation_id") == generation
        checks.append(check_record("negative_mixed_generation_refused_history_kept",
                                   refused and archive_ok and before == generation,
                                   f"cutover -> pinned query {status}, archive HTTP {status2}",
                                   negative_case="mixed_generation_atomic_refusal"))
        served_check = check_served_store_consistency(ctx, base)
        checks.append(check_record("negative_http_200_wrong_generation_detected",
                                   served_check["status"] == "FAIL",
                                   f"post-cutover served generation detected as {served_check['status']}",
                                   negative_case="http_200_wrong_version_not_publish"))
    finally:
        qs.atomic_write_json(store_file, json.loads(original))

    try:
        ctx["server"].query_down = True
        status, doc = http_json(base + "/api/query?q=x")
        down_api = status == 503 and isinstance(doc, dict) and doc.get("status") == "QUERY_UNAVAILABLE"
        status_static, _ = http_get(base + "/")
        status_feed, _ = http_get(base + "/data/intelligence-feed.json")
        status_health, health_doc = http_json(base + "/api/health")
        query_lane_blocked = isinstance(health_doc, dict) and health_doc.get("lanes", {}).get("query") == "BLOCKED"
        checks.append(check_record("negative_query_down_static_readable",
                                   down_api and status_static == 200 and status_feed == 200 and query_lane_blocked,
                                   f"query -> {status}, static -> {status_static}, feed -> {status_feed}, query lane={health_doc.get('lanes', {}).get('query') if isinstance(health_doc, dict) else 'n/a'}",
                                   negative_case="query_down_static_publication_readable"))
    finally:
        ctx["server"].query_down = False

    return checks


def run_browser_checks(ctx: dict[str, Any], base: str, evidence: Path, chrome_path: str | None) -> dict[str, Any]:
    driver = ROOT / "scripts" / "e2e-browser.mjs"
    if not driver.is_file():
        return check_record("browser_e2e", False, "scripts/e2e-browser.mjs missing")
    env = dict(os.environ)
    if chrome_path:
        env["GOVINTEL_CHROME_PATH"] = chrome_path
    try:
        result = run(["node", str(driver), "--base-url", base, "--out", str(evidence)], ROOT,
                     timeout=240)
    except FileNotFoundError:
        return check_record("browser_e2e", False, "node executable not found")
    except subprocess.TimeoutExpired:
        return check_record("browser_e2e", False, "browser e2e timed out")
    (evidence / "browser-stdout.log").parent.mkdir(parents=True, exist_ok=True)
    (evidence / "browser-stdout.log").write_text(result.stdout + "\n" + result.stderr, encoding="utf-8")
    try:
        doc = json.loads(result.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        return check_record("browser_e2e", False, f"browser driver exit {result.returncode}, no JSON result",
                            log="browser-stdout.log")
    return check_record("browser_e2e", result.returncode == 0 and doc.get("status") == "PASS",
                        f"{doc.get('passed', 0)}/{doc.get('total', 0)} browser assertions",
                        screenshots=doc.get("screenshots"), log="browser-stdout.log")


def run_sabotage_check(root: Path, evidence_dir: Path) -> dict[str, Any]:
    tmp = Path(tempfile.mkdtemp(prefix="govintel-sabotage-"))
    evidence_dir.mkdir(parents=True, exist_ok=True)
    try:
        for relative in SABOTAGE_FILES:
            source = root / relative
            if not source.is_file():
                continue
            target = tmp / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
        for artifact in (root / "apps" / "web" / "public" / "data").iterdir():
            if artifact.is_file():
                target = tmp / "apps" / "web" / "public" / "data" / artifact.name
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(artifact, target)
        victim = tmp / "scripts" / "query-store.py"
        text = victim.read_text(encoding="utf-8")
        marker = 'EXPECTED_SOURCES = frozenset(("S-004"'
        if marker not in text:
            return check_record("sabotage_detection", False, "EXPECTED_SOURCES anchor not found in query-store.py copy")
        victim.write_text(text.replace(marker, 'EXPECTED_SOURCES = frozenset(("S-999"', 1), encoding="utf-8")
        inner_out = tmp / "evidence"
        result = run([sys.executable, "-X", "utf8", str(tmp / "scripts" / "verify-current-checkout.py"),
                      "--mode", "core", "--skip-sabotage", "--output", str(inner_out)],
                     tmp, timeout=MAX_SABOTAGE_SECONDS)
        observed = "FAIL" if result.returncode != 0 else "PASS"
        receipt_path = inner_out / "receipt.json"
        receipt_status = None
        inner_failures: list[str] = []
        if receipt_path.is_file():
            inner_receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
            receipt_status = inner_receipt.get("status")
            inner_failures = [c["id"] for c in inner_receipt.get("checks", []) if c.get("status") == "FAIL"]
            shutil.copy2(receipt_path, evidence_dir / "sabotage-receipt.json")
        (evidence_dir / "sabotage-run.log").write_text(result.stdout + "\n" + result.stderr, encoding="utf-8")
        ok = result.returncode != 0 and receipt_status == "FAIL" and bool(inner_failures)
        return check_record("sabotage_detection", ok,
                            f"sabotaged copy exit={result.returncode}, inner receipt={receipt_status}, failed checks={inner_failures}",
                            sabotaged_module="scripts/query-store.py", expected="FAIL",
                            observed=observed, log="sabotage-run.log")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def run_publication_verifiers(root: Path, log_dir: Path) -> list[dict[str, Any]]:
    checks = []
    log_dir.mkdir(parents=True, exist_ok=True)
    for script in PUBLICATION_VERIFIERS:
        name = Path(script).stem
        result = run([sys.executable, "-X", "utf8", str(root / script)], root, timeout=300)
        (log_dir / f"{name}.log").write_text(result.stdout + "\n" + result.stderr, encoding="utf-8")
        checks.append(check_record(f"publication_verifier:{name}", result.returncode == 0,
                                   f"{script} exit={result.returncode}", log=f"logs/{name}.log"))
    return checks


def build_web_site(root: Path, log_dir: Path) -> dict[str, Any]:
    web = root / "apps" / "web"
    log_dir.mkdir(parents=True, exist_ok=True)
    if not (web / "node_modules").is_dir():
        install = run(["npm", "ci", "--no-audit", "--no-fund"], web, timeout=600)
        (log_dir / "npm-ci.log").write_text(install.stdout + "\n" + install.stderr, encoding="utf-8")
        if install.returncode != 0:
            return check_record("web_build", False, f"npm ci exit={install.returncode}", log="logs/npm-ci.log")
    env = dict(os.environ)
    env.pop("PAGES_BASE_PATH", None)
    env.pop("NEXT_PUBLIC_BASE_PATH", None)
    build = subprocess.run(["npm", "run", "build"], cwd=web, env=env,
                           capture_output=True, text=True, encoding="utf-8", timeout=600)
    (log_dir / "next-build.log").write_text(build.stdout + "\n" + build.stderr, encoding="utf-8")
    out = web / "out" / "index.html"
    return check_record("web_build", build.returncode == 0 and out.is_file(),
                        f"next build exit={build.returncode}, out/index.html {'present' if out.is_file() else 'missing'}",
                        log="logs/next-build.log")


def finalize_status(receipt: dict[str, Any]) -> str:
    checks = receipt.get("checks") or []
    if not checks:
        return "FAIL"
    if any(check.get("status") == "FAIL" for check in checks):
        return "FAIL"
    if not any(check.get("status") == "PASS" for check in checks):
        return "FAIL"
    return "PASS"


def build_health_receipt(ctx: dict[str, Any], http_checks: list[dict[str, Any]] | None) -> dict[str, Any]:
    health = ctx["modules"]["system_health"]
    status_doc = ctx["artifacts"]["status"]
    brief = ctx["artifacts"]["brief"]
    stages = health.current_publication_stages(status_doc, brief)
    stages = [s for s in stages if s["stage"] not in ("deployment", "public_http_verification", "query_index", "mcp_web_query")]
    verified = bool(http_checks) and all(c["status"] == "PASS" for c in http_checks)
    now = utcnow().isoformat()
    stages += [
        {"lane": "publication", "stage": "deployment",
         "outcome": "SUCCESS" if http_checks is not None else "SKIPPED",
         "generation_id": ctx["store"]["generation_id"], "ended_at": now,
         "detail": "LOOPBACK_HTTP_NOT_PUBLIC_DEPLOYMENT"},
        {"lane": "publication", "stage": "public_http_verification",
         "outcome": "SUCCESS" if verified else ("FAILED" if http_checks is not None else "SKIPPED"),
         "generation_id": ctx["store"]["generation_id"], "ended_at": now,
         "detail": "LOOPBACK_HASH_AND_GENERATION_VERIFIED"},
        {"lane": "query", "stage": "query_index",
         "outcome": "SUCCESS" if http_checks is not None else "SKIPPED",
         "generation_id": ctx["store"]["generation_id"], "ended_at": now},
        {"lane": "query", "stage": "mcp_web_query",
         "outcome": "SKIPPED", "error_class": "CAPABILITY_NOT_AVAILABLE"},
    ]
    return health.build_health(stages)


def parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Single-checkout GovIntel release candidate e2e verification")
    parser.add_argument("--mode", choices=("full", "core"), default="full")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--skip-browser", action="store_true")
    parser.add_argument("--skip-sabotage", action="store_true")
    parser.add_argument("--chrome-path")
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    started = utcnow()
    evidence = args.output or (ROOT / "runtime-evidence" / "current-checkout" / started.strftime("%Y%m%dT%H%M%SZ"))
    evidence = evidence.resolve()
    evidence.mkdir(parents=True, exist_ok=True)
    log_dir = evidence / "logs"

    checks: list[dict[str, Any]] = []
    not_run: list[dict[str, Any]] = []
    ctx = None
    http_checks = None

    try:
        modules = load_checkout_modules(ROOT)
        checks.append(check_record("modules_loaded_from_checkout", True,
                                   "query_store/system_health/source_policy imported from this checkout"))
    except Exception as error:
        modules = None
        checks.append(check_record("modules_loaded_from_checkout", False, f"{type(error).__name__}: {error}"))

    if modules is not None:
        try:
            ctx = build_candidate_context(ROOT, modules)
            ctx["test_mode"] = "CURRENT_CHECKOUT_LOOPBACK_HTTP" if args.mode == "full" else "CURRENT_CHECKOUT_CORE"
            checks.append(check_record("candidate_context", True,
                                       f"generation={ctx['store']['generation_id'][:12]}… policy=v{ctx['policy']['policy_version']}"))
        except Exception as error:
            checks.append(check_record("candidate_context", False, f"{type(error).__name__}: {error}"))

    if ctx is not None:
        checks.extend(run_publication_verifiers(ROOT, log_dir))
    else:
        not_run.append({"id": "publication_verifiers", "status": "NOT_RUN", "reason": "context failed"})

    server = None
    if args.mode == "full" and ctx is not None:
        build = build_web_site(ROOT, log_dir)
        checks.append(build)
        serve_dir = None
        if build["status"] == "PASS":
            serve_dir = prepare_serve_dir(ctx, evidence / "serve", ROOT / "apps" / "web" / "out")
            ctx["serve_dir"] = serve_dir
            server, base = start_server(ctx, serve_dir)
            ctx["server"] = server
            try:
                try:
                    http_checks = run_http_checks(ctx, base)
                    checks.extend(http_checks)
                except Exception as error:
                    checks.append(check_record("http_checks", False, f"{type(error).__name__}: {error}"))
                if args.skip_browser:
                    not_run.append({"id": "browser_e2e", "status": "NOT_RUN", "reason": "--skip-browser"})
                else:
                    checks.append(run_browser_checks(ctx, base, evidence / "browser", args.chrome_path))
            finally:
                server.shutdown()
                server.server_close()
                with server.request_lock:
                    request_log = list(server.request_log)
                write_json(evidence / "request-log.json", {"requests": request_log})
        else:
            not_run.append({"id": "http_checks", "status": "NOT_RUN", "reason": "web build failed"})
            not_run.append({"id": "browser_e2e", "status": "NOT_RUN", "reason": "web build failed"})
    elif ctx is not None:
        not_run.append({"id": "web_build", "status": "NOT_RUN", "reason": "core mode"})
        not_run.append({"id": "http_checks", "status": "NOT_RUN", "reason": "core mode"})
        not_run.append({"id": "browser_e2e", "status": "NOT_RUN", "reason": "core mode"})

    if args.skip_sabotage:
        not_run.append({"id": "sabotage_detection", "status": "NOT_RUN", "reason": "--skip-sabotage"})
    elif ctx is not None:
        checks.append(run_sabotage_check(ROOT, evidence))
    else:
        not_run.append({"id": "sabotage_detection", "status": "NOT_RUN", "reason": "context failed"})

    receipt: dict[str, Any] = {
        "schema_version": 1,
        "kind": "GOVINTEL_CURRENT_CHECKOUT_CANDIDATE_RECEIPT",
        "candidate_id": ctx["candidate_id"] if ctx else None,
        "code_sha": ctx["identity"]["code_sha"] if ctx else None,
        "worktree_dirty": ctx["identity"]["worktree_dirty"] if ctx else None,
        "dependency_lock_hash": ctx["identity"]["dependency_lock_hash"] if ctx else None,
        "dependency_lock_files": ctx["identity"]["dependency_lock_files"] if ctx else [],
        "missing_lock_files": ctx["identity"]["missing_lock_files"] if ctx else list(LOCKFILES),
        "enabled_capabilities": ctx["enabled_capabilities"] if ctx else [],
        "unavailable_capabilities": ctx["unavailable_capabilities"] if ctx else [],
        "publication": {
            "collection_run_id": ctx["artifacts"]["feed"].get("collection_run_id") if ctx else None,
            "generated_at": ctx["artifacts"]["feed"].get("generated_at") if ctx else None,
            "feed_sha256": ctx["hashes"]["feed"] if ctx else None,
            "status_sha256": ctx["hashes"]["status"] if ctx else None,
            "brief_sha256": ctx["hashes"]["brief"] if ctx else None,
            "publication_status": ctx["artifacts"]["brief"].get("publication_status") if ctx else None,
            "snapshot_complete": ctx["artifacts"]["brief"].get("snapshot_complete") if ctx else None,
            "snapshot_label": "PRESERVED_SNAPSHOT_NOT_LIVE",
        } if ctx else None,
        "query_store": {
            "generation_id": ctx["store"]["generation_id"] if ctx else None,
            "projection_sha256": ctx["store"]["projection_sha256"] if ctx else None,
            "schema_version": ctx["store"]["schema_version"] if ctx else None,
            "projection_version": ctx["store"]["projection_version"] if ctx else None,
            "counts": ctx["store"]["counts"] if ctx else None,
        } if ctx else None,
        "source_policy": {
            "policy_version": ctx["policy"]["policy_version"] if ctx else None,
            "policy_hash": ctx["policy"]["policy_hash"] if ctx else None,
            "catalog_hash": ctx["policy"]["catalog_hash"] if ctx else None,
            "active_source_ids": ctx["policy"]["active_source_ids"] if ctx else None,
        } if ctx else None,
        "test_mode": ctx["test_mode"] if ctx else "CURRENT_CHECKOUT_FAILED_EARLY",
        "checks": checks,
        "not_run": not_run,
        "health_receipt": build_health_receipt(ctx, http_checks) if ctx else None,
        "historical_pinned_replay": {
            "status": "SEPARATE_LANE",
            "evidence": "scripts/verify-backbone-runtime.py + config/backbone-runtime.v1.json (PR #46)",
            "note": "Pinned-SHA replay PASS never substitutes for current-checkout checks.",
            "production_verified": False,
        },
        "limitations": [
            "Loopback HTTP deployment only; no public Pages deployment verified",
            "Checked-in preserved snapshot, not live data; external source failures do not affect this run",
            "Capabilities listed under unavailable_capabilities are CAPABILITY_NOT_AVAILABLE in this candidate",
            "No credentials are read or persisted",
        ],
        "production_verified": False,
        "started_at": started.isoformat(),
        "verified_at": utcnow().isoformat(),
        "evidence_dir": str(evidence),
    }
    receipt["status"] = finalize_status(receipt)
    write_json(evidence / "receipt.json", receipt)
    print(f"CURRENT_CHECKOUT_RC status={receipt['status']} candidate_id={receipt['candidate_id']} "
          f"checks={sum(1 for c in checks if c['status'] == 'PASS')}/{len(checks)} evidence={evidence}")
    return 0 if receipt["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
