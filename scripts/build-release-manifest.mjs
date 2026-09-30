import { createHash } from "node:crypto";
import { readFile, writeFile } from "node:fs/promises";
import { resolve } from "node:path";
import { parseArgs } from "node:util";
import { buildSnapshot, createReleaseManifest } from "../workers/query-gateway/src/index.js";

const { values } = parseArgs({ options: {
  "data-dir": { type: "string" }, output: { type: "string" }, "code-sha": { type: "string" },
} });
if (!values["data-dir"] || !values["code-sha"]) throw new Error("usage: --data-dir <built data> --code-sha <Git SHA> [--output <file>]");
const directory = resolve(values["data-dir"]);
const snapshot = await buildSnapshot({ PUBLIC_ORIGIN: "local-build" }, async name => {
  const bytes = await readFile(resolve(directory, name));
  return { value: JSON.parse(bytes), hash: createHash("sha256").update(bytes).digest("hex") };
});
const release = await createReleaseManifest(snapshot, values["code-sha"]);
await writeFile(values.output || resolve(directory, "release.json"), `${JSON.stringify(release, null, 2)}\n`);
console.log(`RELEASE_MANIFEST_OK release=${release.release_id} evidence=BUILD_ONLY`);
