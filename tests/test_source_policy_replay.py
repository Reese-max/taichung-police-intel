import copy
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


def module_at(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def seal(store, qs):
    store["projection_sha256"] = qs.sha256_bytes(qs.canonical_json(
        {key: value for key, value in store.items() if key != "projection_sha256"}
    ))


class SourcePolicyReplayTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        for relative in ("docs/govintel", "apps/web/public/data"):
            shutil.copytree(ROOT / relative, self.root / relative)
        (self.root / "scripts").mkdir()
        for relative in ("collect.py", "scripts/source-policy.py", "scripts/query-store.py"):
            shutil.copy2(ROOT / relative, self.root / relative)
        self.sp = module_at("replay_policy", self.root / "scripts/source-policy.py")
        self.qs = module_at("replay_store", self.root / "scripts/query-store.py")
        self.old_policy = self.sp.load_current_policy()
        self.old_store = self.qs.build_from_paths(self.qs.DEFAULT_FEED, self.qs.DEFAULT_STATUS, self.qs.DEFAULT_BRIEF)
        self.clock = self.qs.instant(self.old_store["generated_from"]["feed_generated_at"])

    def promote_fixture(self):
        catalog = self.sp.load_catalog()
        next(row for row in catalog["sources"] if row["source_id"] == "S-032")["status"] = "PRODUCTION_ACTIVE"
        policy = self.sp.compile_policy(catalog, previous=self.old_policy, promotions=[{
            "source_id": "S-032", "receipt_id": "fixture:explicit-approval", "reason": "isolated test only",
        }])
        self.sp.DEFAULT_CATALOG.write_text(json.dumps(catalog), encoding="utf-8")
        self.sp.APPROVED_POLICY.write_text(json.dumps(policy), encoding="utf-8")
        return policy

    def zero_store(self):
        feed, _ = self.qs.load_json(self.qs.DEFAULT_FEED)
        status, _ = self.qs.load_json(self.qs.DEFAULT_STATUS)
        brief, _ = self.qs.load_json(self.qs.DEFAULT_BRIEF)
        feed["items"] = []
        status["latest_collection_run"]["status"] = "SUCCEEDED"
        brief.update(publication_status="READY", snapshot_complete=True)
        for source in status["sources"]:
            source.update(source_health="PASS", window_completeness="COMPLETE_ZERO", result="NO_NEW_ITEM",
                          freshness_status="RECENT", last_checked_at=feed["generated_at"])
        hashes = {key: self.qs.sha256_bytes(self.qs.canonical_json(value))
                  for key, value in (("feed", feed), ("status", status), ("brief", brief))}
        return self.qs.build_store(feed, status, brief, hashes)

    def test_collector_rejects_unapproved_catalog_change(self):
        catalog = self.sp.load_catalog()
        next(row for row in catalog["sources"] if row["source_id"] == "S-032")["status"] = "PRODUCTION_ACTIVE"
        self.sp.DEFAULT_CATALOG.write_text(json.dumps(catalog), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "policy/catalog binding mismatch"):
            module_at("unapproved_collector", self.root / "collect.py")

    def test_collector_rejects_missing_approval_anchor(self):
        self.sp.APPROVED_POLICY.unlink()
        with self.assertRaises(FileNotFoundError):
            module_at("missing_approval_collector", self.root / "collect.py")

    def test_collector_accepts_explicit_fixture_approval(self):
        policy = self.promote_fixture()
        collector = module_at("approved_fixture_collector", self.root / "collect.py")
        self.assertEqual(sorted(collector.P0_SOURCES), policy["active_source_ids"])

    def test_generated_policy_binding_cannot_be_resealed(self):
        changed = copy.deepcopy(self.old_store)
        changed["generated_from"]["policy_hash"] = "0" * 64
        seal(changed, self.qs)
        with self.assertRaisesRegex(ValueError, "generated policy"):
            self.qs.query_store(changed, now=self.clock)

    def test_generation_cannot_be_resealed_independently_of_artifacts_and_policy(self):
        for field in ("generation_id", "feed_sha256"):
            with self.subTest(field=field):
                changed = copy.deepcopy(self.old_store)
                if field == "generation_id":
                    changed[field] = "0" * 64
                else:
                    changed["generated_from"][field] = "0" * 64
                seal(changed, self.qs)
                with self.assertRaisesRegex(ValueError, "generation binding"):
                    self.qs.query_store(changed, now=self.clock)

    def test_unsupported_traffic_zero_is_not_answerable(self):
        store = self.zero_store()
        supported = self.qs.query_store(store, now=self.clock)
        self.assertTrue(supported["answerable_no_match"])
        unsupported = self.qs.query_store(store, now=self.clock, capability_id="traffic_events")
        self.assertEqual(unsupported["query_coverage"]["status"], "CAPABILITY_NOT_AVAILABLE")
        self.assertFalse(unsupported["query_coverage"]["can_state_bounded_no_match"])
        self.assertFalse(unsupported["answerable_no_match"])

    def test_old_publication_replays_original_result_after_approved_fixture_transition(self):
        before = self.qs.query_store(self.old_store, text="議事", limit=2, now=self.clock)
        original_store = copy.deepcopy(self.old_store)
        original_archive = (self.sp.APPROVED_HISTORY / f"{self.old_policy['policy_hash']}.json").read_bytes()
        self.promote_fixture()
        with self.assertRaisesRegex(ValueError, "policy mismatch"):
            self.qs.query_store(self.old_store, now=self.clock)
        replay = self.qs.replay_query_store(self.old_store, policy_hash=self.old_policy["policy_hash"],
                                          as_of=self.clock.isoformat(), text="議事", limit=2)
        self.assertEqual(replay["mode"], "APPROVED_HISTORICAL_REPLAY")
        self.assertTrue(replay["read_only"])
        self.assertFalse(replay["may_answer_current"])
        self.assertEqual(replay["result"], before)
        self.assertEqual(self.old_store, original_store)
        self.assertEqual((self.sp.APPROVED_HISTORY / f"{self.old_policy['policy_hash']}.json").read_bytes(), original_archive)

    def test_unknown_or_non_hash_history_selector_fails_closed(self):
        for policy_hash in ("0" * 64, "../source-policy.approved", True, None, "A" * 64):
            with self.subTest(policy_hash=policy_hash), self.assertRaises((ValueError, FileNotFoundError)):
                self.qs.replay_query_store(self.old_store, policy_hash=policy_hash, as_of=self.clock.isoformat())

    def test_tampered_archive_and_cross_policy_replay_fail_closed(self):
        path = self.sp.APPROVED_HISTORY / f"{self.old_policy['policy_hash']}.json"
        policy = json.loads(path.read_text(encoding="utf-8"))
        policy["active_source_ids"] = []
        path.write_text(json.dumps(policy), encoding="utf-8")
        with self.assertRaises(ValueError):
            self.qs.replay_query_store(self.old_store, policy_hash=self.old_policy["policy_hash"], as_of=self.clock.isoformat())

    def test_resealed_archive_cannot_change_its_approved_filename(self):
        path = self.sp.APPROVED_HISTORY / f"{self.old_policy['policy_hash']}.json"
        changed = copy.deepcopy(self.old_policy)
        changed["policy_version"] += 1
        changed["policy_hash"] = self.sp.digest({key: value for key, value in changed.items() if key != "policy_hash"})
        path.write_text(json.dumps(changed), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "archive name"):
            self.qs.replay_query_store(self.old_store, policy_hash=self.old_policy["policy_hash"], as_of=self.clock.isoformat())

    def test_replay_requires_fixed_timezone_clock_and_live_api_rejects_history_selector(self):
        for as_of in (None, "2026-08-14T12:00:00", "invalid"):
            with self.subTest(as_of=as_of), self.assertRaises(ValueError):
                self.qs.replay_query_store(self.old_store, policy_hash=self.old_policy["policy_hash"], as_of=as_of)
        with self.assertRaises(TypeError):
            self.qs.query_store(self.old_store, policy_hash=self.old_policy["policy_hash"])
        with self.assertRaisesRegex(ValueError, "as_of clock"):
            self.qs.replay_query_store(self.old_store, policy_hash=self.old_policy["policy_hash"],
                                      as_of=self.clock.isoformat(), now=self.clock)

    def test_cli_replays_historical_projection_but_live_query_rejects_it(self):
        path = self.root / "old-store.json"
        path.write_text(json.dumps(self.old_store), encoding="utf-8")
        expected = self.qs.query_store(self.old_store, now=self.clock)
        self.promote_fixture()
        command = [sys.executable, str(self.root / "scripts/query-store.py")]
        denied = subprocess.run(command + ["query", "--store", str(path)], text=True, capture_output=True)
        self.assertNotEqual(denied.returncode, 0)
        self.assertIn("policy mismatch", denied.stderr)
        allowed = subprocess.run(command + ["replay", "--store", str(path), "--policy-hash",
                                             self.old_policy["policy_hash"], "--as-of", self.clock.isoformat()],
                                 text=True, capture_output=True)
        self.assertEqual(allowed.returncode, 0, allowed.stderr)
        replay = json.loads(allowed.stdout)
        self.assertEqual(replay["result"], expected)
        self.assertFalse(replay["may_answer_current"])


if __name__ == "__main__":
    unittest.main()
