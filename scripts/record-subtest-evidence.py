"""Record existing alias subtests individually, retaining all assertions and skips."""
import argparse, json, sys, unittest
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT/"tests"), str(ROOT)]

class EvidenceResult(unittest.TextTestResult):
    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.records = []
    def addSubTest(self, test, subtest, error):
        super().addSubTest(test, subtest, error)
        if error is None:
            row = {"parent": test.id(), "subtest": subtest.id(), "status": "PASS"}
            self.records.append(row)
            self.stream.writeln("SUBTEST_EVIDENCE " + json.dumps(row))
        else:
            self.records.append({"parent": test.id(), "subtest": subtest.id(), "status": "FAIL"})
    def addSkip(self, test, reason):
        super().addSkip(test, reason)
        self.records.append({"subtest": test.id(), "status": "SKIP", "reason": reason})

def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--output", required=True, type=Path)
    args = p.parse_args()
    names = ["test_reference_resource_validator_review.IndependentValidatorReviewTests." + n for n in
             ("test_cli_rejects_output_aliases_and_preserves_plan_and_original_bytes",
              "test_cli_receipt_replaces_output_links_without_mutating_their_targets")]
    suite = unittest.defaultTestLoader.loadTestsFromNames(names)
    r = unittest.TextTestRunner(verbosity=2, resultclass=EvidenceResult).run(suite)
    ok = r.wasSuccessful() and not r.skipped and len(r.records) == 10 and all(x["status"] == "PASS" for x in r.records)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps({"status": "PASS" if ok else "FAIL", "tests": r.testsRun,
                                      "subtests": r.records, "production_verified": False}, indent=2)+"\n", encoding="utf-8")
    return 0 if ok else 1

if __name__ == "__main__":
    raise SystemExit(main())

