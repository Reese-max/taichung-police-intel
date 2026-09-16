import importlib.util
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "answer-evidence-gate.py"
spec = importlib.util.spec_from_file_location("answer_evidence_gate", SCRIPT)
gate = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(gate)


def evidence(evidence_id="e1", *, facts=None, trust="OFFICIAL", freshness="RECENT", version="doc:v1"):
    return {
        "evidence_id": evidence_id,
        "trust_tier": trust,
        "freshness": freshness,
        "source_document_version": version,
        "locator": f"https://example.gov/{evidence_id}",
        "facts": facts or {},
    }


def claim(claim_id="c1", *, facts=None, refs=None, current=True):
    return {
        "claim_id": claim_id,
        "text": claim_id,
        "current_claim": current,
        "required_facts": facts or [{"field": "start_time", "value": "16:00"}],
        "evidence_ids": refs or ["e1"],
    }


def payload(claims, evidences):
    return {"publication_hash": "a" * 64, "claims": claims, "evidence": evidences}


class AnswerEvidenceGateTests(unittest.TestCase):
    def test_exact_current_official_fact_is_supported(self):
        result = gate.gate_answer(payload([claim()], [evidence(facts={"start_time": "16:00"})]))
        self.assertEqual(result["answer_status"], "VERIFIED")
        self.assertEqual(result["claim_receipts"][0]["support_status"], "SUPPORTED")
        self.assertEqual(result["safe_claim_ids"], ["c1"])

    def test_supported_time_plus_unsupported_cause_is_partial(self):
        composite = claim(
            facts=[
                {"field": "start_time", "value": "16:00"},
                {"field": "cause", "value": "豪雨"},
            ]
        )
        result = gate.gate_answer(payload([composite], [evidence(facts={"start_time": "16:00"})]))
        receipt = result["claim_receipts"][0]
        self.assertEqual(receipt["support_status"], "PARTIAL")
        self.assertEqual(receipt["unsupported_fields"], ["cause"])
        self.assertEqual(result["answer_status"], "NEEDS_QUALIFICATION")

    def test_conflicting_official_sources_remain_conflict(self):
        refs = ["e1", "e2"]
        result = gate.gate_answer(
            payload(
                [claim(refs=refs)],
                [
                    evidence("e1", facts={"start_time": "16:00"}, version="police:v2"),
                    evidence("e2", facts={"start_time": "17:00"}, version="traffic:v1"),
                ],
            )
        )
        receipt = result["claim_receipts"][0]
        self.assertEqual(receipt["support_status"], "CONFLICT")
        self.assertEqual(receipt["conflict_fields"], ["start_time"])

    def test_stale_evidence_cannot_support_current_wording(self):
        result = gate.gate_answer(payload([claim(current=True)], [evidence(facts={"start_time": "16:00"}, freshness="STALE")]))
        self.assertEqual(result["claim_receipts"][0]["support_status"], "STALE")
        self.assertEqual(result["answer_status"], "NEEDS_QUALIFICATION")

    def test_stale_evidence_can_support_explicit_historical_claim(self):
        result = gate.gate_answer(payload([claim(current=False)], [evidence(facts={"start_time": "16:00"}, freshness="STALE")]))
        self.assertEqual(result["claim_receipts"][0]["support_status"], "SUPPORTED")

    def test_media_discovery_does_not_support_verified_claim(self):
        result = gate.gate_answer(
            payload([claim()], [evidence(facts={"start_time": "16:00"}, trust="DISCOVERY_UNVERIFIED")])
        )
        self.assertEqual(result["claim_receipts"][0]["support_status"], "UNSUPPORTED")
        self.assertEqual(result["claim_receipts"][0]["reason"], "NO_OFFICIAL_EVIDENCE")
        self.assertEqual(result["answer_status"], "BLOCKED")

    def test_missing_evidence_reference_fails_closed(self):
        result = gate.gate_answer(payload([claim(refs=["missing"])], [evidence(facts={"start_time": "16:00"})]))
        receipt = result["claim_receipts"][0]
        self.assertEqual(receipt["support_status"], "UNSUPPORTED")
        self.assertEqual(receipt["missing_evidence_ids"], ["missing"])

    def test_statistic_scope_is_compared_as_structured_value(self):
        fact = {"value": 123, "period": "2026-08", "geography": "臺中市", "unit": "cases"}
        c = claim(facts=[{"field": "statistic", "value": fact}])
        result = gate.gate_answer(payload([c], [evidence(facts={"statistic": fact})]))
        self.assertEqual(result["claim_receipts"][0]["support_status"], "SUPPORTED")
        wrong = dict(fact, period="2026-09")
        result = gate.gate_answer(payload([c], [evidence(facts={"statistic": wrong})]))
        self.assertEqual(result["claim_receipts"][0]["support_status"], "UNSUPPORTED")

    def test_non_https_locator_is_rejected(self):
        bad = evidence(facts={"start_time": "16:00"})
        bad["locator"] = "http://example.gov/e1"
        with self.assertRaisesRegex(ValueError, "HTTPS"):
            gate.gate_answer(payload([claim()], [bad]))

    def test_receipt_hash_is_deterministic(self):
        p = payload([claim()], [evidence(facts={"start_time": "16:00"})])
        first = gate.gate_answer(p)
        second = gate.gate_answer(p)
        self.assertEqual(first["receipt_hash"], second["receipt_hash"])
        self.assertEqual(first["validator_version"], gate.VALIDATOR_VERSION)


if __name__ == "__main__":
    unittest.main()
