#!/usr/bin/env python3
"""Bounded official-site probes; only robots redirects are followed, without retries."""
import argparse
from datetime import datetime, timezone
from http.client import HTTPException
import json
import os
from pathlib import Path
import runpy
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from intel_v2.http_fetch import RobotsPolicy

REGISTRY = Path(__file__).resolve().parents[1] / "docs/govintel/national-sources.v1.json"
MAX_BYTES = 256 * 1024
USER_AGENT = "GovIntelNationalPreflight/1.0"


def origin(url):
    return "https://" + urlsplit(url).hostname


def configuration(runner=None):
    validator = runpy.run_path(str(Path(__file__).with_name("national-sources.py")))
    registry = json.loads(REGISTRY.read_text(encoding="utf-8"))
    validator["validate"](registry)
    rows = {r["national_source_id"]: r for r in registry["sources"]}
    councils = [r for r in rows.values() if r["national_source_id"].startswith("N-COUNCIL-")]
    if len(councils) != 22:
        raise ValueError("preflight requires 22 councils")
    if runner is None:
        runner = os.environ.get("NATIONAL_RUNNER", "github-actions")
    if runner not in ("github-actions", "xisx-pc"):
        raise ValueError("invalid NATIONAL_RUNNER")
    targets = {r["preflight_url"]: r for r in councils}
    for sid in ("N-LY-ID20-BILLS", "N-PCC-WEB", "N-PCC-GPA", "N-ODP-CATALOG", "N-TDX-EVENTS"):
        row = rows[sid]
        url = ("https://data.gov.tw/api/v2/rest/dataset/6564" if sid == "N-ODP-CATALOG"
               else row["endpoints"][0].replace("YYYYMMDD", "20261117"))
        targets[url] = row
    targets = {url: row for url, row in targets.items() if row["runner"] == runner}
    pages = list(targets)
    robots_urls = list(dict.fromkeys(origin(url) + "/robots.txt" for url in pages))
    if rows["N-COUNCIL-CYQ"]["runner"] == runner:
        robots_urls.append(rows["N-COUNCIL-CYQ"]["robots"]["url"])
    blocked = {host for row in rows.values() for host in row["robots"].get("blocked_origins", [])}
    for row in councils:
        if row["status"] == "BLOCKED_BY_ROBOTS_NEEDS_CONSENT":
            blocked.add(origin(row["preflight_url"]))
    for url in pages + robots_urls:
        validator["https_url"](url)
    if len(set(pages + robots_urls)) != len(pages + robots_urls):
        raise ValueError("duplicate preflight URL")
    return pages, robots_urls, blocked, targets


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def observation(url, status):
    return {"url": url, "host": urlsplit(url).hostname, "status": status,
            "http_status": None, "elapsed_seconds": 0, "bytes": 0,
            "kind": "ROBOTS" if urlsplit(url).path == "/robots.txt" else "PAGE"}


def probe(url, opener, *, robots=False):
    started = time.monotonic()
    result = observation(url, "ERROR")
    if robots:
        result["kind"] = "ROBOTS"
    try:
        request = Request(url, headers={"User-Agent": USER_AGENT})
        with opener.open(request, timeout=10) as response:
            result["http_status"] = response.status
            body = response.read(MAX_BYTES + 1)
            result["bytes"] = len(body)
            result["status"] = "BODY_LIMIT" if len(body) > MAX_BYTES else "OK" if body else "EMPTY_BODY"
            if result["kind"] == "ROBOTS":
                result["robots_content"] = body[:MAX_BYTES].decode("utf-8-sig")
    except HTTPError as error:
        result.update(status="HTTP_ERROR", http_status=error.code)
        if 300 <= error.code < 400 and error.headers:
            result["location"] = error.headers.get("Location")
        error.close()
    except (URLError, OSError) as error:
        result.update(status="UNREACHABLE_FROM_RUNNER", error_type=type(error).__name__)
    except (ValueError, HTTPException) as error:
        result["error_type"] = type(error).__name__
    result["elapsed_seconds"] = round(time.monotonic() - started, 6)
    return result


def robots_policy(row, *, documented_open_api=False):
    if row["status"] in {"BODY_LIMIT", "ERROR", "UNREACHABLE_FROM_RUNNER"}:
        return "UNVERIFIED", None
    return RobotsPolicy.evaluate(row["url"], row["http_status"], row.get("robots_content"),
                                 documented_open_api=documented_open_api)


def build_report(*, dry_run=False, opener=None):
    pages, robots_urls, blocked, targets = configuration()
    results, policies, last_request = [], {}, {}
    if not dry_run and opener is None:
        opener = build_opener(NoRedirect())

    def request(url, interval=3, *, robots=False):
        host = urlsplit(url).hostname
        delay = interval - (time.monotonic() - last_request.get(host, -float("inf")))
        if delay > 0:
            time.sleep(delay)
        last_request[host] = time.monotonic()
        return probe(url, opener, robots=robots)

    def request_robots(url):
        current, redirects, elapsed = url, [], 0
        while True:
            row = request(current, robots=True)
            elapsed += row["elapsed_seconds"]
            status = row["http_status"]
            if status is None or not 300 <= status < 400:
                break
            try:
                target = RobotsPolicy.redirect_target(current, row.get("location"), len(redirects))
            except (RuntimeError, ValueError) as error:
                row.update(status="ERROR", error_type=type(error).__name__)
                break
            redirects.append({"url": current, "http_status": status, "target": target})
            current = target
        row.update(url=url, host=urlsplit(url).hostname, kind="ROBOTS", final_url=current,
                   redirects=redirects, elapsed_seconds=round(elapsed, 6))
        return row

    for url in robots_urls:
        row = observation(url, "DRY_RUN") if dry_run else request_robots(url)
        state, parser = ("UNVERIFIED", None) if dry_run else robots_policy(row)
        row["robots_policy"] = state
        policies[origin(url)] = row
        results.append(row)
    for url in pages:
        robots_row = policies[origin(url)]
        state, parser = robots_policy(robots_row, documented_open_api=targets[url]["documented_open_api"])
        if origin(url) in blocked or (parser and not parser.can_fetch(USER_AGENT, url)):
            row = observation(url, "BLOCKED_BY_ROBOTS_NEEDS_CONSENT")
        elif dry_run:
            row = observation(url, "DRY_RUN")
        elif robots_row["status"] == "UNREACHABLE_FROM_RUNNER":
            row = observation(url, "UNREACHABLE_FROM_RUNNER")
            row.update(error_type=robots_row["error_type"], blocked_by="robots_fetch")
        elif state not in RobotsPolicy.ALLOWED:
            row = observation(url, "SKIPPED_ROBOTS_UNVERIFIED")
        else:
            delay = max(3, parser.crawl_delay(USER_AGENT) or 0) if parser else 3
            row = request(url, delay)
        row["robots_policy"] = state
        row["national_source_id"] = targets[url]["national_source_id"]
        results.append(row)
    report = {"schema_version": 1, "generated_at": datetime.now(timezone.utc).isoformat(),
              "runner": os.environ.get("NATIONAL_RUNNER", "github-actions"),
              "dry_run": dry_run, "results": results}
    validate_report(report, robots_urls + pages)
    return report


def validate_report(report, expected_urls=None):
    if expected_urls is None:
        pages, robots_urls, _, _ = configuration(runner=report.get("runner"))
        expected_urls = robots_urls + pages
    if (report["schema_version"] != 1 or report.get("runner") not in ("github-actions", "xisx-pc")
            or [r["url"] for r in report["results"]] != expected_urls):
        raise ValueError("invalid report envelope")
    for row in report["results"]:
        if (row["host"] != urlsplit(row["url"]).hostname
                or row["status"] not in {"DRY_RUN", "OK", "BODY_LIMIT", "EMPTY_BODY", "HTTP_ERROR", "ERROR",
                                         "BLOCKED_BY_ROBOTS_NEEDS_CONSENT", "SKIPPED_ROBOTS_UNVERIFIED",
                                         "UNREACHABLE_FROM_RUNNER"}
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
