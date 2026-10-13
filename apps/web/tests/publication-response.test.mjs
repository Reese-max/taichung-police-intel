import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import test from "node:test";
import { fetchBoundPublication } from "../lib/publication-response.js";

test("publication binding hashes the exact received UTF-8 bytes and overrides self-declared hashes", async () => {
  const text = '{\n  "schema_version": 1, "title": "公告", "brief_sha256": "untrusted", "publication_hash": "untrusted"\n}\n';
  let observed;
  const publication = await fetchBoundPublication("/data/brief.json", async (url, options) => {
    observed = { url, options }; return new Response(text);
  });
  assert.deepEqual(observed, { url: "/data/brief.json", options: { cache: "no-store" } });
  assert.equal(publication.brief_sha256, createHash("sha256").update(text).digest("hex"));
  assert.notEqual(publication.brief_sha256, createHash("sha256").update(JSON.stringify(JSON.parse(text))).digest("hex"));
  assert.equal(publication.title, "公告");
});

test("publication binding refuses HTTP failure or a non-object artifact", async () => {
  await assert.rejects(fetchBoundPublication("/brief", async () => new Response("{}", { status: 503 })), /HTTP 503/);
  await assert.rejects(fetchBoundPublication("/brief", async () => new Response("[]")), /JSON object/);
});
