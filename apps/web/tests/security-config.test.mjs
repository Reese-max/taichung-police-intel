import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";


const rulesetUrl = new URL(
  "../../../.github/rulesets/main-production.json",
  import.meta.url,
);

async function loadRuleset() {
  return JSON.parse(await readFile(rulesetUrl, "utf8"));
}

test("main production ruleset is active and targets the default branch", async () => {
  const ruleset = await loadRuleset();
  assert.equal(ruleset.name, "Protect main production");
  assert.equal(ruleset.target, "branch");
  assert.equal(ruleset.source_type, "Repository");
  assert.equal(ruleset.enforcement, "active");
  assert.deepEqual(ruleset.conditions.ref_name.include, ["~DEFAULT_BRANCH"]);
  assert.deepEqual(ruleset.conditions.ref_name.exclude, []);
});

test("main production ruleset blocks deletion and force pushes", async () => {
  const ruleset = await loadRuleset();
  const ruleTypes = new Set(ruleset.rules.map((rule) => rule.type));
  assert.ok(ruleTypes.has("deletion"));
  assert.ok(ruleTypes.has("non_fast_forward"));
});

test("main production ruleset requires a pull request and resolved conversations", async () => {
  const ruleset = await loadRuleset();
  const pullRequest = ruleset.rules.find((rule) => rule.type === "pull_request");
  assert.ok(pullRequest);
  assert.equal(pullRequest.parameters.required_review_thread_resolution, true);
  assert.equal(pullRequest.parameters.dismiss_stale_reviews_on_push, true);
  // This is a single-maintainer repository; a required self-approval would deadlock all PRs.
  assert.equal(pullRequest.parameters.required_approving_review_count, 0);
});

test("main production ruleset requires the GitHub Actions verify job", async () => {
  const ruleset = await loadRuleset();
  const requiredChecks = ruleset.rules.find(
    (rule) => rule.type === "required_status_checks",
  );
  assert.ok(requiredChecks);
  assert.equal(requiredChecks.parameters.strict_required_status_checks_policy, true);
  assert.deepEqual(requiredChecks.parameters.required_status_checks, [
    {
      context: "verify",
      integration_id: 15368,
    },
  ]);
});

test("only GitHub Actions can bypass for scheduled publication writes", async () => {
  const ruleset = await loadRuleset();
  assert.deepEqual(ruleset.bypass_actors, [
    {
      actor_id: 15368,
      actor_type: "Integration",
      bypass_mode: "always",
    },
  ]);
});
