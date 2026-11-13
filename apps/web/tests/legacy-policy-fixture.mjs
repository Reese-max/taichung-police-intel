import { readFile } from "node:fs/promises";

const publicRoot = new URL("../public/data/", import.meta.url);
// Keep this immutable history pin in sync with tests/governed_policy_fixture.py.
const legacyPolicy = new URL(
  "../../../docs/govintel/source-policy-history/3866b8d3a062291240bb2c8540d68055ed0cdfaf3ac5a72ffb570875327c8501.json",
  import.meta.url,
);
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
  artifacts["source-policy.json"] = await readFile(legacyPolicy);
  return artifacts;
}
