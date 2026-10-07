"""Bounded S032 list continuations; local receipts never establish full coverage.

Hashes bind supplied metadata against accidental mutation. They are neither an
official signature nor an independent source/corpus completeness assessment.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime, timezone
import hashlib
import html
import inspect
import json
import re
import time

from bs4 import BeautifulSoup

import online_collect as oc

MAX_CHECKPOINT_BYTES = 2 * 1024 * 1024
MAX_TOTAL_PAGES = 256
MAX_TOTAL_ROWS = 20000
MAX_CONTROLS = 100
SCOPE = "LOCAL_TRAVERSAL_RECEIPT_ONLY"


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
    parser_code = inspect.getsource(oc._traffic_list_page_identity) + inspect.getsource(oc._traffic_news_list_next)
    return {
        "schema_version": 1, "source_id": "S-032", "source_url": oc.NEWS_LIST_SOURCES["S-032"]["list_url"],
        "requested_start": start.isoformat(), "requested_end": end.isoformat(),
        "parser_contract": "S032_COMMA_PARSER_V1", "parser_code_sha256": hashlib.sha256(parser_code.encode()).hexdigest(),
        "validation_scope": SCOPE,
    }


def seal(checkpoint):
    value = deepcopy(checkpoint)
    value.pop("checkpoint_sha256", None)
    value["checkpoint_sha256"] = digest(value)
    return value


def new_checkpoint(start, end):
    return seal({"binding": binding(start, end), "batches": []})


def pager_controls(body):
    """Keep only navigation metadata, never source titles, bodies, or attachments."""
    result = []
    for node in BeautifulSoup(body, "html.parser").find_all(["a", "link", "button"]):
        label = " ".join(node.stripped_strings).strip()
        labels = (label, str(node.get("title") or ""), str(node.get("aria-label") or ""))
        rel = list(node.get("rel", []))
        classes = list(node.get("class", []))
        if not (any(re.search(r"下一[頁页]|下[頁页]|最後一頁|最后一页|末[頁页]|第一[頁页]|上一[頁页]|\b(?:next|last|first|prev(?:ious)?)(?:\s+page)?\b", value, re.I)
                    or re.fullmatch(r"(?:第\s*)?\d{1,4}\s*(?:頁|页)?", value) for value in labels)
                or any(str(value).lower() in {"next", "last", "first", "prev", "previous"} for value in rel)
                or any("next" in str(value).lower() for value in classes)):
            continue
        entry = {"tag": node.name, "label": label, "href": str(node.get("href") or ""),
                 "title": labels[1], "aria-label": labels[2], "rel": rel, "class": classes}
        if len(result) >= MAX_CONTROLS or any(len(value) > 2048 for value in (entry["href"],)) or any(len(value) > 128 for value in labels):
            raise ValueError("pagination metadata bound exceeded")
        result.append(entry)
    return result


def control_html(controls):
    if type(controls) is not list or len(controls) > MAX_CONTROLS:
        raise ValueError("invalid pager controls")
    result = []
    for control in controls:
        if type(control) is not dict or set(control) != {"tag", "label", "href", "title", "aria-label", "rel", "class"}:
            raise ValueError("invalid pager control shape")
        if control["tag"] not in {"a", "link", "button"}:
            raise ValueError("invalid pager tag")
        for field in ("rel", "class"):
            if type(control[field]) is not list or len(control[field]) > 20 or any(type(item) is not str or len(item) > 128 for item in control[field]):
                raise ValueError("invalid pager attribute")
        for field in ("label", "href", "title", "aria-label"):
            if type(control[field]) is not str or len(control[field]) > (2048 if field == "href" else 128):
                raise ValueError("invalid pager attribute")
        attrs = " ".join(f'{field}="{html.escape(" ".join(value) if type(value) is list else value, quote=True)}"'
                         for field, value in control.items() if field not in {"tag", "label"})
        result.append(f'<{control["tag"]} {attrs}>{html.escape(control["label"])}</{control["tag"]}>')
    return "".join(result).encode()


def validate_checkpoint(checkpoint, start, end, expected_sha256=None):
    if type(checkpoint) is not dict or set(checkpoint) != {"binding", "batches", "checkpoint_sha256"}:
        raise ValueError("invalid checkpoint shape")
    encode_checkpoint(checkpoint)
    if type(checkpoint["binding"]) is not dict or digest(checkpoint["binding"]) != digest(binding(start, end)):
        raise ValueError("source/window/parser binding changed")
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
            following, has_next = oc._traffic_news_list_next(BeautifulSoup(markup, "html.parser"), cursor)
            if type(page["has_next"]) is not bool or (following, has_next) != (page["next_url"], page["has_next"]):
                raise ValueError("navigation claim differs from recorded controls")
            for control in page["controls"]:
                label = " ".join((control["label"], control["title"], control["aria-label"]))
                if re.search(r"最後一頁|最后一页|末[頁页]|\blast(?:\s+page)?\b", label, re.I) or "last" in {value.lower() for value in control["rel"]}:
                    target = oc._traffic_list_page_identity(oc.urllib.parse.urljoin(cursor, control["href"]))
                    if target is None or (total is not None and target[1] != total):
                        raise ValueError("official Last changed across batches")
                    total = target[1]
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
        controls = pager_controls(response.content)
        next_url, has_next = oc._traffic_news_list_next(BeautifulSoup(control_html(controls), "html.parser"), cursor)
        page = {"requested_url": cursor, "response_url": response.url, "observed_at": datetime.now(timezone.utc).isoformat(),
                "http_status": response.status_code, "body_sha256": hashlib.sha256(response.content).hexdigest(), "byte_count": len(response.content),
                "controls": controls, "rows": [{"stable_key": row["stable_key"], "published": row["published"].isoformat() if row["published"] else None,
                    "list_sha256": digest({"title": row["title"], "detail_url": row["detail_url"], "published": row["published"].isoformat() if row["published"] else None})} for row in entries],
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
