import { execFileSync } from "node:child_process";
import { readFile, writeFile } from "node:fs/promises";
import { resolve } from "node:path";
import { fileURLToPath } from "node:url";

const webRoot = resolve(fileURLToPath(new URL("..", import.meta.url)));
const catalogPath = resolve(webRoot, "../../docs/govintel/source-catalog.v2.json");
const outputPath = resolve(webRoot, "public/data/source-policy.json");
const catalog = JSON.parse(await readFile(catalogPath, "utf8"));
if (catalog.schema_version !== 2 || !Array.isArray(catalog.sources)) {
  throw new Error("invalid source catalog");
}

const activeSourceIds = catalog.sources
  .filter(source => source.status === "PRODUCTION_ACTIVE")
  .map(source => source.source_id)
  .sort();
if (!activeSourceIds.length || new Set(activeSourceIds).size !== activeSourceIds.length) {
  throw new Error("source catalog must contain unique active sources");
}

const repoRoot = resolve(webRoot, "../..");
const python = process.platform === "win32" ? "python" : "python3";
const policy = JSON.parse(execFileSync(python, ["-X", "utf8", "scripts/source-policy.py"], {
  cwd: repoRoot,
  encoding: "utf8",
}));
if (policy.schema_version !== 1 || !Number.isInteger(policy.policy_version) ||
    !/^[0-9a-f]{64}$/.test(policy.policy_hash || "") ||
    !/^[0-9a-f]{64}$/.test(policy.catalog_hash || "") ||
    JSON.stringify(policy.active_source_ids) !== JSON.stringify(activeSourceIds)) {
  throw new Error("compiled source policy does not match the catalog projection");
}

const projection = {
  schema_version: 1,
  catalog_schema_version: catalog.schema_version,
  catalog_updated_at: catalog.updated_at,
  policy_version: policy.policy_version,
  policy_hash: policy.policy_hash,
  catalog_hash: policy.catalog_hash,
  active_source_ids: activeSourceIds,
};
await writeFile(outputPath, `${JSON.stringify(projection, null, 2)}\n`, "utf8");
console.log(`SOURCE_POLICY_SYNC_OK active=${activeSourceIds.length} output=${outputPath}`);
