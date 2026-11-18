"""Registry-governed transport for official national metadata adapters."""
from urllib.parse import urlsplit

import requests

from intel_v2 import http_fetch

USER_AGENT = "GovIntelNationalBot/1.0 (+https://github.com/Reese-max/taichung-police-intel)"


def text(row, key):
    value = row.get(key)
    if value is None:
        return ""
    if not isinstance(value, (str, int)) or isinstance(value, bool):
        raise ValueError("invalid API field: " + key)
    return str(value)


class HttpAdapter:
    def __init__(self, source, *, session=None):
        self.source = source
        self.session = session if session is not None else requests.Session()
        self.session.headers.update({"User-Agent": USER_AGENT})
        self.allowed_hosts = {urlsplit(origin).hostname for origin in source["allowed_hosts"]}
        self.robots = source["robots"]
        self.policy = http_fetch.RobotsPolicy(
            user_agent=USER_AGENT, documented_open_api=source["documented_open_api"])
        self.warnings = []

    def response(self, url, *, max_bytes, method="GET", json_body=None, form_body=None):
        if (self.source["status"] == "BLOCKED_BY_ROBOTS_NEEDS_CONSENT"
                or self.robots["status"] not in http_fetch.RobotsPolicy.ALLOWED
                and not (self.robots["status"] == "ROBOTS_SERVER_ERROR"
                         and self.source["documented_open_api"])):
            raise http_fetch.RobotsDisallowed("registry robots state: " + self.robots["status"])
        if "https://" + urlsplit(url).hostname in self.robots.get("blocked_origins", []):
            raise http_fetch.RobotsDisallowed("registry blocks origin")
        return http_fetch.get(
            self.session, url, self.allowed_hosts,
            min_interval_seconds=self.source["min_request_interval_seconds"],
            max_bytes=max_bytes, robots_policy=self.policy, method=method,
            json_body=json_body, form_body=form_body)

    def request(self, url, *, max_bytes, method="GET", json_body=None):
        response = self.response(url, max_bytes=max_bytes, method=method, json_body=json_body)
        try:
            payload = response.json()
            if not isinstance(payload, dict):
                raise ValueError("API response must be an object")
            return payload
        finally:
            response.close()

    def close(self):
        self.session.close()


class SnapshotAdapter(HttpAdapter):
    """Validate bounded metadata before exposing resumable pages to the loader."""

    MAX_BYTES = 8 * 1024 * 1024

    def __init__(self, source, *, session=None):
        super().__init__(source, session=session)
        self._pages = None

    def read(self, url, form=None):
        response = self.response(url, max_bytes=self.MAX_BYTES,
                                 method="POST" if form is not None else "GET", form_body=form)
        try:
            return response.content.decode("utf-8-sig")
        finally:
            response.close()

    def fetch(self, source, cursor):
        from national.loader import normalize

        if self._pages is None or cursor is None:
            self.warnings = []
            pages = self._snapshot(source)
            ids, keys = set(), set()
            for key, records in pages:
                if key in keys:
                    raise ValueError("duplicate snapshot cursor")
                keys.add(key)
                for record in records:
                    normalize(source, record)
                    if record["external_id"] in ids:
                        raise ValueError("duplicate snapshot external_id")
                    ids.add(record["external_id"])
            self._pages = pages
        if not self._pages and cursor is None:
            self._pages = None
            return [], None
        keys = [key for key, _ in self._pages]
        if cursor is not None and cursor not in keys:
            raise ValueError("resume cursor no longer exists")
        index = 0 if cursor is None else keys.index(cursor)
        records = self._pages[index][1]
        next_cursor = keys[index + 1] if index + 1 < len(keys) else None
        if next_cursor is None:
            self._pages = None
        return records, next_cursor
