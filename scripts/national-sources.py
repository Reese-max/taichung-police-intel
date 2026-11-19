#!/usr/bin/env python3
"""Validate the national registry without touching the governance catalog."""
import argparse
from datetime import date
import json
import math
from pathlib import Path
import re
from urllib.parse import unquote, urlsplit

REGISTRY = Path(__file__).resolve().parents[1] / "docs/govintel/national-sources.v1.json"
PUBLIC_FIELDS = {"title", "case_number", "event_date", "jurisdiction", "authority",
                 "official_url", "provider_url", "license"}
REQUIRED = set("national_source_id provider official_authority tier transport endpoints allowed_hosts "
               "official_url_hosts robots min_request_interval_seconds license license_source_url "
               "rights_class rights_note status public_fields required_secret scope capacity_estimate_mb verified_at notes "
               "runner documented_open_api".split())


def check_direct_sources(value):
    if isinstance(value, dict):
        if value.get("status") == "BLOCKED_BY_ROBOTS_NEEDS_CONSENT" and value.get("endpoints") != []:
            raise ValueError("robots-blocked source must have empty endpoints")
        for item in value.values():
            check_direct_sources(item)
    elif isinstance(value, list):
        for item in value:
            check_direct_sources(item)
    elif isinstance(value, str):
        for host in re.findall(r"[a-z0-9.-]+\.[a-z]{2,}", value.lower()):
            if any(part in host for part in ("govapi.tw", "openfun", "huggingface.co")):
                raise ValueError(f"non-official host forbidden: {host}")


def https_url(value, *, origin=False):
    if not isinstance(value, str):
        raise ValueError("URL must be a string")
    parsed = urlsplit(value)
    host = parsed.hostname or ""
    if (parsed.scheme != "https" or parsed.port not in (None, 443)
            or parsed.username is not None or parsed.password is not None
            or not re.fullmatch(r"(?:[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.)+[a-z]{2,}", host)
            or parsed.fragment or any(c.isspace() for c in value)
            or (origin and (parsed.path not in ("", "/") or parsed.query))):
        raise ValueError(f"expected HTTPS domain{' origin' if origin else ''}: {value}")
    return host


def validate(registry):
    check_direct_sources(registry)
    if registry.get("schema_version") != 1 or registry.get("capacity_limit_mb") != 400:
        raise ValueError("unsupported registry version or capacity guard")
    rows = registry.get("sources")
    if not isinstance(rows, list) or not rows:
        raise ValueError("sources must be a nonempty list")
    seen = set()
    total = registry.get("capacity_reserve_mb", 0)
    if type(total) not in (int, float) or not math.isfinite(total) or total < 0:
        raise ValueError("invalid capacity reserve")
    unknown = 0
    for row in rows:
        missing = REQUIRED - row.keys()
        if missing:
            raise ValueError(f"missing fields: {sorted(missing)}")
        sid = row["national_source_id"]
        if not isinstance(sid, str) or not re.fullmatch(r"N-[A-Z0-9]+(?:-[A-Z0-9]+)*", sid) or sid in seen:
            raise ValueError("invalid or duplicate national_source_id")
        seen.add(sid)
        if row["runner"] not in ("github-actions", "xisx-pc"):
            raise ValueError(f"invalid runner: {sid}")
        if type(row["documented_open_api"]) is not bool:
            raise ValueError(f"documented_open_api must be boolean: {sid}")
        if row["documented_open_api"] and row["status"] == "BLOCKED_BY_ROBOTS_NEEDS_CONSENT":
            raise ValueError(f"documented_open_api cannot override robots block: {sid}")
        if row["tier"] not in {"REFERENCE", "DISCOVERY_ONLY"}:
            raise ValueError(f"invalid tier: {sid}")
        if row["required_secret"] not in (None, "TDX_CLIENT_ID+TDX_CLIENT_SECRET"):
            raise ValueError(f"invalid required_secret: {sid}")
        for key in ("provider", "official_authority", "transport", "license", "rights_class", "rights_note", "status"):
            if not isinstance(row[key], str) or not row[key].strip():
                raise ValueError(f"invalid {key}: {sid}")
        for key in ("endpoints", "allowed_hosts", "official_url_hosts", "public_fields", "notes"):
            if not isinstance(row[key], list) or any(not isinstance(v, str) for v in row[key]):
                raise ValueError(f"invalid {key}: {sid}")
        if not isinstance(row["robots"], dict) or not isinstance(row["scope"], dict):
            raise ValueError(f"invalid robots/scope: {sid}")
        if "refresh_days" in row["scope"] and (
                type(row["scope"]["refresh_days"]) is not int or row["scope"]["refresh_days"] < 1):
            raise ValueError(f"scope.refresh_days must be a positive integer: {sid}")
        hosts = {https_url(host, origin=True) for host in row["allowed_hosts"]}
        blocked_hosts = {https_url(origin, origin=True)
                         for origin in row["robots"].get("blocked_origins", [])}
        if hosts & blocked_hosts:
            raise ValueError(f"allowed_hosts includes robots-blocked origin: {sid}")
        for host in row["official_url_hosts"]:
            https_url(host, origin=True)
        for endpoint in row["endpoints"]:
            if https_url(endpoint) not in hosts:
                raise ValueError(f"endpoint outside allowed_hosts: {sid}")
            if "https://" + https_url(endpoint) in row["robots"].get("blocked_origins", []):
                raise ValueError(f"endpoint on robots-blocked origin: {sid}")
        if row["license_source_url"] is not None:
            https_url(row["license_source_url"])
        if not row["public_fields"] or not set(row["public_fields"]) <= PUBLIC_FIELDS:
            raise ValueError(f"invalid public_fields: {sid}")
        if "url" in row["robots"]:
            https_url(row["robots"]["url"])
        if "absent_when_redirect_to" in row["robots"]:
            value = row["robots"]["absent_when_redirect_to"]
            if not isinstance(value, str):
                raise ValueError("invalid robots absence redirect")
            target = urlsplit(value)
            if (target.scheme not in {"http", "https"}
                    or https_url(target._replace(scheme="https").geturl()) !=
                    https_url(row["robots"]["url"])
                    or unquote(target.path).rstrip("/").lower() == "/robots.txt"
                    or "\\" in value):
                raise ValueError("invalid robots absence redirect")
            date.fromisoformat(row["robots"]["observed_at"])
            if row["robots"].get("observed_runner") not in {"github-actions", "xisx-pc"}:
                raise ValueError("robots absence redirect requires evidence")
        for origin in row["robots"].get("blocked_origins", []):
            https_url(origin, origin=True)
        for key in ("min_request_interval_seconds", "capacity_estimate_mb"):
            value = row[key]
            if key == "capacity_estimate_mb" and value is None:
                unknown += 1
                continue
            if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
                raise ValueError(f"invalid {key}: {sid}")
        date.fromisoformat(row["verified_at"])
        total += row["capacity_estimate_mb"] or 0
    if total > 400:
        raise ValueError(f"capacity exceeds 400MB: {total}")
    if unknown and registry.get("capacity_reserve_mb", 0) <= 0:
        raise ValueError("unknown capacity requires an explicit reserve")
    return {"status": "PASS", "source_count": len(rows), "capacity_estimate_mb": round(total, 2),
            "capacity_reserve_mb": registry.get("capacity_reserve_mb", 0),
            "unknown_capacity_sources": unknown, "capacity_limit_mb": 400}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--self-check", action="store_true", required=True)
    parser.parse_args()
    print(json.dumps(validate(json.loads(REGISTRY.read_text(encoding="utf-8"))), ensure_ascii=False))


if __name__ == "__main__":
    main()
