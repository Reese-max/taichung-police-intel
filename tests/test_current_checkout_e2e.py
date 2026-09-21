import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "verify-current-checkout.py"
spec = importlib.util.spec_from_file_location("verify_current_checkout", SCRIPT)
vc = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(vc)

DATA = ROOT / "apps" / "web" / "public" / "data"


def http_get(url):
    request = urllib.request.Request(url, headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as error:
        return error.code, error.read()


def http_json(url):
    status, body = http_get(url)
    return status, json.loads(body.decode("utf-8"))


class IdentityTests(unittest.TestCase):
    def test_identity_records_code_sha_and_lock_hash(self):
        identity = vc.collect_identity(ROOT)
        self.assertRegex(identity["code_sha"] or "", r"^[0-9a-f]{40}$")
        self.assertIsInstance(identity["worktree_dirty"], bool)
        self.assertRegex(identity["dependency_lock_hash"], r"^[0-9a-f]{64}$")
        self.assertIn("requirements.txt", identity["dependency_lock_files"])
        self.assertIn("apps/web/package-lock.json", identity["dependency_lock_files"])

    def test_lock_hash_changes_with_lockfile_content(self):
        with tempfile.TemporaryDirectory() as tmp:
            copied = Path(tmp) / "requirements.txt"
            copied.write_text((ROOT / "requirements.txt").read_text(encoding="utf-8"), encoding="utf-8")
            first = vc.hash_lockfiles([copied])
            copied.write_text(copied.read_text(encoding="utf-8") + "extra==1.0.0\n", encoding="utf-8")
            self.assertNotEqual(first, vc.hash_lockfiles([copied]))


class ModuleBindingTests(unittest.TestCase):
    def test_modules_load_from_this_checkout_only(self):
        modules = vc.load_checkout_modules(ROOT)
        for name in ("query_store", "system_health", "source_policy"):
            module = modules[name]
            self.assertTrue(Path(module.__file__).resolve().is_relative_to(ROOT), name)
        self.assertTrue(hasattr(modules["query_store"], "query_store"))
        self.assertTrue(hasattr(modules["system_health"], "build_health"))
        self.assertTrue(hasattr(modules["source_policy"], "compile_policy"))


class CandidateContextTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.modules = vc.load_checkout_modules(ROOT)
        cls.ctx = vc.build_candidate_context(ROOT, cls.modules)

    def test_policy_p0_set_matches_query_store(self):
        policy = self.ctx["policy"]
        qs = self.modules["query_store"]
        self.assertEqual(policy["active_source_ids"], qs.load_current_policy()["active_source_ids"])
        self.assertEqual(policy["policy_version"], 1)
        self.assertRegex(policy["policy_hash"], r"^[0-9a-f]{64}$")

    def test_store_generation_binds_all_canonical_hashes(self):
        store = self.ctx["store"]
        generated = store["generated_from"]
        for key in ("feed", "status", "brief"):
            self.assertRegex(generated[f"{key}_sha256"], r"^[0-9a-f]{64}$")
        self.assertEqual(store["generation_id"], generated["generation_id"] if "generation_id" in generated else store["generation_id"])
        self.assertGreater(store["counts"]["publication_items"], 0)

    def test_unavailable_capabilities_are_explicit(self):
        unavailable = {row["capability_id"]: row for row in self.ctx["unavailable_capabilities"]}
        self.assertIn("read_only_mcp", self.ctx["enabled_capabilities"])
        for capability in ("event_fusion", "entity_registry", "answer_evidence_gate",
                           "gold_evaluation", "chat_mcp", "live_collection"):
            self.assertEqual(unavailable[capability]["status"], "CAPABILITY_NOT_AVAILABLE", capability)

    def test_stdio_mcp_lifecycle_is_hash_bound_and_read_only(self):
        check = vc.run_stdio_mcp_check(self.ctx)
        self.assertEqual(check["status"], "PASS", check)
        self.assertEqual(check["transport"], "stdio")


class ServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.modules = vc.load_checkout_modules(ROOT)
        cls.ctx = vc.build_candidate_context(ROOT, cls.modules)
        cls.tmp = tempfile.TemporaryDirectory()
        cls.serve_dir = vc.prepare_serve_dir(cls.ctx, Path(cls.tmp.name) / "site")
        cls.server, cls.base = vc.start_server(cls.ctx, cls.serve_dir, port=0)

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.tmp.cleanup()

    def test_static_page_and_feed_served(self):
        status, body = http_get(self.base + "/")
        self.assertEqual(status, 200)
        self.assertIn(b"<html", body)
        status, body = http_get(self.base + "/data/intelligence-feed.json")
        self.assertEqual(status, 200)
        import hashlib
        self.assertEqual(hashlib.sha256(body).hexdigest(), self.ctx["hashes"]["feed"])

    def test_version_endpoint_reports_generation_and_policy(self):
        status, doc = http_json(self.base + "/api/version")
        self.assertEqual(status, 200)
        self.assertEqual(doc["generation_id"], self.ctx["store"]["generation_id"])
        self.assertEqual(doc["policy_version"], self.ctx["policy"]["policy_version"])
        self.assertEqual(doc["policy_hash"], self.ctx["policy"]["policy_hash"])
        self.assertEqual(doc["test_mode"], "CURRENT_CHECKOUT_LOOPBACK_HTTP")

    def test_query_endpoint_returns_same_generation_bounded_results(self):
        status, doc = http_json(self.base + "/api/query?q=S-004&limit=3")
        self.assertEqual(status, 200)
        self.assertEqual(doc["query_generation_id"], self.ctx["store"]["generation_id"])
        self.assertLessEqual(doc["result_count"], 3)
        self.assertTrue(all(row["source_id"] == "S-004" for row in doc["results"]))
        self.assertIn("source_gaps", doc)
        self.assertIn("data_status", doc)

    def test_query_refuses_foreign_generation(self):
        status, doc = http_json(self.base + "/api/query?expected_generation=" + "0" * 64)
        self.assertEqual(status, 409)
        self.assertEqual(doc["code"], "GENERATION_MISMATCH")

    def test_query_unknown_source_is_distinguishable(self):
        status, doc = http_json(self.base + "/api/query?source_id=S-999")
        self.assertEqual(status, 200)
        self.assertEqual(doc["data_status"], "SOURCE_NOT_AVAILABLE")
        self.assertNotEqual(doc["data_status"], "NO_EVENTS")

    def test_query_bounds_enforced(self):
        for bad in ("0", "500"):
            status, _ = http_json(self.base + "/api/query?limit=" + bad)
            self.assertEqual(status, 400, bad)

    def test_stale_snapshot_is_not_reported_as_no_events(self):
        qs = self.modules["query_store"]
        from datetime import datetime, timezone
        expected_status, expected_gaps = qs.assess_scope(self.ctx["store"], None, datetime.now(timezone.utc))
        status, doc = http_json(self.base + "/api/query?q=")
        self.assertEqual(status, 200)
        self.assertEqual(doc["data_status"], expected_status)
        self.assertEqual({gap["reason"] for gap in doc["source_gaps"]}, {gap["reason"] for gap in expected_gaps})
        self.assertNotIn(doc["data_status"], ("NO_EVENTS", "EMPTY"))
        self.assertGreater(doc["total_matches"], 0)
        self.assertFalse(doc["answerable_no_match"] and expected_gaps)

    def test_capability_not_available_is_distinguishable(self):
        status, doc = http_json(self.base + "/api/capability?id=traffic_events")
        self.assertEqual(status, 200)
        self.assertEqual(doc["status"], "CAPABILITY_NOT_AVAILABLE")
        self.assertFalse(doc["can_state_bounded_no_match"])

    def test_mixed_served_store_is_detected_not_accepted(self):
        qs = self.modules["query_store"]
        feed = dict(self.ctx["artifacts"]["feed"])
        feed["items"] = feed["items"][:-1]
        mutated = qs.build_store(feed, self.ctx["artifacts"]["status"], self.ctx["artifacts"]["brief"],
                                 {"feed": "f" * 64, "status": self.ctx["hashes"]["status"],
                                  "brief": self.ctx["hashes"]["brief"]})
        self.assertNotEqual(mutated["generation_id"], self.ctx["store"]["generation_id"])
        served = self.serve_dir / "data" / "query-store.json"
        original = served.read_bytes()
        try:
            qs.atomic_write_json(served, mutated)
            status, doc = http_json(self.base + "/api/version")
            self.assertEqual(doc["generation_id"], mutated["generation_id"])
            check = vc.check_served_store_consistency(self.ctx, self.base)
            self.assertEqual(check["status"], "FAIL")
        finally:
            served.write_bytes(original)
        check = vc.check_served_store_consistency(self.ctx, self.base)
        self.assertEqual(check["status"], "PASS")

    def test_atomic_cutover_refuses_old_generation_and_keeps_history(self):
        qs = self.modules["query_store"]
        old_generation = self.ctx["store"]["generation_id"]
        feed = dict(self.ctx["artifacts"]["feed"])
        feed["items"] = feed["items"][:-1]
        mutated = qs.build_store(feed, self.ctx["artifacts"]["status"], self.ctx["artifacts"]["brief"],
                                 {"feed": "f" * 64, "status": self.ctx["hashes"]["status"],
                                  "brief": self.ctx["hashes"]["brief"]})
        archive = self.serve_dir / "data" / "query-store.archive.json"
        store_file = self.serve_dir / "data" / "query-store.json"
        original = store_file.read_bytes()
        try:
            shutil.copy2(store_file, archive)
            qs.atomic_write_json(store_file, mutated)
            status, doc = http_json(self.base + "/api/query?expected_generation=" + old_generation)
            self.assertEqual(status, 409)
            self.assertEqual(doc["code"], "GENERATION_MISMATCH")
            status, body = http_get(self.base + "/data/query-store.archive.json")
            self.assertEqual(status, 200)
            self.assertEqual(json.loads(body)["generation_id"], old_generation)
        finally:
            store_file.write_bytes(original)
            archive.unlink(missing_ok=True)

    def test_query_down_keeps_static_readable(self):
        try:
            self.server.query_down = True
            status, doc = http_json(self.base + "/api/query?q=x")
            self.assertEqual(status, 503)
            self.assertEqual(doc["status"], "QUERY_UNAVAILABLE")
            status, _ = http_get(self.base + "/")
            self.assertEqual(status, 200)
            status, _ = http_get(self.base + "/data/intelligence-feed.json")
            self.assertEqual(status, 200)
            status, health = http_json(self.base + "/api/health")
            self.assertEqual(status, 200)
            self.assertEqual(health["lanes"]["query"], "BLOCKED")
            self.assertNotEqual(health["lanes"]["publication"], "BLOCKED")
        finally:
            self.server.query_down = False

    def test_health_endpoint_keeps_formal_deployment_unknown(self):
        status, health = http_json(self.base + "/api/health")
        self.assertEqual(status, 200)
        stages = {(row["lane"], row["stage"]): row for row in health["stages"]}
        self.assertEqual(stages[("publication", "deployment")]["outcome"], "UNKNOWN")
        self.assertEqual(stages[("publication", "public_http_verification")]["outcome"], "UNKNOWN")
        self.assertNotEqual(health["lanes"]["publication"], "HEALTHY")


class ReceiptGuardTests(unittest.TestCase):
    def test_loopback_receipt_does_not_promote_formal_publication_stages(self):
        modules = vc.load_checkout_modules(ROOT)
        ctx = vc.build_candidate_context(ROOT, modules)
        result = vc.build_health_receipt(ctx, [{"id": "loopback", "status": "PASS"}])
        stages = {(row["lane"], row["stage"]): row for row in result["stages"]}
        self.assertEqual(stages[("publication", "deployment")]["outcome"], "UNKNOWN")
        self.assertEqual(stages[("publication", "public_http_verification")]["outcome"], "UNKNOWN")
        self.assertNotEqual(result["lanes"]["publication"], "HEALTHY")

    def test_receipt_fails_with_any_failed_check(self):
        receipt = {"checks": [{"id": "a", "status": "PASS"}, {"id": "b", "status": "FAIL"}]}
        self.assertEqual(vc.finalize_status(receipt), "FAIL")

    def test_receipt_fails_with_zero_checks(self):
        self.assertEqual(vc.finalize_status({"checks": []}), "FAIL")
        self.assertEqual(vc.finalize_status({"checks": [{"id": "a", "status": "NOT_RUN"}]}), "FAIL")

    def test_receipt_passes_only_when_all_checks_pass(self):
        receipt = {"checks": [{"id": "a", "status": "PASS"}, {"id": "b", "status": "PASS"}]}
        self.assertEqual(vc.finalize_status(receipt), "PASS")


class SabotageTests(unittest.TestCase):
    def test_sabotaged_checkout_copy_fails_verification(self):
        with tempfile.TemporaryDirectory() as tmp:
            check = vc.run_sabotage_check(ROOT, Path(tmp))
        self.assertEqual(check["status"], "PASS")
        self.assertEqual(check["observed"], "FAIL")
        self.assertIn("query-store.py", check["sabotaged_module"])


class CliTests(unittest.TestCase):
    def test_core_mode_writes_machine_readable_receipt(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = subprocess.run(
                [sys.executable, "-X", "utf8", str(SCRIPT), "--mode", "core", "--output", tmp],
                cwd=ROOT, capture_output=True, text=True, encoding="utf-8", timeout=300)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            receipt = json.loads((Path(tmp) / "receipt.json").read_text(encoding="utf-8"))
            self.assertEqual(receipt["status"], "PASS")
            self.assertEqual(receipt["test_mode"], "CURRENT_CHECKOUT_CORE")
            self.assertRegex(receipt["code_sha"], r"^[0-9a-f]{40}$")
            self.assertRegex(receipt["dependency_lock_hash"], r"^[0-9a-f]{64}$")
            self.assertRegex(receipt["query_store"]["generation_id"], r"^[0-9a-f]{64}$")
            self.assertRegex(receipt["source_policy"]["policy_hash"], r"^[0-9a-f]{64}$")
            self.assertGreater(len(receipt["checks"]), 0)
            self.assertFalse(receipt["production_verified"])
            self.assertEqual(receipt["historical_pinned_replay"]["status"], "SEPARATE_LANE")


if __name__ == "__main__":
    unittest.main()
