"""Bounded S032 list continuations; local receipts never establish full coverage.

Hashes bind supplied metadata against accidental mutation. They are neither an
official signature nor an independent source/corpus completeness assessment.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime, timezone
import hashlib
import inspect
import json
from pathlib import Path
import re
import time
from urllib.parse import urljoin

from bs4 import BeautifulSoup

import online_collect as oc

MAX_CHECKPOINT_BYTES = 2 * 1024 * 1024
MAX_TOTAL_PAGES = 256
MAX_TOTAL_ROWS = 20000
MAX_CONTROLS = 100
SCOPE = "LOCAL_TRAVERSAL_RECEIPT_ONLY"
CONTRACT = "S032_COMMA_PARSER_V2"
ROLES = {"FIRST", "PREVIOUS", "NEXT", "LAST", "NUMBERED"}
ROLE_LABELS = {
    "FIRST": {"第一頁", "第一页", "first", "first page"},
    "PREVIOUS": {"上一頁", "上一页", "prev", "previous", "prev page", "previous page"},
    "NEXT": {"下一頁", "下一页", "下頁", "下页", "next", "next page"},
    "LAST": {"最後一頁", "最后一页", "末頁", "末页", "最末頁", "最末页", "last", "last page"},
}
REL_ROLES = {"first": "FIRST", "prev": "PREVIOUS", "previous": "PREVIOUS", "next": "NEXT", "last": "LAST"}


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()


def encode_checkpoint(checkpoint):
    encoded = (json.dumps(checkpoint, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
    if len(encoded) > MAX_CHECKPOINT_BYTES:
        raise ValueError("serialized checkpoint exceeds 2 MiB")
    return encoded


def binding(start, end):
    if type(start) is not date or type(end) is not date or start > end:
        raise ValueError("valid ordered start/end dates required")
    root = Path(__file__).resolve().parents[1]
    # Full modules cover helpers/constants as well as the row projection and
    # transport. Live callable fingerprints also reject in-memory replacement
    # of a core parser while an already-imported process is running.
    modules = ("online_collect.py", "collect.py", "intel_v2/news_list_checkpoint.py",
               "scripts/news-list-resume.py", "scripts/candidate-runtime-canary.py")
    module_hashes = {name: hashlib.sha256((root / name).read_bytes()).hexdigest() for name in modules}
    functions = {"row_parser": oc.parse_news_list, "row_date_parser": oc.roc_date,
                 "cursor_parser": oc._traffic_list_page_identity, "navigation_parser": oc._traffic_news_list_next,
                 "row_projection": row_metadata, "canonical_digest": digest, "control_projection": pager_controls,
                 "batch_projection": collect_batch}
    try:
        callable_hashes = {name: hashlib.sha256(inspect.getsource(function).encode()).hexdigest() for name, function in functions.items()}
    except (TypeError, OSError) as error:
        raise ValueError("parser code binding is unverifiable; restart from the official entrypoint") from error
    return {
        "schema_version": 2, "source_id": "S-032", "source_url": oc.NEWS_LIST_SOURCES["S-032"]["list_url"],
        "requested_start": start.isoformat(), "requested_end": end.isoformat(),
        "parser_contract": CONTRACT, "parser_code_sha256": digest({"modules": module_hashes, "callables": callable_hashes}),
        "module_code_sha256": module_hashes, "callable_code_sha256": callable_hashes,
        "source_row_contract_sha256": digest(oc.NEWS_LIST_SOURCES["S-032"]),
        "control_contract_sha256": digest({"roles": sorted(ROLES), "labels": {role: sorted(values) for role, values in ROLE_LABELS.items()}, "rel_roles": REL_ROLES}),
        "validation_scope": SCOPE,
    }


def seal(checkpoint):
    value = deepcopy(checkpoint)
    value.pop("checkpoint_sha256", None)
    value["checkpoint_sha256"] = digest(value)
    return value


def new_checkpoint(start, end):
    return seal({"binding": binding(start, end), "batches": []})


def canonical_cursor(number):
    if type(number) is not int or not 1 <= number <= 999999:
        raise ValueError("bounded navigation target page required")
    root = oc.NEWS_LIST_SOURCES["S-032"]["list_url"]
    return root if number == 1 else root + ",,,,,,,," + str(number)


def pager_controls(body, base_url):
    """Recognize exact roles/official list targets before projecting metadata."""
    current = oc._traffic_list_page_identity(base_url)
    if current is None:
        raise ValueError("unapproved pagination base cursor")
    result = []
    for node in BeautifulSoup(body, "html.parser").find_all(["a", "link", "button"]):
        labels = [" ".join(value.split()).lower() for value in (
            " ".join(node.stripped_strings), str(node.get("title") or ""), str(node.get("aria-label") or ""))]
        rel = {str(value).lower() for value in node.get("rel", [])}
        classes = {str(value).lower() for value in node.get("class", [])}
        roles = {role for role, values in ROLE_LABELS.items() if any(label in values for label in labels)}
        roles.update(REL_ROLES[value] for value in rel | classes if value in REL_ROLES)
        numbers = {int(re.search(r"\d+", label).group()) for label in labels
                   if re.fullmatch(r"(?:第\s*)?\d{1,4}\s*(?:頁|页)?", label)}
        if numbers:
            roles.add("NUMBERED")
        href = str(node.get("href") or "").strip()
        target = oc._traffic_list_page_identity(urljoin(base_url, href)) if href and len(href) <= 2048 else None
        if not roles:
            loose_hint = (any(re.search(r"下一[頁页]|下[頁页]|最後一頁|最后一页|末[頁页]|第一[頁页]|上一[頁页]|\b(?:next|last|first|prev(?:ious)?)(?:\s+page)?\b", label)
                              for label in labels) or any("next" in value for value in classes))
            article = bool(re.search(oc.NEWS_LIST_SOURCES["S-032"]["id_pattern"], href))
            # An article-shaped link inside a pager can still be a malformed
            # control; dropping it could manufacture a terminal page.
            in_pager = any(set(parent.get("class", [])) & {"page", "pagination", "pager", "paginator"}
                           for parent in (node, *node.parents) if getattr(parent, "attrs", None) is not None)
            if loose_hint and (not article or in_pager):
                raise ValueError("ambiguous pagination role; acquisition blocked")
            continue
        if len(roles) != 1 or len(numbers) > 1:
            raise ValueError("conflicting pagination roles; acquisition blocked")
        if target is None or target[0] != current[0]:
            raise ValueError("unapproved pagination target; acquisition blocked")
        if numbers and numbers != {target[1]}:
            raise ValueError("numbered pagination label disagrees with target")
        if len(result) >= MAX_CONTROLS:
            raise ValueError("pagination metadata bound exceeded")
        result.append({"role": next(iter(roles)), "target_page": target[1]})
    return result


def control_html(controls):
    if type(controls) is not list or len(controls) > MAX_CONTROLS:
        raise ValueError("invalid pager controls")
    result = []
    for control in controls:
        if type(control) is not dict or set(control) != {"role", "target_page"} or type(control["role"]) is not str or control["role"] not in ROLES:
            raise ValueError("invalid normalized pager control shape")
        target = canonical_cursor(control["target_page"])
        role = control["role"]
        if role == "NUMBERED":
            if control["target_page"] > 9999:
                raise ValueError("numbered target exceeds recognized label contract")
            result.append(f'<a href="{target}">{control["target_page"]}</a>')
        else:
            rel = {"FIRST": "first", "PREVIOUS": "prev", "NEXT": "next", "LAST": "last"}[role]
            result.append(f'<a href="{target}" rel="{rel}">{rel}</a>')
    return "".join(result).encode()


def row_metadata(row):
    published = row["published"].isoformat() if row["published"] else None
    return {"stable_key": row["stable_key"], "published": published,
            "list_sha256": digest({"title": row["title"], "detail_url": row["detail_url"], "published": published})}


def validate_checkpoint(checkpoint, start, end, expected_sha256=None):
    if type(checkpoint) is not dict or set(checkpoint) != {"binding", "batches", "checkpoint_sha256"}:
        raise ValueError("invalid checkpoint shape")
    encode_checkpoint(checkpoint)
    if type(checkpoint["binding"]) is not dict or digest(checkpoint["binding"]) != digest(binding(start, end)):
        raise ValueError("source/window/parser binding changed; restart from the official entrypoint without migrating or rehashing old receipts")
    if seal(checkpoint)["checkpoint_sha256"] != checkpoint["checkpoint_sha256"] or (expected_sha256 is not None and checkpoint["checkpoint_sha256"] != expected_sha256):
        raise ValueError("checkpoint hash mismatch")
    batches = checkpoint["batches"]
    if type(batches) is not list or len(batches) > MAX_TOTAL_PAGES:
        raise ValueError("invalid batch ledger")
    cursor = checkpoint["binding"]["source_url"]
    previous_batch = None
    seen_rows = {}
    dates = []
    page_count = 0
    total = None
    last_observed = None
    for index, batch in enumerate(batches, 1):
        if type(batch) is not dict or set(batch) != {"batch", "previous_batch_sha256", "pages", "batch_sha256"}:
            raise ValueError("invalid batch shape")
        if type(batch["batch"]) is not int or batch["batch"] != index or batch["previous_batch_sha256"] != previous_batch:
            raise ValueError("broken batch chain")
        unsigned = {key: value for key, value in batch.items() if key != "batch_sha256"}
        if digest(unsigned) != batch["batch_sha256"]:
            raise ValueError("batch hash mismatch")
        previous_batch = batch["batch_sha256"]
        pages = batch["pages"]
        if type(pages) is not list or not 1 <= len(pages) <= oc.MAX_NEWS_LIST_PAGES_HARD_LIMIT:
            raise ValueError("each batch must contain 1..40 pages")
        for page in pages:
            page_count += 1
            if page_count > MAX_TOTAL_PAGES or type(page) is not dict or set(page) != {"requested_url", "response_url", "observed_at", "http_status", "body_sha256", "byte_count", "controls", "rows", "next_url", "has_next"}:
                raise ValueError("invalid/bounded page ledger")
            if cursor is None or page["requested_url"] != cursor or page["response_url"] != cursor:
                raise ValueError("cursor gap, middle start, redirect, or post-terminal page")
            identity = oc._traffic_list_page_identity(cursor)
            if identity is None or identity[1] != page_count:
                raise ValueError("list page sequence does not begin at the approved entrypoint")
            if type(page["http_status"]) is not int or page["http_status"] != 200 or type(page["byte_count"]) is not int or not 0 < page["byte_count"] <= 2 * 1024 * 1024:
                raise ValueError("non-success or unbounded page")
            if type(page["body_sha256"]) is not str or not re.fullmatch(r"[0-9a-f]{64}", page["body_sha256"]):
                raise ValueError("missing page byte digest")
            observed = datetime.fromisoformat(page["observed_at"])
            if observed.tzinfo is None or (last_observed is not None and observed < last_observed):
                raise ValueError("observation time must be aware and monotonic")
            last_observed = observed
            markup = control_html(page["controls"])
            following, has_next = oc._traffic_news_list_next(BeautifulSoup(markup.decode("utf-8"), "html.parser"), cursor)
            if following is None and has_next:
                raise ValueError("unresolved pagination; acquisition blocked")
            if type(page["has_next"]) is not bool or (following, has_next) != (page["next_url"], page["has_next"]):
                raise ValueError("navigation claim differs from recorded controls")
            for control in page["controls"]:
                if control["role"] == "LAST":
                    number = control["target_page"]
                    if total is not None and number != total:
                        raise ValueError("official Last changed across batches")
                    total = number
            rows = page["rows"]
            if type(rows) is not list or not 1 <= len(rows) <= 500:
                raise ValueError("invalid page row metadata")
            for row in rows:
                if type(row) is not dict or set(row) != {"stable_key", "published", "list_sha256"}:
                    raise ValueError("invalid row metadata")
                if type(row["stable_key"]) is not str or not re.fullmatch(r"\d{1,30}", row["stable_key"]) or type(row["list_sha256"]) is not str or not re.fullmatch(r"[0-9a-f]{64}", row["list_sha256"]):
                    raise ValueError("invalid stable row identity")
                published = date.fromisoformat(row["published"]) if row["published"] is not None else None
                if row["stable_key"] in seen_rows and seen_rows[row["stable_key"]] != row:
                    raise ValueError("stable row changed during traversal")
                seen_rows[row["stable_key"]] = row
                dates.append(published)
                if len(dates) > MAX_TOTAL_ROWS:
                    raise ValueError("row ledger bound exceeded")
            cursor = following
    known = [value for value in dates if value is not None]
    ordered = all(left >= right for left, right in zip(known, known[1:]))
    terminal_consistent = bool(page_count and not page["has_next"] and total == page_count)
    return {
        "validation_scope": SCOPE, "checkpoint_sha256": checkpoint["checkpoint_sha256"],
        "source_id": "S-032", "requested_start": start.isoformat(), "requested_end": end.isoformat(),
        "batches": len(batches), "pages": page_count, "unique_ids": len(seen_rows),
        "next_resume_url": cursor, "declared_last_page": total,
        "prefix_to_declared_terminal_consistent": terminal_consistent,
        "all_observed_dates_known": len(known) == len(dates), "reverse_chronological_observed": ordered,
        "window_list_item_count": sum(row["published"] is not None and start <= date.fromisoformat(row["published"]) <= end for row in seen_rows.values()),
        "window_completeness": "PARTIAL", "whole_history_completeness": "UNKNOWN",
        "coverage_independently_verified": False, "detail_pages_complete": False, "attachments_downloaded": False,
        "rights_approved": False, "promotion_eligible": False,
    }


def collect_batch(session, start, end, *, checkpoint=None, expected_sha256=None, max_list_pages=None, minimum_interval=0.3, seconds=120):
    budget = oc.MAX_NEWS_LIST_PAGES if max_list_pages is None else max_list_pages
    if type(budget) is not int or not 1 <= budget <= oc.MAX_NEWS_LIST_PAGES_HARD_LIMIT:
        raise ValueError("max_list_pages must be an integer between 1 and 40")
    if not 0.1 <= minimum_interval <= 10 or not 1 <= seconds <= 300:
        raise ValueError("invalid bounded interval/deadline")
    checkpoint = new_checkpoint(start, end) if checkpoint is None else deepcopy(checkpoint)
    state = validate_checkpoint(checkpoint, start, end, expected_sha256)
    cursor = state["next_resume_url"]
    if cursor is None:
        raise ValueError("checkpoint has no safe continuation cursor")
    pages = []
    started = time.monotonic()
    for index in range(budget):
        if time.monotonic() - started >= seconds:
            break
        if index:
            time.sleep(minimum_interval)
        response = oc.get(session, cursor, source_id="S-032")
        # A same-host redirect is not evidence of the requested list cursor.
        if response.url != cursor:
            raise ValueError("redirected list cursor requires review")
        entries = oc.parse_news_list(response.content, cursor, oc.NEWS_LIST_SOURCES["S-032"]["id_pattern"])
        controls = pager_controls(response.content, cursor)
        next_url, has_next = oc._traffic_news_list_next(BeautifulSoup(control_html(controls).decode("utf-8"), "html.parser"), cursor)
        if next_url is None and has_next:
            raise ValueError("unresolved pagination; acquisition blocked before sealing")
        page = {"requested_url": cursor, "response_url": response.url, "observed_at": datetime.now(timezone.utc).isoformat(),
                "http_status": response.status_code, "body_sha256": hashlib.sha256(response.content).hexdigest(), "byte_count": len(response.content),
                "controls": controls, "rows": [row_metadata(row) for row in entries],
                "next_url": next_url, "has_next": has_next}
        pages.append(page)
        cursor = next_url
        if cursor is None:
            break
    if not pages:
        raise ValueError("batch acquired no page before deadline")
    batches = checkpoint["batches"]
    batch = {"batch": len(batches) + 1, "previous_batch_sha256": batches[-1]["batch_sha256"] if batches else None, "pages": pages}
    batch["batch_sha256"] = digest(batch)
    batches.append(batch)
    checkpoint = seal(checkpoint)
    return checkpoint, validate_checkpoint(checkpoint, start, end)
