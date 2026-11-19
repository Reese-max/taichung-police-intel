from copy import deepcopy
from http.client import IncompleteRead
import importlib.util
import json
from pathlib import Path
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError, URLError

ROOT = Path(__file__).resolve().parents[1]


def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


sources = load("national-sources")
preflight = load("national-preflight")


class NationalSourcesTests(unittest.TestCase):
    def setUp(self):
        self.registry = json.loads(sources.REGISTRY.read_text(encoding="utf-8"))
        self.rows = {r["national_source_id"]: r for r in self.registry["sources"]}

    def test_registry_has_22_councils_and_bounded_budget_with_unknowns(self):
        report = sources.validate(self.registry)
        self.assertEqual(report["source_count"], 29)
        self.assertEqual(report["capacity_estimate_mb"], 286.71)
        self.assertEqual(report["unknown_capacity_sources"], 18)
        self.assertEqual(report["capacity_reserve_mb"], 170)
        codes = "TPE NWT TAO TXG TNN KHH KEE HSZ CYI HSQ MIA CHA NAN YUN CYQ PIF ILA HUA TTT PEN KIN LIE".split()
        councils = {sid: row for sid, row in self.rows.items() if sid.startswith("N-COUNCIL-")}
        self.assertEqual(set(councils), {"N-COUNCIL-" + c for c in codes})
        for code, count in {"TXG": 15496, "KEE": 4998, "CYQ": 1246, "KHH": 1636}.items():
            self.assertEqual(councils["N-COUNCIL-" + code]["known_bill_count"], count)
        for row in councils.values():
            self.assertIn(row["transport"], {"OFFICIAL_JSON_API", "HTML", "HTML_HEADLESS", "HTML_FILES"})
            if row["known_bill_count"] is None:
                self.assertIsNone(row["capacity_estimate_mb"])
                self.assertIn("W2 盤點", " ".join(row["notes"]))

    def test_every_required_field_is_checked(self):
        for field in sources.REQUIRED:
            registry = deepcopy(self.registry)
            del registry["sources"][0][field]
            with self.subTest(field=field), self.assertRaises(ValueError):
                sources.validate(registry)

    def test_rejects_unsafe_hosts_fields_and_budget(self):
        invalid = [("allowed_hosts", [host]) for host in
                   ("http://example.org", "https://*.example.org", "https://user@example.org",
                    "https://example.org:444", "https://example.org/path", "example.org")]
        invalid += [("public_fields", [field]) for field in ("full_text", "excerpt", "body", "unknown")]
        invalid += [("capacity_estimate_mb", value) for value in (401, -1, float("nan"), True)]
        invalid += [("endpoints", ["https://outside.test/api"])]
        for field, value in invalid:
            registry = deepcopy(self.registry)
            registry["sources"][0][field] = value
            with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                sources.validate(registry)
        self.registry["capacity_reserve_mb"] = 0
        with self.assertRaisesRegex(ValueError, "reserve"):
            sources.validate(self.registry)

    def test_refresh_days_requires_a_positive_non_boolean_integer(self):
        for value in (0, -1, "7", True):
            registry = deepcopy(self.registry)
            registry["sources"][0]["scope"]["refresh_days"] = value
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "refresh_days"):
                sources.validate(registry)

    def test_rejects_blocked_endpoints_including_chiayi_www(self):
        for sid, nested in [("N-COUNCIL-CYI", None), ("N-COUNCIL-CYQ", "www_access")]:
            registry = deepcopy(self.registry)
            row = next(r for r in registry["sources"] if r["national_source_id"] == sid)
            target = row[nested] if nested else row
            target["endpoints"] = [row["official_url"]]
            with self.subTest(sid=sid), self.assertRaisesRegex(ValueError, "empty endpoints"):
                sources.validate(registry)
        row = self.rows["N-COUNCIL-CYQ"]
        row["allowed_hosts"].append("https://www.cyscc.gov.tw")
        row["endpoints"].append(row["official_url"])
        with self.assertRaisesRegex(ValueError, "blocked origin"):
            sources.validate(self.registry)

    def test_rejects_removed_hosts_even_in_nested_urls_and_notes(self):
        for host in ["ly.govapi.tw", "tpe.cc.govapi.tw", "pcc-api.openfun.app", "huggingface.co", "WWW.HUGGINGFACE.CO"]:
            for field in ["endpoints", "allowed_hosts", "official_url_hosts", "notes"]:
                registry = deepcopy(self.registry)
                registry["sources"][0][field] = ["https://" + host + "/"]
                with self.subTest(host=host, field=field), self.assertRaisesRegex(ValueError, "non-official host"):
                    sources.validate(registry)
            registry = deepcopy(self.registry)
            registry["sources"][0]["scope"]["nested"] = {"url": "https://" + host}
            with self.assertRaisesRegex(ValueError, "non-official host"):
                sources.validate(registry)

    def test_robots_blocked_origin_is_link_only(self):
        for sid in ("N-LY-ID20-BILLS", "N-LY-ID6-INTERP"):
            row = self.rows[sid]
            self.assertIn("https://ppg.ly.gov.tw", row["official_url_hosts"])
            self.assertNotIn("https://ppg.ly.gov.tw", row["allowed_hosts"])
            sources.validate(self.registry)
            row["allowed_hosts"].append("https://ppg.ly.gov.tw")
            with self.assertRaisesRegex(ValueError, "robots-blocked origin"):
                sources.validate(self.registry)
            row["allowed_hosts"].pop()

    def test_source_safety_decisions_are_explicit(self):
        rows = self.rows
        for code in ("TXG", "KEE"):
            self.assertEqual(rows["N-COUNCIL-" + code]["status"], "IMPLEMENTED_API")
        self.assertEqual(rows["N-COUNCIL-CYQ"]["status"], "PLANNED_API")
        self.assertEqual(rows["N-COUNCIL-NWT"]["status"], "PLANNED_HEADLESS")
        self.assertEqual(rows["N-COUNCIL-CYI"]["status"], "BLOCKED_BY_ROBOTS_NEEDS_CONSENT")
        self.assertEqual(rows["N-COUNCIL-CYQ"]["www_access"]["endpoints"], [])
        self.assertEqual(rows["N-COUNCIL-PEN"]["status"], "PARTIAL_ROBOTS")
        self.assertFalse(rows["N-COUNCIL-PEN"]["scope"]["fetch_attachments"])
        self.assertEqual(rows["N-LY-ID6-INTERP"]["known_gap"], "第 11 屆第 03、05、06 會期官方回空（2026-11-18 主對話提供）")
        self.assertEqual(rows["N-PCC-WEB"]["min_request_interval_seconds"], 3)
        self.assertEqual(rows["N-PCC-WEB"]["capacity_estimate_mb"], 40)
        self.assertEqual(rows["N-PCC-GPA"]["scope"]["dataset_ids"], [172020, 172023])
        self.assertFalse(rows["N-ODP-CATALOG"]["scope"]["crawl_city_portals"])
        self.assertFalse(rows["N-TWINKLE-MANUAL"]["scope"]["automated"])
        self.assertEqual(rows["N-TDX-EVENTS"]["scope"]["missing_secret_status"], "NOT_CONFIGURED")

    def test_runner_and_documented_api_constraints(self):
        self.assertEqual({sid for sid, row in self.rows.items() if row["runner"] == "xisx-pc"},
                         {"N-COUNCIL-" + code for code in ("TPE", "NWT", "TAO", "PIF", "ILA")})
        self.assertEqual({sid for sid, row in self.rows.items() if row["documented_open_api"]},
                         {"N-LY-ID20-BILLS", "N-LY-ID6-INTERP", "N-PCC-GPA", "N-ODP-CATALOG"})
        for field, value in (("runner", "unknown"), ("documented_open_api", "true"),
                             ("documented_open_api", 1)):
            registry = deepcopy(self.registry)
            registry["sources"][0][field] = value
            with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                sources.validate(registry)
        self.rows["N-COUNCIL-CYI"]["documented_open_api"] = True
        with self.assertRaisesRegex(ValueError, "cannot override robots block"):
            sources.validate(self.registry)


class PreflightTests(unittest.TestCase):
    def setUp(self):
        runner = patch.dict(preflight.os.environ, {"NATIONAL_RUNNER": "github-actions"})
        runner.start()
        self.addCleanup(runner.stop)

    def configuration(self, pages, robots, open_api_urls=()):
        return pages, robots, set(), {
            url: {"documented_open_api": url in open_api_urls, "national_source_id": "TEST"} for url in pages}

    def response(self, body, status=200):
        response = Mock(status=status)
        response.read.return_value = body
        context = Mock()
        context.__enter__ = Mock(return_value=response)
        context.__exit__ = Mock(return_value=False)
        return context

    def test_dry_run_covers_all_targets_without_network(self):
        with patch.object(preflight, "build_opener", side_effect=AssertionError("network")):
            report = preflight.build_report(dry_run=True)
        preflight.validate_report(json.loads(json.dumps(report)))
        pages, robots, blocked, _ = preflight.configuration()
        self.assertEqual(report["runner"], "github-actions")
        self.assertEqual(len(pages), 22)
        self.assertEqual(len(robots), 22)
        self.assertIn("https://api.cyscc.gov.tw/robots.txt", robots)
        self.assertTrue(any("tender_20260902.xml" in u for u in pages))
        self.assertTrue(all(row["bytes"] == 0 for row in report["results"]))
        blocked_rows = [r for r in report["results"] if r["status"] == "BLOCKED_BY_ROBOTS_NEEDS_CONSENT"]
        self.assertEqual({r["host"] for r in blocked_rows}, {"www.cycc.gov.tw", "www.cyscc.gov.tw"})
        self.assertEqual(len(report["results"]), 44)
        github_urls = {row["url"] for row in report["results"]}
        with patch.dict(preflight.os.environ, {"NATIONAL_RUNNER": "xisx-pc"}):
            local_report = preflight.build_report(dry_run=True)
        self.assertEqual(local_report["runner"], "xisx-pc")
        preflight.validate_report(local_report)
        self.assertEqual(len(local_report["results"]), 10)
        self.assertFalse(github_urls & {row["url"] for row in local_report["results"]})
        with patch.dict(preflight.os.environ, {"NATIONAL_RUNNER": "typo"}), self.assertRaises(ValueError):
            preflight.build_report(dry_run=True)

    def test_known_blocks_only_fetch_robots_even_if_server_now_allows(self):
        opener = Mock(open=Mock(return_value=self.response(b"User-agent: *\nDisallow:\n")))
        with patch.object(preflight.time, "sleep"):
            report = preflight.build_report(opener=opener)
        urls = [call.args[0].full_url for call in opener.open.call_args_list]
        self.assertEqual(len(urls), 42)
        self.assertEqual(len(set(urls)), len(urls))
        for host in ("www.cycc.gov.tw", "www.cyscc.gov.tw", "api.cyscc.gov.tw"):
            self.assertEqual([u for u in urls if preflight.urlsplit(u).hostname == host], [f"https://{host}/robots.txt"])
        self.assertEqual(len([r for r in report["results"] if "robots_content" in r]), 22)

    def test_errors_are_isolated_and_unknown_robots_skip_pages(self):
        pages, robots, _, _ = preflight.configuration()
        opener = Mock()
        opener.open.side_effect = [URLError("offline"), HTTPError(robots[1], 503, "unavailable", {}, None),
                                  IncompleteRead(b"partial")] + [OSError("offline")] * (len(robots) - 3)
        report = preflight.build_report(opener=opener)
        self.assertEqual(opener.open.call_count, len(robots))
        self.assertEqual(report["results"][0]["status"], "UNREACHABLE_FROM_RUNNER")
        self.assertEqual(report["results"][1]["http_status"], 503)
        self.assertEqual(report["results"][2]["error_type"], "IncompleteRead")
        self.assertTrue(all(r["status"] in {"BLOCKED_BY_ROBOTS_NEEDS_CONSENT", "SKIPPED_ROBOTS_UNVERIFIED",
                                           "UNREACHABLE_FROM_RUNNER"}
                            for r in report["results"] if r["kind"] == "PAGE"))
        self.assertIsNone(preflight.NoRedirect().redirect_request(None, None, 302, "", {}, "https://other.test"))

    def test_live_disallow_prevents_all_content_requests(self):
        opener = Mock(open=Mock(return_value=self.response(b"User-agent: *\nDisallow: /\n")))
        report = preflight.build_report(opener=opener)
        self.assertEqual(opener.open.call_count, 22)
        self.assertTrue(all(r["status"] == "BLOCKED_BY_ROBOTS_NEEDS_CONSENT"
                            for r in report["results"] if r["kind"] == "PAGE"))

    def test_missing_robots_allows_but_complex_or_invalid_robots_do_not(self):
        row = preflight.observation("https://example.org/robots.txt", "HTTP_ERROR")
        row["http_status"] = 404
        self.assertEqual(preflight.robots_policy(row), ("NO_ROBOTS_RESOURCE", None))
        row.update(status="OK", http_status=200, robots_content="<html>home</html>")
        self.assertEqual(preflight.robots_policy(row), ("HTML_NOT_ROBOTS", None))
        for body in ("oops", "User-agent: *\nDisallow: /*.pdf$", "User-agent: *\nAllow: /\nDisallow: /private"):
            row.update(status="OK", http_status=200, robots_content=body)
            self.assertEqual(preflight.robots_policy(row), ("UNVERIFIED", None))

    def test_robots_redirect_to_404_and_three_second_spacing(self):
        pages = ["https://web.pcc.gov.tw/", "https://web.pcc.gov.tw/tps/tp/OpenData/downloadFile?fileName=GPAtender_20261117.json"]
        robots = ["https://web.pcc.gov.tw/robots.txt"]
        opener = Mock()
        opener.open.side_effect = [HTTPError(robots[0], 302, "redirect", {"Location": "/"}, None),
                                  HTTPError(pages[0], 404, "missing", {}, None),
                                  self.response(b"home"), self.response(b"[]")]
        with patch.object(preflight, "configuration", return_value=self.configuration(pages, robots)), \
                patch.object(preflight.time, "monotonic", return_value=100), patch.object(preflight.time, "sleep") as sleep:
            report = preflight.build_report(opener=opener)
        self.assertEqual(report["results"][0]["robots_policy"], "NO_ROBOTS_RESOURCE")
        self.assertEqual(report["results"][0]["final_url"], pages[0])
        self.assertEqual(len(report["results"][0]["redirects"]), 1)
        self.assertEqual([c.args[0] for c in sleep.call_args_list], [3, 3, 3])
        self.assertEqual(opener.open.call_count, 4)

    def test_crawl_delay_is_respected(self):
        pages, robots = ["https://example.org/"], ["https://example.org/robots.txt"]
        opener = Mock(open=Mock(return_value=self.response(b"User-agent: *\nDisallow:\nCrawl-delay: 7")))
        with patch.object(preflight, "configuration", return_value=self.configuration(pages, robots)), \
                patch.object(preflight.time, "monotonic", return_value=100), patch.object(preflight.time, "sleep") as sleep:
            preflight.build_report(opener=opener)
        sleep.assert_called_once_with(7)

    def test_empty_robots_and_source_scoped_open_api_exception(self):
        pages = ["https://web.pcc.gov.tw/", "https://web.pcc.gov.tw/tps/tp/OpenData/downloadFile"]
        robots = ["https://web.pcc.gov.tw/robots.txt"]
        for reply, expected, count in (
                (self.response(b""), ["OK", "OK"], 3),
                (HTTPError(robots[0], 500, "error", {}, None), ["SKIPPED_ROBOTS_UNVERIFIED", "OK"], 2),
                (self.response(b"User-agent: *\nDisallow: /"), ["BLOCKED_BY_ROBOTS_NEEDS_CONSENT"] * 2, 1),
                (TimeoutError("timeout"), ["UNREACHABLE_FROM_RUNNER"] * 2, 1)):
            opener = Mock(open=Mock(side_effect=[reply, self.response(b"page"), self.response(b"api")]))
            with self.subTest(expected=expected), patch.object(preflight, "configuration",
                    return_value=self.configuration(pages, robots, [pages[1]])), patch.object(preflight.time, "sleep"):
                report = preflight.build_report(opener=opener)
            self.assertEqual([row["status"] for row in report["results"][1:]], expected)
            self.assertEqual(opener.open.call_count, count)
            if isinstance(reply, HTTPError):
                self.assertEqual(report["results"][2]["robots_policy"], "ROBOTS_SERVER_ERROR_OPEN_API_ALLOWED")
                self.assertEqual(opener.open.call_args.args[0].full_url, pages[1])

    def test_robots_redirect_limits_https_and_final_policy(self):
        pages, robots = ["https://example.org/private"], ["https://example.org/robots.txt"]
        target = "https://cdn.test/policy"
        for location, hops, terminal, state, calls in (
                (target, 5, self.response(b""), "OK", 7),
                (target, 5, self.response(b"User-agent: *\nDisallow: /"), "BLOCKED_BY_ROBOTS_NEEDS_CONSENT", 6),
                (target, 6, None, "SKIPPED_ROBOTS_UNVERIFIED", 6),
                ("http://cdn.test/policy", 1, None, "SKIPPED_ROBOTS_UNVERIFIED", 1)):
            replies = [HTTPError(robots[0], 302, "redirect", {"Location": location}, None) for _ in range(hops)]
            replies += [terminal, self.response(b"page")]
            opener = Mock(open=Mock(side_effect=replies))
            with self.subTest(location=location, hops=hops, state=state), patch.object(preflight, "configuration",
                    return_value=self.configuration(pages, robots)), patch.object(preflight.time, "sleep"):
                report = preflight.build_report(opener=opener)
            self.assertEqual(report["results"][1]["status"], state)
            self.assertEqual(opener.open.call_count, calls)
            self.assertLessEqual(len(report["results"][0]["redirects"]), 5)

    def test_page_timeout_and_redirect_remain_single_requests(self):
        for error, status in ((URLError("offline"), "UNREACHABLE_FROM_RUNNER"),
                              (TimeoutError("timeout"), "UNREACHABLE_FROM_RUNNER"),
                              (HTTPError("https://example.org/", 302, "redirect",
                                         {"Location": "https://other.test/"}, None), "HTTP_ERROR")):
            opener = Mock(open=Mock(side_effect=error))
            self.assertEqual(preflight.probe("https://example.org/", opener)["status"], status)
            self.assertEqual(opener.open.call_count, 1)

    def test_body_limit_and_report_validation(self):
        context = self.response(b"x" * (preflight.MAX_BYTES + 1))
        row = preflight.probe("https://example.org/robots.txt", Mock(open=Mock(return_value=context)))
        self.assertEqual(row["status"], "BODY_LIMIT")
        context.__enter__.return_value.read.assert_called_once_with(preflight.MAX_BYTES + 1)
        self.assertEqual(preflight.robots_policy(row), ("UNVERIFIED", None))
        report = preflight.build_report(dry_run=True)
        report["results"][0]["status"] = "INVALID"
        with self.assertRaises(ValueError):
            preflight.validate_report(report)
        report = preflight.build_report(dry_run=True)
        report["results"][-1] = report["results"][0]
        with self.assertRaises(ValueError):
            preflight.validate_report(report)


if __name__ == "__main__":
    unittest.main()
