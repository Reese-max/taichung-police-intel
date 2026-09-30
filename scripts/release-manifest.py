#!/usr/bin/env python3
"""Build and validate the Pages/Worker release binding manifest.

The manifest is deliberately derived from the exact bytes shipped by Pages.
It is a release receipt, not a claim that a deployment or anonymous HTTP smoke
has happened; those fields remain null until the deployment workflow records
them.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATA_DIR = ROOT / "apps" / "web" / "public" / "data"
DEFAULT_OUTPUT = DEFAULT_DATA_DIR / "release.json"
ARTIFACTS = {
    "feed": "intelligence-feed.json",
    "status": "source-status.json",
    "brief": "v2-daily-brief.json",
}
POLICY_FILE = "source-policy.json"
HEX64 = re.compile(r"^[0-9a-f]{64}$")
HEX40 = re.compile(r"^[0-9a-f]{40}$")
ISO_WITH_ZONE = re.compile(r"(?:Z|[+-]\d{2}:\d{2})$")


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def sha256_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def sha256_json(value: Any) -> str:
    return sha256_bytes(canonical_json(value).encode("utf-8"))


def _read_json(data_dir: Path, name: str) -> tuple[dict[str, Any], bytes]:
    path = data_dir / name
    raw = path.read_bytes()
    value = json.loads(raw.decode("utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{name} must contain a JSON object")
    return value, raw


def _publication(data_dir: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    documents: dict[str, dict[str, Any]] = {}
    raw_by_name: dict[str, bytes] = {}
    for name in (*ARTIFACTS.values(), POLICY_FILE):
        documents[name], raw_by_name[name] = _read_json(data_dir, name)

    feed = documents[ARTIFACTS["feed"]]
    status = documents[ARTIFACTS["status"]]
    brief = documents[ARTIFACTS["brief"]]
    policy = documents[POLICY_FILE]
    run = feed.get("collection_run_id")
    if not isinstance(run, str) or not run:
        raise ValueError("publication_generation is missing from intelligence-feed.json")
    if run != (status.get("latest_collection_run") or {}).get("collection_run_id"):
        raise ValueError("publication_generation mismatch between feed and source status")
    if run != brief.get("source_collection_run_id"):
        raise ValueError("publication_generation mismatch between feed and brief")
    generated_at = feed.get("generated_at")
    if generated_at != status.get("generated_at") or generated_at != brief.get("source_status_generated_at"):
        raise ValueError("publication generated_at mismatch")
    policy_hash = policy.get("policy_hash")
    if not isinstance(policy_hash, str) or not HEX64.fullmatch(policy_hash):
        raise ValueError("source policy hash is invalid")
    active_source_ids = policy.get("active_source_ids")
    if not isinstance(active_source_ids, list) or active_source_ids != sorted(set(active_source_ids)) or not active_source_ids:
        raise ValueError("source policy active_source_ids must be sorted and non-empty")

    hashes = {key: sha256_bytes(raw_by_name[name]) for key, name in ARTIFACTS.items()}
    hashes["policy"] = sha256_bytes(raw_by_name[POLICY_FILE])
    return {
        "feed": feed,
        "status": status,
        "brief": brief,
        "policy": policy,
        "hashes": hashes,
    }, raw_by_name


def _stable_evidence_catalog(feed: dict[str, Any], status: dict[str, Any]) -> list[dict[str, Any]]:
    catalog = []
    source_freshness = {row["source_id"]: row.get("freshness_status") for row in status.get("sources", [])}
    for item in feed.get("items", []):
        official_url = item.get("official_url")
        stable_id = item.get("stable_id")
        if not isinstance(stable_id, str) or not stable_id or not isinstance(official_url, str) or not official_url.startswith("https://"):
            continue
        content_hash = item.get("content_sha256")
        if not isinstance(content_hash, str) or not HEX64.fullmatch(content_hash):
            raise ValueError(f"invalid content_sha256 for {stable_id}")
        freshness = str(item.get("freshness_status") or source_freshness.get(item.get("source_id")) or "UNKNOWN").upper()
        catalog.append({
            "schema_version": 1,
            "evidence_id": f"PUB-{stable_id}",
            "evidence_type": "WRITTEN_OFFICIAL",
            "source_id": item.get("source_id"),
            "locator": f"{official_url}#publication:{stable_id}",
            "document_version": content_hash,
            "content_sha256": content_hash,
            "trust_tier": "CANONICAL_PUBLICATION",
            "verification_status": "CONFIRMED_OFFICIAL",
            "freshness": freshness,
            "published_at": item.get("published_at") or item.get("data_as_of") or item.get("fetched_at"),
            "assertions": [
                {"subject": f"publication:{stable_id}:title", "value": item.get("title")},
                {"subject": f"publication:{stable_id}:source_id", "value": item.get("source_id")},
            ],
        })
    return sorted(catalog, key=lambda row: row["evidence_id"].encode("utf-16-be"))


def _binding_material(publication: dict[str, Any], evidence_catalog_hash: str, worker_version: str) -> tuple[dict[str, Any], str, str]:
    policy = publication["policy"]
    binding = {
        "policy_version": policy["policy_version"],
        "policy_hash": policy["policy_hash"],
        "catalog_hash": policy["catalog_hash"],
        "active_source_ids": sorted(policy["active_source_ids"]),
    }
    return {
        "schema_version": 2,
        "projection_version": "publication-metadata-v2",
        "artifact_hashes": {
            "feed": publication["hashes"]["feed"],
            "status": publication["hashes"]["status"],
            "brief": publication["hashes"]["brief"],
        },
        "policy": binding,
    }, evidence_catalog_hash, worker_version


def build_manifest(
    data_dir: Path,
    *,
    code_sha: str,
    worker_version: str,
    pages_deployment: dict[str, Any],
    built_at: str,
    deployed_at: str | None = None,
    anonymous_http_verified_at: str | None = None,
) -> dict[str, Any]:
    if not HEX40.fullmatch(code_sha):
        raise ValueError("code_sha must be a 40-character commit SHA")
    if not worker_version:
        raise ValueError("worker_version is required")
    publication, _ = _publication(Path(data_dir))
    catalog = _stable_evidence_catalog(publication["feed"], publication["status"])
    evidence_catalog_hash = sha256_json(catalog)
    material, _, _ = _binding_material(publication, evidence_catalog_hash, worker_version)
    query_generation = sha256_json(material)
    identity = {
        "code_sha": code_sha,
        "publication_generation": publication["feed"]["collection_run_id"],
        "publication_hash": publication["hashes"]["brief"],
        "source_policy_hash": publication["policy"]["policy_hash"],
        "query_generation": query_generation,
        "evidence_catalog_hash": evidence_catalog_hash,
        "worker_version": worker_version,
    }
    manifest = {
        "schema_version": 1,
        "kind": "GOVINTEL_RELEASE_MANIFEST",
        "release_id": sha256_json(identity),
        **identity,
        "artifact_hashes": {
            "feed": publication["hashes"]["feed"],
            "status": publication["hashes"]["status"],
            "brief": publication["hashes"]["brief"],
            "source_policy": publication["hashes"]["policy"],
        },
        "pages_deployment": pages_deployment,
        "built_at": built_at,
        "deployed_at": deployed_at,
        "anonymous_http_verified_at": anonymous_http_verified_at,
    }
    validate_manifest(manifest)
    return manifest


def _require_iso(value: Any, field: str, *, nullable: bool = False) -> None:
    if value is None and nullable:
        return
    if not isinstance(value, str) or not ISO_WITH_ZONE.search(value) or not isinstance(datetime.fromisoformat(value.replace("Z", "+00:00")), datetime):
        raise ValueError(f"{field} must be an ISO-8601 timestamp with timezone")


def validate_manifest(manifest: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(manifest, dict) or manifest.get("schema_version") != 1 or manifest.get("kind") != "GOVINTEL_RELEASE_MANIFEST":
        raise ValueError("invalid release manifest schema")
    for field in ("release_id", "publication_hash", "source_policy_hash", "query_generation", "evidence_catalog_hash"):
        if not isinstance(manifest.get(field), str) or not HEX64.fullmatch(manifest[field]):
            raise ValueError(f"{field} must be a SHA-256 hex digest")
    if not isinstance(manifest.get("code_sha"), str) or not HEX40.fullmatch(manifest["code_sha"]):
        raise ValueError("code_sha must be a 40-character commit SHA")
    if not isinstance(manifest.get("publication_generation"), str) or not manifest["publication_generation"]:
        raise ValueError("publication_generation is required")
    if not isinstance(manifest.get("worker_version"), str) or not manifest["worker_version"]:
        raise ValueError("worker_version is required")
    hashes = manifest.get("artifact_hashes")
    if not isinstance(hashes, dict):
        raise ValueError("artifact_hashes is required")
    for field in ("feed", "status", "brief", "source_policy"):
        if not isinstance(hashes.get(field), str) or not HEX64.fullmatch(hashes[field]):
            raise ValueError(f"artifact_hashes.{field} must be a SHA-256 hex digest")
    if not isinstance(manifest.get("pages_deployment"), dict) or not isinstance(manifest["pages_deployment"].get("status"), str):
        raise ValueError("pages_deployment.status is required")
    _require_iso(manifest.get("built_at"), "built_at")
    _require_iso(manifest.get("deployed_at"), "deployed_at", nullable=True)
    _require_iso(manifest.get("anonymous_http_verified_at"), "anonymous_http_verified_at", nullable=True)
    identity = {key: manifest[key] for key in ("code_sha", "publication_generation", "publication_hash", "source_policy_hash", "query_generation", "evidence_catalog_hash", "worker_version")}
    expected_release_id = sha256_json(identity)
    if manifest["release_id"] != expected_release_id:
        raise ValueError("release_id does not match the release identity")
    return manifest


def validate_binding(manifest: dict[str, Any], expected: dict[str, Any]) -> dict[str, Any]:
    validate_manifest(manifest)
    for field in ("publication_generation", "publication_hash", "source_policy_hash", "query_generation", "evidence_catalog_hash", "worker_version"):
        if manifest.get(field) != expected.get(field):
            raise ValueError(f"{field} mismatch")
    if manifest.get("artifact_hashes") != expected.get("artifact_hashes"):
        raise ValueError("artifact_hashes mismatch")
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    build = subparsers.add_parser("build")
    build.add_argument("--data-dir", type=Path, default=DEFAULT_DATA_DIR)
    build.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    build.add_argument("--code-sha", default=os.environ.get("GITHUB_SHA", ""))
    build.add_argument("--worker-version", default="query-gateway-v1-workers")
    build.add_argument("--pages-url", default=os.environ.get("PAGES_DEPLOYMENT_URL"))
    build.add_argument("--deployment-id", default=os.environ.get("GITHUB_RUN_ID"))
    build.add_argument("--built-at", default=os.environ.get("RELEASE_BUILT_AT"))
    build.add_argument("--deployed-at", default=None)
    build.add_argument("--anonymous-http-verified-at", default=None)
    verify = subparsers.add_parser("verify")
    verify.add_argument("manifest", type=Path, default=DEFAULT_OUTPUT, nargs="?")
    args = parser.parse_args()
    if args.command == "verify":
        manifest = validate_manifest(json.loads(args.manifest.read_text(encoding="utf-8")))
        expected = build_manifest(
            args.manifest.parent, code_sha=manifest["code_sha"], worker_version=manifest["worker_version"],
            pages_deployment=manifest["pages_deployment"], built_at=manifest["built_at"],
        )
        validate_binding(manifest, expected)
        print(f"RELEASE_MANIFEST_OK path={args.manifest}")
        return 0
    built_at = args.built_at or datetime.now(timezone.utc).isoformat()
    manifest = build_manifest(
        args.data_dir,
        code_sha=args.code_sha,
        worker_version=args.worker_version,
        pages_deployment={
            "provider": "github-pages",
            "status": "PENDING",
            "url": args.pages_url,
            "deployment_id": args.deployment_id,
        },
        built_at=built_at,
        deployed_at=args.deployed_at,
        anonymous_http_verified_at=args.anonymous_http_verified_at,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print("RELEASE_MANIFEST_BUILT " + json.dumps(manifest, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
