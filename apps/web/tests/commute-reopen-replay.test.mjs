import assert from "node:assert/strict";
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

test("commute reopen replay unit suite passes", () => {
  const result = runPython(["-X", "utf8", "-m", "unittest", "discover", "-s", "tests", "-p", "test_commute_reopen_replay.py", "-v"]);
  assert.equal(result.status, 0, `${result.stdout}\n${result.stderr}`);
  assert.match(result.stderr, /^OK$/m);
  assert.doesNotMatch(result.stderr, /FAILED|ERROR: /);
});

test("commute reopen replay single command reproduces the gold expected sets", () => {
  const result = runPython(["-X", "utf8", "scripts/replay-commute-reopen.py", "--self-check"]);
  assert.equal(result.status, 0, `${result.stdout}\n${result.stderr}`);
  assert.match(result.stdout, /COMMUTE_REOPEN_SELF_CHECK_OK/);
  // The full arm must be exact; every other arm is a live, worse measurement, and
  // each false-alert counter must be reachable from real replay output.
  assert.match(result.stdout, /tp=6 fp=0 fn=0/);
  assert.match(result.stdout, /arm=C_RULES_ONLY .*fp=2/);
  assert.match(result.stdout, /arm=B_v6 .*fp=17/);
  assert.match(result.stdout, /arm=PROBE_ONE_ALERT_PER_DOCUMENT .*fn=3/);
  assert.match(result.stdout, /arm=PROBE_PROMPT_AFTER_CANCEL .*fp=1/);
  assert.match(result.stdout, /arm=PROBE_CANCEL_MOVED_LATER .*fp=1/);
  assert.match(result.stdout, /model_requests=0/);
});

test("v6 extension leaves the frozen v1 gold harness intact", () => {
  const result = runPython(["-X", "utf8", "scripts/evaluate-govintel.py", "--self-check"]);
  assert.equal(result.status, 0, `${result.stdout}\n${result.stderr}`);
  assert.match(result.stdout, /GOLD_EVAL_SELF_CHECK_OK dataset=govintel-gold-v1 cases=15/);
});

test("v6 methods stay distinct from the frozen v1 dataset and unexecuted policy proxies", () => {
  const result = spawnSync(python.command, [...python.prefix, "-X", "utf8", "-c", [
    "import json,sys",
    "template=json.load(open('docs/govintel/competition-2026/evaluation-manifest.template.json',encoding='utf-8'))",
    "v6=json.load(open('eval/gold/v6/commute-reopen-v1/manifest.json',encoding='utf-8'))",
    "assert template['schema_version']==2 and template['proposal_version']=='v6_2026-10-01'",
    "assert template['arms']['A']['method']=='direct_official_manual_query_and_update_recheck'",
    "assert template['arms']['B']['method']=='same_scope_search_and_general_summary'",
    "assert template['arms']['C']['method']=='full_GovIntel_public_query_and_browser_tracking'",
    "assert template['arms']['C_semantic_off']['method']=='same_C_interface_data_tracking_read_rules_without_semantic_processing'",
    "assert all(arm['method_execution_status']=='NOT_RUN' and arm['results'] is None for arm in template['arms'].values())",
    "assert template['engineering_policy_replay']['evaluation_scope']=='SYNTHETIC_DETERMINISTIC_POLICY_REPLAY'",
    "assert 'B_v6' in v6['arms'] and v6['arms']['B_v6']['method_id']!='same_new_workflow_without_semantic_AI'",
    "assert v6['arms']['C_RULES_ONLY']['method_id']!=v6['arms']['B_v6']['method_id']",
    "assert all(arm['method_execution_status']=='NOT_RUN' for arm in v6['arms'].values())",
    "assert v6['evaluation_scope']=='SYNTHETIC_DETERMINISTIC_POLICY_REPLAY'",
    "print('METHOD_IDS_OK')",
  ].join("\n")], { cwd: repo, encoding: "utf8" });
  assert.equal(result.status, 0, `${result.stdout}\n${result.stderr}`);
  assert.match(result.stdout, /METHOD_IDS_OK/);
});
