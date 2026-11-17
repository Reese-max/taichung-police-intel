#!/usr/bin/env python3
"""Bounded official-site probes, gated by robots; no redirects or retries."""
import argparse
from datetime import datetime, timezone
from http.client import HTTPException
import json
from pathlib import Path
import runpy
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener
from urllib.robotparser import RobotFileParser

REGISTRY = Path(__file__).resolve().parents[1] / "docs/govintel/national-sources.v1.json"
MAX_BYTES = 256 * 1024
USER_AGENT = "GovIntelNationalPreflight/1.0"


def origin(url):
    return "https://" + urlsplit(url).hostname


def configuration():
    validator = runpy.run_path(str(Path(__file__).with_name("national-sources.py")))
    registry = json.loads(REGISTRY.read_text(encoding="utf-8"))
    validator["validate"](registry)
    rows = {r["national_source_id"]: r for r in registry["sources"]}
    councils = [r for r in rows.values() if r["national_source_id"].startswith("N-COUNCIL-")]
    if len(councils) != 22:
        raise ValueError("preflight requires 22 councils")
    pages = [r["preflight_url"] for r in councils]
    pages += [rows["N-LY-ID20-BILLS"]["endpoints"][0], rows["N-PCC-WEB"]["endpoints"][0],
              rows["N-PCC-GPA"]["endpoints"][0].replace("YYYYMMDD", "20261117"),
              "https://data.gov.tw/api/v2/rest/dataset/6564", rows["N-TDX-EVENTS"]["endpoints"][0]]
    robots_urls = list(dict.fromkeys(origin(url) + "/robots.txt" for url in pages))
    robots_urls.append(rows["N-COUNCIL-CYQ"]["robots"]["url"])
    blocked = {host for row in rows.values() for host in row["robots"].get("blocked_origins", [])}
    for row in councils:
        if row["status"] == "BLOCKED_BY_ROBOTS_NEEDS_CONSENT":
            blocked.add(origin(row["preflight_url"]))
    for url in pages + robots_urls:
        validator["https_url"](url)
    if len(set(pages + robots_urls)) != len(pages + robots_urls):
        raise ValueError("duplicate preflight URL")
    return pages, robots_urls, blocked


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def observation(url, status):
    return {"url": url, "host": urlsplit(url).hostname, "status": status,
            "http_status": None, "elapsed_seconds": 0, "bytes": 0,
            "kind": "ROBOTS" if urlsplit(url).path == "/robots.txt" else "PAGE"}


def probe(url, opener):
    started = time.monotonic()
    result = observation(url, "ERROR")
    try:
        request = Request(url, headers={"User-Agent": USER_AGENT})
        with opener.open(request, timeout=10) as response:
            result["http_status"] = response.status
            body = response.read(MAX_BYTES + 1)
            result["bytes"] = len(body)
            result["status"] = "BODY_LIMIT" if len(body) > MAX_BYTES else "OK" if body else "EMPTY_BODY"
            if result["kind"] == "ROBOTS":
                result["robots_content"] = body[:MAX_BYTES].decode("utf-8-sig", errors="replace")
    except HTTPError as error:
        result.update(status="HTTP_ERROR", http_status=error.code)
        # Do not follow redirects, including those emitted by robots.txt.
        if result["kind"] == "ROBOTS" and error.headers:
            target = urljoin(url, error.headers.get("Location", ""))
            if target in (origin(url), origin(url) + "/"):
                result["redirect_target"] = target
        error.close()
    except (URLError, OSError, ValueError, HTTPException) as error:
        result["error_type"] = type(error).__name__
    result["elapsed_seconds"] = round(time.monotonic() - started, 6)
    return result


def robots_policy(row):
    status = row["http_status"]
    if status in (404, 410):
        return "NO_ROBOTS_RESOURCE", None
    if (origin(row["url"]) == "https://web.pcc.gov.tw" and status in (301, 302, 303, 307, 308)
            and row.get("redirect_target") in ("https://web.pcc.gov.tw", "https://web.pcc.gov.tw/")):
        return "KNOWN_REDIRECT_TO_HOME", None
    if status in (401, 403):
        return "DENY", None
    body = row.get("robots_content", "")
    if row["status"] != "OK" or body.lstrip().startswith("<"):
        return "UNVERIFIED", None
    lines = body.splitlines()
    if not any(line.split(":", 1)[0].strip().lower() == "user-agent" for line in lines):
        return "UNVERIFIED", None
    directives = [line.split("#", 1)[0].strip().split(":", 1) for line in lines]
    rules = [(parts[0].strip().lower(), parts[1].strip()) for parts in directives
             if len(parts) == 2 and parts[1].strip()]
    # ponytail: stdlib handles basic rules; skip complex policies until a full parser is needed.
    if (any(key in ("allow", "disallow") and ("*" in value or "$" in value) for key, value in rules)
            or {key for key, _ in rules} >= {"allow", "disallow"}):
        return "UNVERIFIED", None
    parser = RobotFileParser(row["url"])
    parser.parse(lines)
    return "PARSED", parser


def build_report(*, dry_run=False, opener=None):
    pages, robots_urls, blocked = configuration()
    results, policies, last_request = [], {}, {}
    if not dry_run and opener is None:
        opener = build_opener(NoRedirect())

    def request(url, interval=3):
        host = urlsplit(url).hostname
        delay = interval - (time.monotonic() - last_request.get(host, -float("inf")))
        if delay > 0:
            time.sleep(delay)
        last_request[host] = time.monotonic()
        return probe(url, opener)

    for url in robots_urls:
        row = observation(url, "DRY_RUN") if dry_run else request(url)
        state, parser = ("UNVERIFIED", None) if dry_run else robots_policy(row)
        row["robots_policy"] = state
        policies[origin(url)] = state, parser
        results.append(row)
    for url in pages:
        state, parser = policies[origin(url)]
        if origin(url) in blocked or state == "DENY" or (parser and not parser.can_fetch(USER_AGENT, url)):
            row = observation(url, "BLOCKED_BY_ROBOTS_NEEDS_CONSENT")
        elif dry_run:
            row = observation(url, "DRY_RUN")
        elif state == "UNVERIFIED":
            row = observation(url, "SKIPPED_ROBOTS_UNVERIFIED")
        else:
            delay = max(3, parser.crawl_delay(USER_AGENT) or 0) if parser else 3
            row = request(url, delay)
        results.append(row)
    report = {"schema_version": 1, "generated_at": datetime.now(timezone.utc).isoformat(),
              "dry_run": dry_run, "results": results}
    validate_report(report, robots_urls + pages)
    return report


def validate_report(report, expected_urls=None):
    if expected_urls is None:
        pages, robots_urls, _ = configuration()
        expected_urls = robots_urls + pages
    if report["schema_version"] != 1 or [r["url"] for r in report["results"]] != expected_urls:
        raise ValueError("invalid report envelope")
    for row in report["results"]:
        if (row["host"] != urlsplit(row["url"]).hostname
                or row["status"] not in {"DRY_RUN", "OK", "BODY_LIMIT", "EMPTY_BODY", "HTTP_ERROR", "ERROR",
                                         "BLOCKED_BY_ROBOTS_NEEDS_CONSENT", "SKIPPED_ROBOTS_UNVERIFIED"}
                or not 0 <= row["bytes"] <= MAX_BYTES + 1 or row["elapsed_seconds"] < 0):
            raise ValueError("invalid report row")
        if row["status"] in {"DRY_RUN", "BLOCKED_BY_ROBOTS_NEEDS_CONSENT", "SKIPPED_ROBOTS_UNVERIFIED"}:
            if row["bytes"] != 0 or row["http_status"] is not None:
                raise ValueError("skipped request must not claim an HTTP receipt")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    text = json.dumps(build_report(dry_run=args.dry_run), ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding="utf-8")
    print(text, end="")


if __name__ == "__main__":
    main()
