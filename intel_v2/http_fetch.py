"""Shared, bounded HTTPS transport; optional controls leave canary calls unchanged."""
from __future__ import annotations

import math
import threading
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser

import requests

REDIRECT_STATUSES = {301, 302, 303, 307, 308}
MAX_REDIRECTS = 3
_last_request: dict[str, float] = {}
_interval_lock = threading.Lock()


class RobotsDisallowed(RuntimeError):
    """The robots policy does not permit this URL."""


def _approved_url(url: str, allowed_hosts: set[str]) -> None:
    try:
        parsed = urlparse(url)
        port = parsed.port
    except ValueError as error:
        raise RuntimeError(f"網址格式無效：{url}") from error
    if (
        parsed.scheme.lower() != "https"
        or parsed.hostname is None
        or parsed.hostname.lower().rstrip(".") not in {host.lower().rstrip(".") for host in allowed_hosts}
        or parsed.username is not None
        or parsed.password is not None
        or port not in (None, 443)
    ):
        raise RuntimeError(f"非白名單來源：{url}")


def _wait_for_host(host: str, interval: float) -> None:
    # ponytail: process-wide lock; use per-host locks if concurrent throughput matters.
    with _interval_lock:
        remaining = _last_request.get(host, -math.inf) + interval - time.monotonic()
        if interval and remaining > 0:
            time.sleep(remaining)
        _last_request[host] = time.monotonic()


def _retry_after(value: str | None) -> float:
    if not value:
        return 0
    try:
        seconds = min(60, int(value))
    except ValueError:
        try:
            when = parsedate_to_datetime(value)
            if when.tzinfo is None:
                when = when.replace(tzinfo=timezone.utc)
            seconds = (when - datetime.now(timezone.utc)).total_seconds()
        except (TypeError, ValueError, OverflowError):
            return 0
    return max(0, min(60, seconds))


class RobotsPolicy:
    """Cache robots per host for this policy's lifetime.

    fetcher(url) returns robots text. On failure, deny configured path prefixes
    (default: all paths). No bearer token is sent when fetching robots.
    """

    def __init__(self, fetcher=None, *, forbidden_prefixes=("/",), user_agent="*"):
        if any(not prefix.startswith("/") for prefix in forbidden_prefixes):
            raise ValueError("robots fallback prefixes must start with /")
        self.fetcher = fetcher
        self.forbidden_prefixes = tuple(forbidden_prefixes)
        self.user_agent = user_agent
        self._cache: dict[str, RobotFileParser | None] = {}

    def check(self, session, url, allowed_hosts, timeout, min_interval_seconds=0) -> float:
        _approved_url(url, allowed_hosts)
        parsed = urlparse(url)
        host = parsed.hostname.lower().rstrip(".")
        if host not in self._cache:
            robots_url = f"https://{host}/robots.txt"
            try:
                if self.fetcher is None:
                    response = get(session, robots_url, {host}, timeout,
                                   min_interval_seconds=min_interval_seconds, max_bytes=256 * 1024)
                    try:
                        body = response.content.decode("utf-8-sig")
                    finally:
                        response.close()
                else:
                    body = self.fetcher(robots_url)
                if not isinstance(body, str) or not body.strip() or body.lstrip().startswith("<"):
                    raise ValueError("invalid robots response")
                parser = RobotFileParser(robots_url)
                parser.parse(body.splitlines())
                self._cache[host] = parser
            except (requests.RequestException, RuntimeError, ValueError, OSError):
                self._cache[host] = None
        parser = self._cache[host]
        if parser is None:
            if (parsed.path or "/").startswith(self.forbidden_prefixes):
                raise RobotsDisallowed(f"robots unavailable; forbidden prefix: {url}")
            return 0
        if not parser.can_fetch(self.user_agent, url):
            raise RobotsDisallowed(f"robots disallow: {url}")
        return parser.crawl_delay(self.user_agent) or 0


def get(session: requests.Session, url: str, allowed_hosts: set[str], timeout: int = 60, *,
        min_interval_seconds: float = 0, bearer_token: str | None = None,
        max_bytes: int | None = None, robots_policy: RobotsPolicy | None = None) -> requests.Response:
    _approved_url(url, allowed_hosts)
    if not math.isfinite(min_interval_seconds) or min_interval_seconds < 0:
        raise ValueError("min_interval_seconds must be finite and nonnegative")
    if max_bytes is not None and (type(max_bytes) is not int or max_bytes <= 0):
        raise ValueError("max_bytes must be a positive integer")
    token_host = urlparse(url).hostname.lower().rstrip(".")
    for attempt in range(3):
        current_url = url
        response = None
        try:
            for _ in range(MAX_REDIRECTS + 1):
                _approved_url(current_url, allowed_hosts)
                host = urlparse(current_url).hostname.lower().rstrip(".")
                interval = min_interval_seconds
                if robots_policy is not None:
                    interval = max(interval, robots_policy.check(
                        session, current_url, allowed_hosts, timeout, min_interval_seconds))
                _wait_for_host(host, interval)
                kwargs = {"timeout": timeout, "allow_redirects": False}
                if bearer_token is not None and host == token_host:
                    kwargs["headers"] = {"Authorization": f"Bearer {bearer_token}"}
                if max_bytes is not None:
                    kwargs["stream"] = True
                response = session.get(current_url, **kwargs)
                _approved_url(str(response.url or current_url), allowed_hosts)
                if response.status_code in REDIRECT_STATUSES:
                    location = response.headers.get("location")
                    response.close()
                    if not location:
                        raise RuntimeError("redirect response 沒有 location")
                    current_url = urljoin(current_url, location)
                    continue
                response.raise_for_status()
                if max_bytes is not None:
                    body = bytearray()
                    for chunk in response.iter_content(chunk_size=min(64 * 1024, max_bytes + 1)):
                        if len(body) + len(chunk) > max_bytes:
                            raise ValueError("response exceeds max_bytes")
                        body.extend(chunk)
                    response._content = bytes(body)
                    response._content_consumed = True
                    response.close()
                if not response.content:
                    raise requests.RequestException("HTTP 成功但內容為空")
                response._govintel_requested_url = url
                return response
            raise RuntimeError("redirect budget exhausted")
        except requests.RequestException as exc:
            status = exc.response.status_code if exc.response is not None else None
            retry_after = _retry_after(response.headers.get("Retry-After")) if response is not None else 0
            if response is not None:
                response.close()
            if attempt == 2 or (status is not None and status != 429 and status < 500):
                raise
            time.sleep(max(attempt + 1, retry_after))
        except Exception:
            if response is not None:
                response.close()
            raise
