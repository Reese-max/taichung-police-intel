import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import test from "node:test";
import { createHash } from "node:crypto";
import { projectDateEvidence, projectPublicationDates, projectSourceDates, publicationDate, sourceDateLabel } from "../lib/publication-dates.js";
import { createGovernedPolicyFixture } from "./governed-policy-fixture.mjs";
import { gateAnswer } from "../lib/answer-evidence-gate.js";

const root = fileURLToPath(new URL("../../../", import.meta.url));
const fixture = JSON.parse(await readFile(new URL("../../../tests/fixtures/date-provenance/revision-metadata.json", import.meta.url)));
const approved = ["https://www.tccc.gov.tw"];

test("date presentation keeps revision, publication and observation meanings separate", () => {
  assert.deepEqual(publicationDate(fixture.item), { label: "官方修訂日期", value: fixture.item.document_revision_at });
  assert.deepEqual(publicationDate({ published_at: null, fetched_at: fixture.item.fetched_at, data_as_of: fixture.item.fetched_at }), { label: "發布時間", value: null });
  assert.equal(publicationDate({ ...fixture.item, published_at: "2026-08-01T00:00:00+08:00" }).label, "發布時間");
  assert.equal(publicationDate({published_at: "2026-05-27T01:12:00+08:00", date_basis: "OFFICIAL_API_RECORD_DATE"}).label, "官方記錄日期");
  assert.equal(sourceDateLabel(fixture.source), "官方修訂日期（已核對附件）");
  assert.equal(sourceDateLabel({data_as_of_basis: "constructor"}), "官方資料截至");
});

test("JS and Python publish the same closed date metadata", () => {
  const result = spawnSync(process.env.PYTHON || "python3", ["-B", "-c", `import importlib.util,json,sys
from pathlib import Path
root=Path(sys.argv[1]); spec=importlib.util.spec_from_file_location('projection',root/'scripts/query-store.py'); module=importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
fixture=json.loads((root/'tests/fixtures/date-provenance/revision-metadata.json').read_text())
print(json.dumps({'item':module._project_publication_dates(fixture['item']), 'source':module._project_source_dates(fixture['source'])}))`, root], {encoding: "utf8", timeout: 10000});
  assert.equal(result.status, 0, result.stderr);
  assert.deepEqual(JSON.parse(result.stdout), { item: projectPublicationDates(fixture.item, approved), source: projectSourceDates(fixture.source, approved) });
  assert.equal(JSON.stringify(projectPublicationDates(fixture.item, approved)).includes("FICTIONAL_PRIVATE_HEADER"), false);
});

test("revision evidence rejects invalid provenance and dates before rendering", () => {
  for (const mutation of [
    {official_url: "https://unapproved.example.test/fixture.pdf"},
    {official_url: "https://user:secret@www.tccc.gov.tw/fixture.pdf"},
    {official_url: "https://www.tccc.gov.tw/fixture.doc"}, {content_sha256: "WRONG"},
    {page_number: 0}, {page_number: true}, {document_revision_at: "2026-08-28T00:00:00+08:00"},
    {document_revision_at: "2026-08-27T00:00:00"}, {document_revision_at: "2026-02-30T00:00:00+08:00"},
    {private_notes: "FICTIONAL_PRIVATE"}, {excerpt: {body: "FICTIONAL_PRIVATE"}},
  ]) assert.throws(() => projectDateEvidence({...fixture.item.date_evidence, ...mutation}, fixture.item.document_revision_at, approved));
  for (const key of ["document_revision_at", "date_basis", "date_evidence"]) {
    const item = structuredClone(fixture.item); delete item[key];
    assert.throws(() => projectPublicationDates(item, approved));
  }
  assert.throws(() => projectSourceDates({...fixture.source, data_as_of_scope: "COLLECTION_WINDOW"}, approved));
  assert.equal(projectSourceDates({...fixture.source, data_as_of_basis: "OFFICIAL_LIST_TITLE_DATE", data_as_of_evidence: null}, approved).data_as_of_scope, "LATEST_EVIDENCED_DOCUMENT_VERSION");
});

test("Worker retains source provenance and respects existing rights field restrictions", async () => {
  const policyFixture = await createGovernedPolicyFixture();
  try {
    const module = await policyFixture.loadWorker("official-date-metadata");
    const bytes = await policyFixture.publication();
    const feed = JSON.parse(bytes["intelligence-feed.json"]);
    feed.items = [fixture.item];
    bytes["intelligence-feed.json"] = Buffer.from(JSON.stringify(feed));
    const status = JSON.parse(bytes["source-status.json"]);
    const source = status.sources.find(row => row.source_id === "S-006");
    Object.assign(source, fixture.source, projectSourceDates(fixture.source, approved));
    bytes["source-status.json"] = Buffer.from(JSON.stringify(status));
    const read = async name => ({bytes: bytes[name], hash: createHash("sha256").update(bytes[name]).digest("hex")});
    const snapshot = await module.buildSnapshot({PUBLIC_ORIGIN: "https://fictional.example.test"}, read);
    assert.equal(snapshot.items.length, 1);
    assert.equal(snapshot.items[0].published_at, null);
    for (const key of ["document_revision_at", "date_basis", "date_evidence"]) assert.equal(Object.hasOwn(snapshot.items[0], key), false, `${key} is not in the approved public field whitelist`);
    const projectedSource = snapshot.sources.find(row => row.source_id === "S-006");
    for (const [key, value] of Object.entries(projectSourceDates(fixture.source, approved))) assert.deepEqual(projectedSource[key], value);
    assert.equal(JSON.stringify({items: snapshot.items, sources: snapshot.sources}).includes("FICTIONAL_PRIVATE_HEADER"), false);
    const originalFetch = globalThis.fetch;
    const healthEnv = {PUBLIC_ORIGIN: "https://fictional.example.test", CF_VERSION_METADATA: {id: "fixture-worker", tag: "a".repeat(40)}};
    try {
      globalThis.fetch = async url => {
        const name = String(url).split("/").at(-1);
        if (name === "release.json") return Response.json(await module.createReleaseManifest(await module.buildSnapshot(healthEnv, read), healthEnv.CF_VERSION_METADATA.tag));
        return new Response(bytes[name], {status: bytes[name] ? 200 : 404});
      };
      const response = await module.default.fetch(new Request("https://fixture-gateway.example/query", {
        method: "POST", headers: {"Content-Type": "application/json"},
        body: JSON.stringify({tool: "get_source_health", arguments: {source_id: "S-006"}}),
      }), healthEnv);
      assert.equal(response.status, 200);
      const health = await response.json();
      assert.equal(health.sources.length, 1);
      for (const [key, value] of Object.entries(projectSourceDates(fixture.source, approved))) assert.deepEqual(health.sources[0][key], value);
      assert.equal(JSON.stringify(health).includes("FICTIONAL_PRIVATE_HEADER"), false);
    } finally { globalThis.fetch = originalFetch; }
    // Catalog unit: an internally projected future permitted metadata row. The
    // preceding assertions separately prove today's rights gate omits these fields.
    Object.assign(snapshot.items[0], projectPublicationDates(fixture.item, approved));
    let captured;
    const verdict = await module.validateAnswer(snapshot, [], payload => {
      captured = payload.evidence;
      return gateAnswer(payload);
    });
    assert.equal(captured.length, 1);
    assert.equal(captured[0].published_at, null);
    assert.equal(captured[0].observed_at, fixture.item.fetched_at);
    assert.equal(captured[0].document_revision_at, fixture.item.document_revision_at);
    assert.equal(captured[0].date_basis, fixture.item.date_basis);
    const canonical = value => Array.isArray(value) ? `[${value.map(canonical).join(",")}]` :
      value && typeof value === "object" ? `{${Object.keys(value).sort().map(key => `${JSON.stringify(key)}:${canonical(value[key])}`).join(",")}}` : JSON.stringify(value);
    assert.equal(verdict.receipt.evidence_catalog_hash, createHash("sha256").update(canonical(captured)).digest("hex"));
    const pythonCatalog = spawnSync(process.env.PYTHON || "python3", ["-B", "-c", `import importlib.util,json,sys
from pathlib import Path
root=Path(sys.argv[1]); spec=importlib.util.spec_from_file_location('gateway',root/'scripts/query-gateway.py'); module=importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
fixture=json.loads((root/'tests/fixtures/date-provenance/revision-metadata.json').read_text()); item=module.qs.project_feed_item(fixture['item'],'a'*64); source=module.qs.project_source(fixture['source'],'b'*64)
print(json.dumps(module.QueryGateway._trusted_evidence_catalog({'items':[item],'sources':[source]})))`, root], {encoding: "utf8", timeout: 10000});
    assert.equal(pythonCatalog.status, 0, pythonCatalog.stderr);
    assert.deepEqual(JSON.parse(pythonCatalog.stdout), captured);
    source.data_as_of_evidence.official_url = "https://unapproved.example.test/fixture.pdf";
    bytes["source-status.json"] = Buffer.from(JSON.stringify(status));
    await assert.rejects(module.buildSnapshot({PUBLIC_ORIGIN: "https://fictional.example.test"}, read), /date evidence origin/);
  } finally { await policyFixture.cleanup(); }
});
