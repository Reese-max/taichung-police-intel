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

    def request(self, url, *, max_bytes, method="GET", json_body=None):
        if (self.source["status"] == "BLOCKED_BY_ROBOTS_NEEDS_CONSENT"
                or self.robots["status"] not in http_fetch.RobotsPolicy.ALLOWED
                and not (self.robots["status"] == "ROBOTS_SERVER_ERROR"
                         and self.source["documented_open_api"])):
            raise http_fetch.RobotsDisallowed("registry robots state: " + self.robots["status"])
        if "https://" + urlsplit(url).hostname in self.robots.get("blocked_origins", []):
            raise http_fetch.RobotsDisallowed("registry blocks origin")
        response = http_fetch.get(
            self.session, url, self.allowed_hosts,
            min_interval_seconds=self.source["min_request_interval_seconds"],
            max_bytes=max_bytes, robots_policy=self.policy, method=method, json_body=json_body)
        try:
            payload = response.json()
            if not isinstance(payload, dict):
                raise ValueError("API response must be an object")
            return payload
        finally:
            response.close()

    def close(self):
        self.session.close()
