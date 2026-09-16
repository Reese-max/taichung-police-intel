#!/usr/bin/env python3
"""One bounded, read-only observation of candidate news collectors; no promotion."""
from __future__ import annotations
import argparse
from datetime import datetime, timedelta
import json
from pathlib import Path
import re
import sys
import time
from urllib.parse import urljoin, urlsplit

ROOT = Path(__file__).resolve().parents[1]
SOURCES = ("S-001", "S-019", "S-032")


class BoundedSession:
    """No implicit retries/credentials, bounded same-host redirects and response bytes."""
    def __init__(self, source_url, transport=None):
        import requests
        self.transport = transport or requests.Session()
        self.transport.trust_env = False
        self.transport.headers.update({"User-Agent": "GovIntelCandidateCanary/1.0 (+public-source-monitor)",
                                       "Accept-Language": "zh-TW"})
        host = urlsplit(source_url).hostname
        self.hosts = {host, host.removeprefix("www."), "www." + host.removeprefix("www.")}
        self.calls = 0
        self.last_http_status = None

    def get(self, url, **kwargs):
        kwargs.pop("timeout", None)
        for _ in range(3):
            parts = urlsplit(url)
            if (parts.scheme != "https" or parts.hostname not in self.hosts or
                    parts.username or parts.password or parts.port not in (None, 443)):
                raise ValueError("unapproved candidate source URL")
            if self.calls >= 6:
                raise RuntimeError("candidate HTTP budget exhausted")
            self.calls += 1
            response = self.transport.get(url, timeout=(5, 15), allow_redirects=False, stream=True, **kwargs)
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


def run_canary(collector, sources, now, *, session_factory=BoundedSession):
    if not sources or len(sources) != len(set(sources)) or any(s not in SOURCES for s in sources):
        raise ValueError("select a nonempty unique subset of the approved candidate IDs")
    if now.tzinfo is None:
        raise ValueError("canary clock must be timezone-aware")
    records = []
    for source_id in sources:
        url = collector.NEWS_LIST_SOURCES[source_id]["list_url"]
        session = session_factory(url)
        start = time.monotonic()
        record = {"source_id": source_id, "source_url": url, "integration_status": "CANDIDATE",
                  "promotion_eligible": False, "observed_at": now.isoformat()}
        try:
            result = collector.collect_source(session, source_id, now.date() - timedelta(days=6),
                                              now.date(), {}, max_details=1)
            manifest = result.get("manifest_sha256", "")
            if not re.fullmatch(r"[0-9a-f]{64}", manifest):
                raise ValueError("missing collector manifest")
            if result.get("window_completeness") not in ("COMPLETE_ZERO", "COMPLETE_WITH_ITEMS", "PARTIAL"):
                raise ValueError("unknown collector coverage claim")
            record.update({"source_health": result["source_health"],
                           "collector_window_claim": result["window_completeness"],
                           "window_item_count": result["window_item_count"],
                           "snapshot_item_count": result["snapshot_item_count"],
                           "snapshot_count": len(result["snapshots"]),
                           "detail_fetch_count": sum(s["purpose"] == "DETAIL" for s in result["snapshots"]),
                           "manifest_sha256": manifest,
                           "coverage_independently_verified": False})
            if record["source_health"] not in ("PASS", "DEGRADED") or record["detail_fetch_count"] > 1:
                raise ValueError("collector violated the bounded candidate contract")
        except Exception as error:
            record.update({"source_health": "FAILED", "collector_window_claim": "PARTIAL",
                           "window_item_count": None, "snapshot_item_count": None,
                           "snapshot_count": None, "detail_fetch_count": None,
                           "manifest_sha256": None, "error_type": type(error).__name__})
        finally:
            record["http_calls"] = session.calls
            record["last_http_status"] = getattr(session, "last_http_status", None)
            record["elapsed_ms"] = round((time.monotonic() - start) * 1000)
            session.close()
        records.append(record)
    failed = sum(r["source_health"] == "FAILED" for r in records)
    return {"schema_version": 1, "validation_scope": "SINGLE_OBSERVATION_NOT_PROMOTION",
            "observed_at": now.isoformat(), "source_count": len(records), "failed_count": failed,
            "status": "FAILED" if failed else "OBSERVED", "sources": records}


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", action="append", choices=SOURCES)
    parser.add_argument("--output", required=True)
    args = parser.parse_args(argv)
    sys.path.insert(0, str(ROOT))
    import online_collect as collector
    report = run_canary(collector, args.source or list(SOURCES), datetime.now(collector.TZ))
    path = Path(args.output)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 1 if report["failed_count"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
