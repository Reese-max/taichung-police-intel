import { readFile } from "node:fs/promises";

const publicRoot = new URL("../public/data/", import.meta.url);
// Keep this immutable history pin in sync with tests/governed_policy_fixture.py.
const legacyPolicy = new URL(
  "../../../docs/govintel/source-policy-history/3866b8d3a062291240bb2c8540d68055ed0cdfaf3ac5a72ffb570875327c8501.json",
  import.meta.url,
);
const legacyPublicPolicyFields = [
  "schema_version",
  "catalog_schema_version",
  "catalog_updated_at",
  "policy_version",
  "policy_hash",
  "catalog_hash",
  "active_source_ids",
];
const workerRetentionBinding = new URL("../public/data/retention-policy-binding.json", import.meta.url);
const publicationFiles = [
  "intelligence-feed.json",
  "source-status.json",
  "intelligence-summary.json",
  "v2-daily-brief.json",
];

export async function createLegacyPublicArtifacts() {
  const artifacts = Object.fromEntries(await Promise.all(publicationFiles.map(async name => {
    const value = JSON.parse(await readFile(new URL(name, publicRoot), "utf8"));
    delete value.source_policy;
    return [name, Buffer.from(JSON.stringify(value))];
  })));
  const archivedPolicy = JSON.parse(await readFile(legacyPolicy, "utf8"));
  const publicPolicy = Object.fromEntries(legacyPublicPolicyFields.map(key => [key, archivedPolicy[key]]));
  artifacts["source-policy.json"] = Buffer.from(`${JSON.stringify(publicPolicy, null, 2)}\n`);
  return artifacts;
}

export async function readWorkerEmbeddedRetentionBinding() {
  // This is the exact JSON file statically imported by workers/query-gateway/src/index.js.
  return JSON.parse(await readFile(workerRetentionBinding, "utf8"));
}
