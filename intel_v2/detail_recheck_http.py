"""Bounded HTTPS transport for the transport-neutral detail recheck core."""
from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any
from urllib.parse import urljoin, urlsplit, urlunsplit

import requests
from bs4 import BeautifulSoup

from intel_v2.detail_recheck import classify_observation, plan_recheck
from intel_v2.located_facts import MAX_DOCUMENT_BYTES, normalized_text, sha256


MAX_BODY_BYTES = MAX_DOCUMENT_BYTES
MAX_REDIRECTS = 3
CHUNK_BYTES = 64 * 1024
REDIRECT_STATUSES = {301, 302, 303, 307, 308}
ATTACHMENT_RE = re.compile(r"\.(?:pdf|docx?|xlsx?|odt|zip)(?:$|[?#])", re.IGNORECASE)


def _hosts(values: Iterable[str]) -> set[str]:
    if isinstance(values, (str, bytes)):
        raise ValueError("allowed_hosts must be a collection of hostnames")
    result = set()
    for value in values:
        host = str(value).strip().lower().rstrip(".")
        if not host or any(char in host for char in "/:@"):
            raise ValueError("allowed_hosts must contain hostnames only")
        result.add(host)
    if not result:
        raise ValueError("allowed_hosts is required")
    return result


def _approved_url(url: str, allowed_hosts: set[str]) -> str:
    if not isinstance(url, str) or not url:
        raise ValueError("detail URL is required")
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError as error:
        raise ValueError("detail URL is malformed") from error
    host = (parts.hostname or "").lower().rstrip(".")
    if (
        parts.scheme.lower() != "https"
        or not host
        or host not in allowed_hosts
        or parts.username
        or parts.password
        or port not in (None, 443)
    ):
        raise ValueError("detail URL is outside the approved HTTPS origins")
    return url


def _header(response: Any, name: str) -> str | None:
    headers = getattr(response, "headers", {})
    value = headers.get(name)
    if value is None:
        value = headers.get(name.lower())
    return str(value) if value is not None else None


def _retry_after(value: str | None) -> int | None:
    if value is None:
        return None
    try:
        seconds = int(value.strip())
    except (TypeError, ValueError):
        return None
    return max(0, seconds)


def _read_body(response: Any, max_body_bytes: int) -> bytes:
    declared = _header(response, "Content-Length")
    if declared is not None:
        try:
            declared_bytes = int(declared)
            if declared_bytes < 0:
                raise ValueError("detail response has invalid content length")
            if declared_bytes > max_body_bytes:
                raise ValueError("detail response exceeds bounded byte limit")
        except ValueError as error:
            if str(error) == "detail response exceeds bounded byte limit":
                raise
            raise ValueError("detail response has invalid content length") from error

    body = bytearray()
    for chunk in response.iter_content(chunk_size=CHUNK_BYTES):
        if not isinstance(chunk, (bytes, bytearray)):
            raise ValueError("detail response yielded a non-byte chunk")
        body.extend(chunk)
        if len(body) > max_body_bytes:
            raise ValueError("detail response exceeds bounded byte limit")
    return bytes(body)


def _attachment_rows(body: bytes, base_url: str, allowed_hosts: set[str]) -> list[dict[str, str]]:
    rows = []
    seen: set[str] = set()
    soup = BeautifulSoup(body, "html.parser")
    for anchor in soup.find_all("a", href=True):
        href = str(anchor["href"]).strip()
        resolved = urljoin(base_url, href)
        try:
            parts = urlsplit(resolved)
            if (
                parts.scheme.lower() != "https"
                or not parts.hostname
                or parts.hostname.lower().rstrip(".") not in allowed_hosts
                or parts.username
                or parts.password
                or parts.port not in (None, 443)
                or not ATTACHMENT_RE.search(resolved)
            ):
                continue
        except ValueError:
            continue
        canonical_url = urlunsplit(
            (parts.scheme.lower(), parts.netloc.lower(), parts.path or "/", parts.query, "")
        )
        if canonical_url in seen:
            continue
        seen.add(canonical_url)
        rows.append({
            "attachment_id": "ATT-" + sha256(canonical_url.encode("utf-8"))[:20].upper(),
        })
    return sorted(rows, key=lambda item: item["attachment_id"])


def _unavailable(previous: dict[str, Any] | None, observed_at: str, error: Exception) -> dict[str, Any]:
    observation = {"status_code": 0, "available": False}
    return {
        "observation": observation,
        "classification": classify_observation(previous, observation, observed_at=observed_at),
        "transport_error": {
            "type": type(error).__name__,
            "message": str(error).strip()[:256],
        },
    }


def recheck_detail(
    session: Any,
    requested_url: str,
    previous: dict[str, Any] | None,
    *,
    observed_at: str,
    allowed_hosts: Iterable[str],
    interval_hours: float = 24,
    timeout: float | tuple[float, float] = (5, 15),
    max_body_bytes: int = MAX_BODY_BYTES,
    max_redirects: int = MAX_REDIRECTS,
    include_body: bool = False,
) -> dict[str, Any]:
    """Fetch one approved detail page without following unapproved redirects."""
    if not isinstance(max_body_bytes, int) or max_body_bytes <= 0:
        raise ValueError("max_body_bytes must be positive")
    if not isinstance(max_redirects, int) or max_redirects < 0:
        raise ValueError("max_redirects must be non-negative")
    hosts = _hosts(allowed_hosts)
    _approved_url(requested_url, hosts)
    plan = plan_recheck(previous, observed_at, interval_hours=interval_hours)
    result: dict[str, Any] = {
        "requested_url": requested_url,
        "final_url": None,
        "http_status": None,
        "redirect_count": 0,
        "request_headers": plan["request_headers"],
        "plan": plan,
        "observation": None,
        "classification": None,
    }
    if plan["status"] != "DUE":
        return result

    current_url = requested_url
    response = None
    try:
        for redirect_count in range(max_redirects + 1):
            _approved_url(current_url, hosts)
            response = session.get(
                current_url,
                headers=plan["request_headers"],
                timeout=timeout,
                allow_redirects=False,
                stream=True,
            )
            status_code = int(response.status_code)
            if status_code in REDIRECT_STATUSES:
                location = _header(response, "Location")
                response.close()
                response = None
                if not location:
                    raise ValueError("redirect response has no location")
                current_url = urljoin(current_url, location)
                continue

            final_url = _approved_url(str(getattr(response, "url", current_url) or current_url), hosts)
            result.update({
                "final_url": final_url,
                "http_status": status_code,
                "redirect_count": redirect_count,
            })
            if status_code == 304:
                observation = {
                    "status_code": status_code,
                    "attachments_checked": False,
                }
            elif status_code == 429 or status_code >= 500:
                observation = {
                    "status_code": status_code,
                    "retry_after_seconds": _retry_after(_header(response, "Retry-After")),
                }
            elif 200 <= status_code < 300:
                body = _read_body(response, max_body_bytes)
                content_type = _header(response, "Content-Type") or "application/octet-stream"
                normalized = normalized_text(body, content_type)
                observation = {
                    "status_code": status_code,
                    "content_type": content_type,
                    "body_sha256": sha256(body),
                    "normalized_text_sha256": sha256(normalized),
                    "attachments": _attachment_rows(body, final_url, hosts),
                }
                if include_body:
                    result["response_body"] = body
                for key, header_name in (("etag", "ETag"), ("last_modified", "Last-Modified")):
                    value = _header(response, header_name)
                    if value is not None:
                        observation[key] = value
            else:
                observation = {"status_code": status_code}
            result["observation"] = observation
            result["classification"] = classify_observation(
                previous, observation, observed_at=observed_at
            )
            return result
        raise ValueError("redirect budget exhausted")
    except (requests.RequestException, OSError, ValueError, UnicodeError, AttributeError, TypeError) as error:
        result.update(_unavailable(previous, observed_at, error))
        return result
    finally:
        if response is not None:
            response.close()
