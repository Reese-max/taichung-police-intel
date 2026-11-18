import unittest
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from unittest.mock import Mock, patch

import requests

from intel_v2 import http_fetch as http


URL = "https://official.test/start"
HOSTS = {"official.test"}


def response(status=200, *, url=URL, body=b"ok", headers=None, chunks=None):
    result = requests.Response()
    result.status_code = status
    result.url = url
    result._content = body
    result._content_consumed = True
    result.headers.update(headers or {})
    result.close = Mock()
    if chunks is not None:
        result.iter_content = Mock(return_value=iter(chunks))
    return result


class HttpFetchTests(unittest.TestCase):
    def setUp(self):
        state = patch.object(http, "_last_request", {})
        state.start()
        self.addCleanup(state.stop)

    def test_rejects_unapproved_https_port_and_credentials_before_send(self):
        for url in ("http://official.test/", "https://official.test:444/", "https://other.test/",
                    "https://user@official.test/", "https://@official.test/"):
            session = Mock()
            with self.subTest(url=url), self.assertRaisesRegex(RuntimeError, "非白名單來源"):
                http.get(session, url, HOSTS, bearer_token="private-token")
            session.get.assert_not_called()

    def test_redirect_escape_never_receives_token(self):
        redirect = response(302, headers={"Location": "https://outside.test/"})
        session = Mock(get=Mock(return_value=redirect))
        with self.assertRaisesRegex(RuntimeError, "非白名單來源"):
            http.get(session, URL, HOSTS, bearer_token="private-token")
        session.get.assert_called_once_with(URL, timeout=60, allow_redirects=False,
                                            headers={"Authorization": "Bearer private-token"})
        redirect.close.assert_called()

    def test_token_is_not_forwarded_even_to_a_second_allowlisted_host(self):
        session = Mock(get=Mock(side_effect=[response(302, headers={"Location": "https://cdn.test/file"}),
                                            response(url="https://cdn.test/file")]))
        result = http.get(session, URL, HOSTS | {"cdn.test"}, bearer_token="private-token")
        self.assertNotIn("headers", session.get.call_args_list[1].kwargs)
        self.assertEqual(result._govintel_requested_url, URL)

    def test_redirect_budget_is_three_and_default_call_is_unchanged(self):
        session = Mock(get=Mock(side_effect=[response(302, headers={"Location": "/next"}) for _ in range(4)]))
        with self.assertRaisesRegex(RuntimeError, "redirect budget exhausted"):
            http.get(session, URL, HOSTS)
        self.assertEqual(session.get.call_count, 4)
        self.assertEqual(session.get.call_args_list[0].kwargs, {"timeout": 60, "allow_redirects": False})

    def test_post_never_follows_any_redirect(self):
        for status in (300, 301, 302, 303, 304, 307, 308):
            reply = response(status, headers={"Location": "/next"})
            session = Mock(post=Mock(return_value=reply))
            with self.subTest(status=status), self.assertRaisesRegex(RuntimeError, "POST redirect forbidden"):
                http.get(session, URL, HOSTS, method="POST", json_body={})
            session.post.assert_called_once_with(URL, json={}, timeout=60, allow_redirects=False)
            session.get.assert_not_called()
            reply.close.assert_called()

    def test_post_reuses_transport_guards_and_retry(self):
        session = Mock(post=Mock(side_effect=[response(503), response(body=b"{}")]))
        with patch.object(http.time, "sleep"):
            self.assertEqual(http.get(session, URL, HOSTS, method="POST", json_body={}, max_bytes=2).content, b"{}")
        self.assertEqual(session.post.call_count, 2)
        self.assertTrue(session.post.call_args.kwargs["stream"])
        with self.assertRaisesRegex(ValueError, "max_bytes"):
            http.get(Mock(post=Mock(return_value=response(body=b"123"))), URL, HOSTS, method="POST", max_bytes=2)
        blocked = Mock()
        with self.assertRaises(http.RobotsDisallowed):
            http.get(blocked, URL, HOSTS, method="POST", robots_policy=http.RobotsPolicy(
                Mock(return_value="User-agent: *\nDisallow: /")))
        blocked.post.assert_not_called()
        with self.assertRaises(RuntimeError):
            http.get(blocked, "https://outside.test/", HOSTS, method="POST")
        with self.assertRaises(ValueError):
            http.get(blocked, URL, HOSTS, method="DELETE")

    def test_success_after_three_redirects_and_response_url_revalidated(self):
        replies = [response(302, headers={"Location": "/next"}) for _ in range(3)]
        session = Mock(get=Mock(side_effect=replies + [response(url="https://official.test/next")]))
        self.assertEqual(http.get(session, URL, HOSTS)._govintel_requested_url, URL)
        invalid = response(url="https://outside.test/final")
        with self.assertRaisesRegex(RuntimeError, "非白名單來源"):
            http.get(Mock(get=Mock(return_value=invalid)), URL, HOSTS)
        invalid.close.assert_called()

    def test_retry_after_seconds_date_and_cap(self):
        now = datetime(2026, 11, 17, tzinfo=timezone.utc)
        for value, expected in (("8", 8), ("999", 60),
                                (format_datetime(now + timedelta(seconds=12), usegmt=True), 12),
                                ("bad-date", 1), ("-2", 1)):
            session = Mock(get=Mock(side_effect=[response(429, headers={"Retry-After": value}), response()]))
            with self.subTest(value=value), patch.object(http, "datetime") as clock, patch.object(http.time, "sleep") as sleep:
                clock.now.return_value = now
                http.get(session, URL, HOSTS)
                sleep.assert_called_once_with(expected)

    def test_retry_5xx_empty_and_connection_errors_but_not_4xx(self):
        for bad in (response(503), response(body=b""), requests.ConnectionError("offline")):
            session = Mock(get=Mock(side_effect=[bad, response()]))
            with self.subTest(bad=bad), patch.object(http.time, "sleep") as sleep:
                self.assertEqual(http.get(session, URL, HOSTS).content, b"ok")
                sleep.assert_called_once_with(1)
        session = Mock(get=Mock(return_value=response(403)))
        with self.assertRaises(requests.HTTPError):
            http.get(session, URL, HOSTS)
        self.assertEqual(session.get.call_count, 1)
        session = Mock(get=Mock(side_effect=[response(500) for _ in range(3)]))
        with patch.object(http.time, "sleep"), self.assertRaises(requests.HTTPError):
            http.get(session, URL, HOSTS)
        self.assertEqual(session.get.call_count, 3)

    def test_interval_shared_between_calls_and_sessions_but_separate_hosts(self):
        now = [100.0]
        def sleep(seconds):
            now[0] += seconds
        with patch.object(http.time, "monotonic", side_effect=lambda: now[0]), patch.object(http.time, "sleep", side_effect=sleep) as wait:
            for url in (URL, URL, "https://second.test/"):
                http.get(Mock(get=Mock(return_value=response(url=url))), url,
                         HOSTS | {"second.test"}, min_interval_seconds=3)
            wait.assert_called_once_with(3)

    def test_stream_limit_enforced_without_content_length_and_closes(self):
        oversized = response(chunks=[b"12", b"345"])
        session = Mock(get=Mock(return_value=oversized))
        with self.assertRaisesRegex(ValueError, "max_bytes"):
            http.get(session, URL, HOSTS, max_bytes=4)
        self.assertTrue(session.get.call_args.kwargs["stream"])
        self.assertEqual(session.get.call_count, 1)
        oversized.close.assert_called()
        bounded = response(chunks=[b"12", b"34"])
        result = http.get(Mock(get=Mock(return_value=bounded)), URL, HOSTS, max_bytes=4)
        self.assertEqual(result.content, b"1234")
        bounded.close.assert_called()

    def test_robots_disallow_and_cache(self):
        fetcher = Mock(return_value="User-agent: *\nDisallow: /api/\nCrawl-delay: 3\n")
        policy = http.RobotsPolicy(fetcher)
        session = Mock()
        for path in ("/api/bills", "/api/meets"):
            with self.assertRaises(http.RobotsDisallowed):
                http.get(session, "https://official.test" + path, HOSTS,
                         robots_policy=policy, bearer_token="private-token")
        session.get.assert_not_called()
        fetcher.assert_called_once_with("https://official.test/robots.txt")

    def test_robots_failure_denies_all_even_for_open_api(self):
        for fetcher in (Mock(side_effect=requests.ConnectionError("offline")), Mock(return_value="User-agent *\nDisallow /")):
            policy = http.RobotsPolicy(fetcher, documented_open_api=True)
            session = Mock(get=Mock(return_value=response()))
            for url in (URL, "https://official.test/api/bills"):
                with self.assertRaises(http.RobotsDisallowed):
                    http.get(session, url, HOSTS, robots_policy=policy)
            session.get.assert_not_called()
            self.assertEqual(fetcher.call_count, 1)
        with self.assertRaises(http.RobotsDisallowed):
            http.get(Mock(), URL, HOSTS, robots_policy=http.RobotsPolicy(Mock(side_effect=RuntimeError())))

    def test_robots_http_decisions_and_explicit_disallow(self):
        robots_url = "https://official.test/robots.txt"
        for status, body, open_api, allowed, state in (
                (200, b"", False, True, "EMPTY_ROBOTS"),
                (204, b"\r\n", False, True, "EMPTY_ROBOTS"),
                (404, b"", False, True, "NO_ROBOTS_RESOURCE"),
                (403, b"", False, True, "NO_ROBOTS_RESOURCE"),
                (429, b"", False, True, "NO_ROBOTS_RESOURCE"),
                (500, b"", True, True, "ROBOTS_SERVER_ERROR_OPEN_API_ALLOWED"),
                (503, b"", False, False, "ROBOTS_SERVER_ERROR"),
                (200, b"<!doctype html><html>SPA fallback</html>", False, True, "HTML_NOT_ROBOTS"),
                (200, b"User-agent: *\nDisallow: /", True, False, "PARSED")):
            with self.subTest(status=status, open_api=open_api, body=body):
                policy = http.RobotsPolicy(documented_open_api=open_api)
                reply = response(status, url=robots_url, body=body)
                session = Mock(get=Mock(return_value=reply))
                if allowed:
                    self.assertEqual(policy.check(session, URL, HOSTS, 10), 0)
                else:
                    with self.assertRaises(http.RobotsDisallowed):
                        policy.check(session, URL, HOSTS, 10)
                self.assertEqual(policy._cache["official.test"][0], state)
                self.assertEqual(session.get.call_count, 1)
                reply.close.assert_called()

    def test_robots_redirects_cross_host_to_404_or_rules_and_enforce_budget(self):
        robots_url = "https://official.test/robots.txt"
        target = "https://cdn.test/policy"
        for status, body, allowed in ((404, b"", True), (200, b"", True),
                                      (200, b"User-agent: *\nDisallow: /", False)):
            replies = [response(302, url=robots_url, headers={"Location": target})]
            replies += [response(302, url=target, headers={"Location": "/policy"}) for _ in range(4)]
            replies += [response(status, url=target, body=body)]
            session = Mock(get=Mock(side_effect=replies))
            policy = http.RobotsPolicy(documented_open_api=True)
            with self.subTest(status=status, body=body):
                if allowed:
                    policy.check(session, URL, HOSTS, 10)
                else:
                    with self.assertRaises(http.RobotsDisallowed):
                        policy.check(session, URL, HOSTS, 10)
                self.assertEqual(session.get.call_count, 6)
                self.assertTrue(all(r.close.called for r in replies))
                self.assertTrue(all("headers" not in c.kwargs for c in session.get.call_args_list))
        for location, count in (("/loop", 6), ("http://cdn.test/policy", 1),
                                ("https://user@cdn.test/policy", 1), (None, 1)):
            session = Mock(get=Mock(return_value=response(302, url=robots_url,
                                                          headers={"Location": location} if location else {})))
            with self.subTest(location=location), self.assertRaises(http.RobotsDisallowed):
                http.RobotsPolicy(documented_open_api=True).check(session, URL, HOSTS, 10)
            self.assertEqual(session.get.call_count, count)

    def test_robots_same_host_http_redirect_is_upgraded_to_https(self):
        robots_url = "https://official.test/robots.txt"
        replies = [response(302, url=robots_url, headers={"Location": "http://official.test/home"}),
                   response(200, url="https://official.test/home", body=b"<html>home</html>")]
        session = Mock(get=Mock(side_effect=replies))
        policy = http.RobotsPolicy()
        self.assertEqual(policy.check(session, URL, HOSTS, 10), 0)
        self.assertEqual(session.get.call_args_list[1].args[0], "https://official.test/home")
        self.assertEqual(policy._cache["official.test"][0], "HTML_NOT_ROBOTS")

    def test_robots_body_limit_is_fail_closed(self):
        reply = response(url="https://official.test/robots.txt", chunks=[b"x" * (http.RobotsPolicy.MAX_BYTES + 1)])
        with self.assertRaises(http.RobotsDisallowed):
            http.RobotsPolicy(documented_open_api=True).check(Mock(get=Mock(return_value=reply)), URL, HOSTS, 10)
        reply.close.assert_called()

    def test_default_robots_fetch_is_bounded_tokenless_and_crawl_delay_applies(self):
        robots = b"User-agent: *\nDisallow: /api/\nCrawl-delay: 3\n"
        session = Mock(get=Mock(side_effect=[response(url="https://official.test/robots.txt", chunks=[robots]), response()]))
        with patch.object(http.time, "monotonic", return_value=100), patch.object(http.time, "sleep") as sleep:
            http.get(session, URL, HOSTS, bearer_token="private-token", robots_policy=http.RobotsPolicy())
        self.assertNotIn("headers", session.get.call_args_list[0].kwargs)
        self.assertTrue(session.get.call_args_list[0].kwargs["stream"])
        sleep.assert_called_once_with(3)

    def test_robots_rechecked_after_redirect(self):
        policy = http.RobotsPolicy(Mock(return_value="User-agent: *\nDisallow: /api/\n"))
        session = Mock(get=Mock(return_value=response(302, headers={"Location": "/api/bills"})))
        with self.assertRaises(http.RobotsDisallowed):
            http.get(session, URL, HOSTS, robots_policy=policy)
        self.assertEqual(session.get.call_count, 1)


if __name__ == "__main__":
    unittest.main()
