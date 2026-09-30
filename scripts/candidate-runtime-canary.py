#!/usr/bin/env python3
"""Run one bounded, read-only observation of catalog-approved news candidates."""
from __future__ import annotations

import argparse
import importlib.util
from datetime import datetime, timedelta
import json
from pathlib import Path
import re
import sys
import time
from urllib.parse import urljoin, urlsplit

ROOT = Path(__file__).resolve().parents[1]
CANDIDATE_STATUSES = {"AUDITED_EXISTING", "VERIFIED_CANDIDATE"}


def _load_policy_module():
    policy_path = ROOT / "scripts/source-policy.py"
    spec = importlib.util.spec_from_file_location("govintel_source_policy", policy_path)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load source policy")
    policy = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(policy)
    return policy


def catalog_source_rows() -> dict[str, dict]:
    return {row["source_id"]: row for row in _load_policy_module().load_catalog()["sources"]}


def candidate_source_ids(collector) -> tuple[str, ...]:
    """Derive the bounded list from the canonical source catalog."""
    catalog_ids = {
        row["source_id"]
        for row in _load_policy_module().load_catalog()["sources"]
        if row["status"] in CANDIDATE_STATUSES
    }
    return tuple(sorted(catalog_ids & set(collector.NEWS_LIST_SOURCES)))


class BoundedSession:
    """Allow only same-host HTTPS requests with small call and body budgets."""

    def __init__(self, source_url, transport=None):
        if transport is None:
            from online_collect import http_session

            transport = http_session()
        self.transport = transport
        self.transport.trust_env = False
        self.transport.headers.update({
            "User-Agent": "GovIntelCandidateCanary/1.0 (+public-source-monitor)",
            "Accept-Language": "zh-TW",
        })
        host = urlsplit(source_url).hostname
        if not host:
            raise ValueError("candidate source URL has no host")
        base_host = host.removeprefix("www.")
        self.hosts = {base_host, f"www.{base_host}"}
        self.calls = 0
        self.last_http_status = None

    def get(self, url, **kwargs):
        kwargs.pop("timeout", None)
        for _ in range(3):
            parts = urlsplit(url)
            if (
                parts.scheme != "https"
                or parts.hostname not in self.hosts
                or parts.username
                or parts.password
                or parts.port not in (None, 443)
            ):
                raise ValueError("unapproved candidate source URL")
            if self.calls >= 6:
                raise RuntimeError("candidate HTTP budget exhausted")
            self.calls += 1
            kwargs.pop("allow_redirects", None)
            response = self.transport.get(
                url, timeout=(5, 15), allow_redirects=False, stream=True, **kwargs
            )
            self.last_http_status = response.status_code
            if response.status_code in (301, 302, 303, 307, 308):
                location = response.headers.get("location")
                response.close()
                if not location:
                    raise ValueError("redirect without location")
                url = urljoin(url, location)
                kwargs.pop("params", None)
                continue
            try:
                response.raise_for_status()
                body = bytearray()
                for chunk in response.iter_content(65536):
                    body.extend(chunk)
                    if len(body) > 2 * 1024 * 1024:
                        raise RuntimeError("candidate response byte budget exceeded")
                response._content = bytes(body)
                response._content_consumed = True
                return response
            finally:
                response.close()
        raise RuntimeError("candidate redirect budget exhausted")

    def close(self):
        self.transport.close()


def run_canary(collector, sources, now, *, session_factory=BoundedSession, existing_items=None):
    allowed = set(candidate_source_ids(collector))
    if not sources or len(sources) != len(set(sources)) or any(source not in allowed for source in sources):
        raise ValueError("select a nonempty unique subset of catalog-approved candidate IDs")
    if now.tzinfo is None:
        raise ValueError("canary clock must be timezone-aware")

    catalog = catalog_source_rows()
    existing_items = existing_items or {}
    item_state: dict[str, dict[str, str]] = {}
    records = []
    drift = None
    try:
        drift_path = ROOT / "scripts" / "schema_drift.py"
        drift_spec = importlib.util.spec_from_file_location("govintel_schema_drift", drift_path)
        if drift_spec is not None and drift_spec.loader is not None:
            drift = importlib.util.module_from_spec(drift_spec)
            drift_spec.loader.exec_module(drift)
    except Exception:
        drift = None
    for source_id in sources:
        url = collector.NEWS_LIST_SOURCES[source_id]["list_url"]
        session = session_factory(url)
        started = time.monotonic()
        catalog_row = catalog.get(source_id, {})
        record = {
            "source_id": source_id,
            "source_url": url,
            "source_role": catalog_row.get("role"),
            "integration_status": "CANDIDATE",
            "catalog_status": catalog_row.get("status"),
            "promotion_eligible": False,
            "observed_at": now.isoformat(),
        }
        for field in ("public_usage_notice", "retention_class"):
            if catalog_row.get(field):
                record[field] = catalog_row[field]
        try:
            # Unchanged list rows are diffed against the previous observation so
            # only new/changed stable IDs cost a detail-page request.
            existing = dict(existing_items.get(source_id) or {})
            result = collector.collect_source(
                session,
                source_id,
                now.date() - timedelta(days=6),
                now.date(),
                existing,
                max_details=1,
            )
            manifest = result.get("manifest_sha256", "")
            if not re.fullmatch(r"[0-9a-f]{64}", manifest):
                raise ValueError("missing collector manifest")
            if result.get("window_completeness") not in {
                "COMPLETE_ZERO",
                "COMPLETE_WITH_ITEMS",
                "PARTIAL",
            }:
                raise ValueError("unknown collector coverage claim")
            record.update({
                "source_health": result["source_health"],
                "collector_window_claim": result["window_completeness"],
                "window_item_count": result["window_item_count"],
                "snapshot_item_count": result["snapshot_item_count"],
                "snapshot_count": len(result["snapshots"]),
                "detail_fetch_count": sum(
                    snapshot["purpose"] == "DETAIL" for snapshot in result["snapshots"]
                ),
                "unchanged_detail_skips": sum(
                    1
                    for item in result.get("items", [])
                    if isinstance(item.get("payload"), dict)
                    and item["payload"].get("detail") == "unchanged-skipped"
                ),
                "manifest_sha256": manifest,
                "coverage_independently_verified": False,
            })
            item_state[source_id] = {
                item["stable_key"]: item["content_sha256"]
                for item in result.get("items", [])
                if item.get("stable_key") and item.get("content_sha256")
            }
            first_snapshot = next((item for item in result["snapshots"] if item.get("purpose") == "LIST"), None)
            if drift is not None and first_snapshot and "body" in first_snapshot:
                contract = drift.CONTRACTS[source_id]
                contract_result = drift.observe(
                    contract,
                    first_snapshot["body"],
                    http_status=first_snapshot.get("http_status", 200),
                    content_type=first_snapshot.get("content_type", ""),
                    requested_url=first_snapshot.get("requested_url"),
                    final_url=first_snapshot.get("final_url"),
                    observed_at=now.isoformat(),
                )
                record["schema_contract"] = {
                    key: contract_result.get(key)
                    for key in ("contract_version", "parser_version", "transport", "status", "observed_schema_fingerprint", "sample_sha256", "reasons", "review_required")
                }
                if contract_result["status"] not in drift.GOOD_STATUSES:
                    raise ValueError(f"schema contract {contract_result['status']}")
            else:
                record["schema_contract"] = {
                    "status": "CONTENT_SHAPE_UNKNOWN",
                    "reasons": ["NO_SNAPSHOT_BODY"],
                    "review_required": False,
                }
            if record["source_health"] not in ("PASS", "DEGRADED") or record["detail_fetch_count"] > 1:
                raise ValueError("collector violated the bounded candidate contract")
        except Exception as error:
            record.update({
                "source_health": "FAILED",
                "collector_window_claim": "PARTIAL",
                "window_item_count": None,
                "snapshot_item_count": None,
                "snapshot_count": None,
                "detail_fetch_count": None,
                "manifest_sha256": None,
                "error_type": type(error).__name__,
                "error_message": str(error).strip()[:256],
                "schema_contract": {
                    "status": "CONTENT_SHAPE_UNKNOWN",
                    "reasons": ["COLLECTOR_DID_NOT_RETURN_VERIFIABLE_SNAPSHOT"],
                    "review_required": False,
                },
            })
        finally:
            record["http_calls"] = session.calls
            record["last_http_status"] = getattr(session, "last_http_status", None)
            record["elapsed_ms"] = round((time.monotonic() - started) * 1000)
            session.close()
        records.append(record)

    failed = sum(record["source_health"] == "FAILED" for record in records)
    return {
        "schema_version": 1,
        "validation_scope": "SINGLE_OBSERVATION_NOT_PROMOTION",
        "observed_at": now.isoformat(),
        "source_count": len(records),
        "failed_count": failed,
        "status": "FAILED" if failed else "OBSERVED",
        "sources": records,
        "item_state": item_state,
    }


def load_existing_items(path: Path | None) -> dict:
    """Read a prior observation report into the collector's existing-item map."""
    if path is None or not path.exists():
        return {}
    document = json.loads(path.read_text(encoding="utf-8"))
    state = document.get("item_state") if isinstance(document, dict) else None
    if not isinstance(state, dict):
        return {}
    existing = {}
    for source_id, items in state.items():
        if not isinstance(items, dict):
            continue
        existing[source_id] = {
            str(stable_key): {"content_sha256": content_sha256}
            for stable_key, content_sha256 in items.items()
            if isinstance(content_sha256, str)
        }
    return existing


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", action="append")
    parser.add_argument("--output", required=True)
    parser.add_argument(
        "--existing",
        type=Path,
        help="Previous observation report whose item_state seeds the incremental diff.",
    )
    args = parser.parse_args(argv)
    sys.path.insert(0, str(ROOT))
    import online_collect as collector

    report = run_canary(
        collector,
        args.source or list(candidate_source_ids(collector)),
        datetime.now(collector.TZ),
        existing_items=load_existing_items(args.existing),
    )
    path = Path(args.output)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 1 if report["failed_count"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
