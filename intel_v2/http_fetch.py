"""Shared, bounded HTTPS transport; optional controls leave canary calls unchanged."""
from __future__ import annotations

import math
import threading
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import TYPE_CHECKING
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser

if TYPE_CHECKING:
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
    """Cache robots per source; failures deny all paths except documented API 5xx.

    An optional fetcher(url) returns robots text (an HTTP 2xx response).
    """

    MAX_REDIRECTS = 5
    MAX_BYTES = 256 * 1024
    ALLOWED = {"PARSED", "EMPTY_ROBOTS", "NO_ROBOTS_RESOURCE", "HTML_NOT_ROBOTS",
               "ROBOTS_SERVER_ERROR_OPEN_API_ALLOWED"}

    def __init__(self, fetcher=None, *, user_agent="*", documented_open_api=False):
        if type(documented_open_api) is not bool:
            raise ValueError("documented_open_api must be boolean")
        self.fetcher = fetcher
        self.user_agent = user_agent
        self.documented_open_api = documented_open_api
        self._cache: dict[str, tuple[str, RobotFileParser | None]] = {}

    @staticmethod
    def evaluate(url, status, body, *, documented_open_api=False):
        if status is not None and 400 <= status < 500:
            return "NO_ROBOTS_RESOURCE", None
        if status is not None and 500 <= status < 600:
            return ("ROBOTS_SERVER_ERROR_OPEN_API_ALLOWED" if documented_open_api is True
                    else "ROBOTS_SERVER_ERROR"), None
        if status is None or not 200 <= status < 300 or not isinstance(body, str):
            return "UNVERIFIED", None
        if not body.strip():
            return "EMPTY_ROBOTS", None
        if body.lstrip().startswith("<"):
            # RFC 9309: an HTML page (soft 404 / SPA fallback) holds no robots groups, so no rules apply.
            return "HTML_NOT_ROBOTS", None
        lines = body.splitlines()
        directives = [line.split("#", 1)[0].strip().split(":", 1) for line in lines]
        rules = [(p[0].strip().lower(), p[1].strip()) for p in directives if len(p) == 2]
        # ponytail: stdlib lacks wildcard/longest-match rules; deny until a full parser is needed.
        if (not any(key == "user-agent" for key, _ in rules)
                or any(key in ("allow", "disallow") and ("*" in value or "$" in value)
                       for key, value in rules)
                or {key for key, value in rules if value} >= {"allow", "disallow"}):
            return "UNVERIFIED", None
        parser = RobotFileParser(url)
        parser.parse(lines)
        return "PARSED", parser

    @staticmethod
    def redirect_target(url, location, followed):
        if not location or followed >= RobotsPolicy.MAX_REDIRECTS:
            raise RuntimeError("robots redirect missing location or budget exhausted")
        target = urljoin(url, location)
        parsed = urlparse(target)
        if parsed.scheme == "http" and parsed.hostname == urlparse(url).hostname and parsed.port is None:
            target = parsed._replace(scheme="https").geturl()  # same-host downgrade: stay on HTTPS
        _approved_url(target, {urlparse(target).hostname or ""})
        return target

    def _fetch(self, session, url, timeout, interval):
        current = url
        for followed in range(self.MAX_REDIRECTS + 1):
            host = urlparse(current).hostname
            _approved_url(current, {host})
            _wait_for_host(host, interval)
            response = session.get(current, timeout=timeout, allow_redirects=False, stream=True)
            try:
                _approved_url(str(response.url or current), {host})
                status = response.status_code
                if 300 <= status < 400:
                    if self.redirect_is_absent(current, status, response.headers.get("Location")):
                        return "NO_ROBOTS_RESOURCE", None
                    current = self.redirect_target(current, response.headers.get("Location"), followed)
                    continue
                body = bytearray()
                if 200 <= status < 300:
                    for chunk in response.iter_content(chunk_size=64 * 1024):
                        if len(body) + len(chunk) > self.MAX_BYTES:
                            raise ValueError("robots response exceeds max_bytes")
                        body.extend(chunk)
                return self.evaluate(url, status, body.decode("utf-8-sig"),
                                     documented_open_api=self.documented_open_api)
            finally:
                response.close()

    def redirect_is_absent(self, url, status, location):
        return False

    def check(self, session, url, allowed_hosts, timeout, min_interval_seconds=0) -> float:
        import requests

        _approved_url(url, allowed_hosts)
        parsed = urlparse(url)
        host = parsed.hostname.lower().rstrip(".")
        if host not in self._cache:
            robots_url = f"https://{host}/robots.txt"
            try:
                if self.fetcher is None:
                    self._cache[host] = self._fetch(session, robots_url, timeout, min_interval_seconds)
                else:
                    self._cache[host] = self.evaluate(robots_url, 200, self.fetcher(robots_url),
                                                     documented_open_api=self.documented_open_api)
            except (requests.RequestException, RuntimeError, ValueError, OSError):
                self._cache[host] = "UNVERIFIED", None
        state, parser = self._cache[host]
        if state not in self.ALLOWED:
            raise RobotsDisallowed(f"{state}: {url}")
        if parser and not parser.can_fetch(self.user_agent, url):
            raise RobotsDisallowed(f"robots disallow: {url}")
        return (parser.crawl_delay(self.user_agent) or 0) if parser else 0


def get(session: requests.Session, url: str, allowed_hosts: set[str], timeout: int = 60, *,
        min_interval_seconds: float = 0, bearer_token: str | None = None,
        max_bytes: int | None = None, robots_policy: RobotsPolicy | None = None,
        method: str = "GET", json_body=None, form_body=None, allow_empty=False,
        allowed_paths=None, output_file=None) -> requests.Response:
    """Optionally replace a seekable binary file with bounded response chunks.

    The caller owns the file; retries replace partial content, failures clear it.
    In file mode the returned response has no buffered body.
    """
    import requests

    _approved_url(url, allowed_hosts)
    if method not in {"GET", "POST"}:
        raise ValueError("method must be GET or POST")
    if form_body is not None and (json_body is not None or method != "POST"):
        raise ValueError("form_body requires POST and cannot accompany json_body")
    if not math.isfinite(min_interval_seconds) or min_interval_seconds < 0:
        raise ValueError("min_interval_seconds must be finite and nonnegative")
    if max_bytes is not None and (type(max_bytes) is not int or max_bytes <= 0):
        raise ValueError("max_bytes must be a positive integer")
    if output_file is not None and max_bytes is None:
        raise ValueError("output_file requires max_bytes")
    token_host = urlparse(url).hostname.lower().rstrip(".")
    for attempt in range(3):
        current_url = url
        response = None
        try:
            if output_file is not None:
                output_file.seek(0)
                output_file.truncate()
            for _ in range(MAX_REDIRECTS + 1):
                _approved_url(current_url, allowed_hosts)
                if allowed_paths is not None and urlparse(current_url).path not in allowed_paths:
                    raise RuntimeError("request path outside adapter allowlist")
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
                body = {"data": form_body} if form_body is not None else {"json": json_body}
                response = (session.post(current_url, **body, **kwargs) if method == "POST"
                            else session.get(current_url, **kwargs))
                _approved_url(str(response.url or current_url), allowed_hosts)
                if method == "POST" and 300 <= response.status_code < 400:
                    raise RuntimeError("POST redirect forbidden")
                if response.status_code in REDIRECT_STATUSES:
                    location = response.headers.get("location")
                    response.close()
                    if not location:
                        raise RuntimeError("redirect response 沒有 location")
                    current_url = urljoin(current_url, location)
                    continue
                response.raise_for_status()
                if max_bytes is not None:
                    body = bytearray() if output_file is None else None
                    received = 0
                    for chunk in response.iter_content(chunk_size=min(64 * 1024, max_bytes + 1)):
                        received += len(chunk)
                        if received > max_bytes:
                            raise ValueError("response exceeds max_bytes")
                        if output_file is None:
                            body.extend(chunk)
                        else:
                            output_file.write(chunk)
                    response._content = bytes(body) if output_file is None else b""
                    response._content_consumed = True
                    response.close()
                if not allow_empty and (output_file is not None and not received
                                        or output_file is None and not response.content):
                    raise requests.RequestException("HTTP 成功但內容為空")
                if output_file is not None:
                    output_file.seek(0)
                response._govintel_requested_url = url
                return response
            raise RuntimeError("redirect budget exhausted")
        except requests.RequestException as exc:
            if output_file is not None:
                output_file.seek(0)
                output_file.truncate()
            status = exc.response.status_code if exc.response is not None else None
            retry_after = _retry_after(response.headers.get("Retry-After")) if response is not None else 0
            if response is not None:
                response.close()
            if attempt == 2 or (status is not None and status != 429 and status < 500):
                raise
            time.sleep(max(attempt + 1, retry_after))
        except Exception:
            if output_file is not None:
                output_file.seek(0)
                output_file.truncate()
            if response is not None:
                response.close()
            raise
