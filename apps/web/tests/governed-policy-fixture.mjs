// Isolated fictional rights permission. No repository-approved input is changed.
import { spawnSync } from "node:child_process";
import { mkdtemp, readFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const root = fileURLToPath(new URL("../../../", import.meta.url));
let fixtureNumber = 0;
export async function createGovernedPolicyFixture({ rightsReviewed = true, briefReviewed = false, perSourceReview = false } = {}) {
  const repoRoot = await mkdtemp(join(tmpdir(), "govintel-fictional-rights-"));
  try {
    const result = spawnSync(process.env.PYTHON || "python", ["-B", join(root, "tests/governed_policy_fixture.py"),
      "--root", repoRoot, ...(rightsReviewed ? [] : ["--rights-unknown"]), ...(briefReviewed ? ["--brief-reviewed"] : []),
      ...(perSourceReview ? ["--per-source-review"] : [])], { encoding: "utf8", timeout: 15_000 });
    if (result.status !== 0) throw new Error(`fictional compiler fixture failed: ${result.stderr || result.error}`);
    const publicRoot = pathToFileURL(join(repoRoot, "apps/web/public/data/"));
    const policy = JSON.parse(await readFile(new URL("source-policy.json", publicRoot), "utf8"));
    const binding = Object.fromEntries(["policy_version", "policy_hash", "catalog_hash", "active_source_ids"]
      .map(key => [key, policy[key]]));
    binding.governance_hash = policy.governance_binding.governance_hash;
    binding.retention_policy_hash = policy.governance_binding.retention_policy.policy_hash;
    return {
      repoRoot, publicRoot, policy, binding, fixtureRightsReview: "FICTIONAL_OFFLINE_ONLY",
      officialUrl(path, sourceId = policy.active_source_ids[0]) {
        return policy.active_sources.find(row => row.source_id === sourceId).approved_origins[0] + path;
      },
      async publication() {
        return Object.fromEntries(await Promise.all(["source-policy.json", "intelligence-feed.json", "source-status.json", "v2-daily-brief.json"]
          .map(async name => [name, await readFile(new URL(name, publicRoot))])));
      },
      loadWorker(tag = "fixture") {
        const url = pathToFileURL(join(repoRoot, "workers/query-gateway/src/index.js"));
        url.search = `${tag}=${++fixtureNumber}`;
        return import(url.href);
      },
      cleanup: () => rm(repoRoot, { recursive: true, force: true }),
    };
  } catch (error) { await rm(repoRoot, { recursive: true, force: true }); throw error; }
}
