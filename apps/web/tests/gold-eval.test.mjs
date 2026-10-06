import assert from "node:assert/strict";
import { mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import os from "node:os";
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import path from "node:path";
import test from "node:test";

const here = path.dirname(fileURLToPath(import.meta.url));
const repo = path.resolve(here, "../../..");

function findPython() {
  const candidates = process.platform === "win32"
    ? [{ command: "python", prefix: [] }, { command: "py", prefix: ["-3"] }]
    : [{ command: "python3", prefix: [] }, { command: "python", prefix: [] }];
  for (const candidate of candidates) {
    const result = spawnSync(candidate.command, [...candidate.prefix, "--version"], { stdio: "ignore" });
    if (result.status === 0) return candidate;
  }
  throw new Error("No supported Python interpreter found");
}

const python = findPython();
function runPython(args) {
  return spawnSync(python.command, [...python.prefix, ...args], { cwd: repo, encoding: "utf8" });
}

test("gold evaluation unit suite passes", () => {
  const result = runPython([
    "-X", "utf8", "-m", "unittest", "discover", "-s", "tests", "-p", "test_gold_evaluator.py", "-v",
  ]);
  assert.equal(result.status, 0, `${result.stdout}\n${result.stderr}`);
  assert.match(result.stderr, /Ran 47 tests/);
  assert.match(result.stderr, /OK/);
});

test("gold evaluation CLI self-check detects deliberate false merge and wrong source-gap semantics", () => {
  const result = runPython(["-X", "utf8", "scripts/evaluate-govintel.py", "--self-check"]);
  assert.equal(result.status, 0, `${result.stdout}\n${result.stderr}`);
  assert.match(result.stdout, /GOLD_EVAL_SELF_CHECK_OK/);
});

test("gold evaluation CLI reports baseline deltas and deterministic regression status", async () => {
  const temp = await mkdtemp(path.join(os.tmpdir(), "govintel-gold-compare-"));
  try {
    const caseRows = (await readFile(path.join(repo, "eval/gold/v2/cases.jsonl"), "utf8"))
      .trim()
      .split(/\r?\n/)
      .map((line) => JSON.parse(line));
    const baselinePredictions = caseRows.map(({ case_id, expected }) => ({ case_id, prediction: expected }));
    const currentPredictions = structuredClone(baselinePredictions);
    currentPredictions.find((row) => row.case_id === "query-source-001").prediction.ids.push("EXTRA-ID");
    const baselinePredictionsPath = path.join(temp, "baseline-predictions.jsonl");
    const currentPredictionsPath = path.join(temp, "current-predictions.jsonl");
    const baselineReportPath = path.join(temp, "baseline-report.json");
    const toJsonl = (rows) => rows.map((row) => JSON.stringify(row)).join("\n") + "\n";
    await writeFile(baselinePredictionsPath, toJsonl(baselinePredictions), "utf8");
    await writeFile(currentPredictionsPath, toJsonl(currentPredictions), "utf8");

    const baseline = runPython([
      "-X", "utf8", "scripts/evaluate-govintel.py", "--manifest", "eval/gold/v2/manifest.json", "--predictions", baselinePredictionsPath,
      "--output", baselineReportPath,
    ]);
    assert.equal(baseline.status, 0, `${baseline.stdout}\n${baseline.stderr}`);
    const current = runPython([
      "-X", "utf8", "scripts/evaluate-govintel.py", "--manifest", "eval/gold/v2/manifest.json", "--predictions", currentPredictionsPath,
      "--baseline-report", baselineReportPath,
    ]);
    assert.equal(current.status, 0, `${current.stdout}\n${current.stderr}`);
    const report = JSON.parse(current.stdout);
    assert.equal(report.baseline_comparison.regression_status, "REGRESSION");
    assert.equal(report.promotion_gate.status, "NOT_ELIGIBLE");
    assert.ok(report.promotion_gate.blockers.includes("REGRESSION"));
    assert.ok(report.baseline_comparison.score_deltas["query_ids.precision"].delta < 0);
    assert.equal(report.baseline_comparison.count_deltas["query_ids.predicted_id_denominator"].delta, 1);
  } finally {
    await rm(temp, { recursive: true, force: true });
  }
});
