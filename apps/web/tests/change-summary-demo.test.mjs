import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import {
  highlightSegments, spansForSide, validateChangeSummaryDemo,
} from "../lib/change-summary-view.js";

const dataUrl = new URL("../public/data/change-summary-demo.json", import.meta.url);
const pageUrl = new URL("../app/change-summary/page.js", import.meta.url);
const load = async () => JSON.parse(await readFile(dataUrl, "utf8"));

test("change-summary demo is fixture-only, pending review and unpublished", async () => {
  const data = await load();
  assert.equal(data.status, "FIXTURE_ONLY");
  assert.equal(data.production_verified, false);
  assert.equal(data.summary.status, "PENDING_REVIEW");
  assert.equal(data.summary.published, false);
  assert.equal(data.summary.ai_disclosure.role, "CANDIDATE_DRAFT_ONLY");
  assert.ok(validateChangeSummaryDemo(data));
});

test("a quote that no longer matches the displayed text fails closed", async () => {
  const data = await load();
  data.after_text = data.after_text.replace("11月30日", "12月31日");
  assert.equal(validateChangeSummaryDemo(data), false);
  const published = await load();
  published.summary.published = true;
  assert.equal(validateChangeSummaryDemo(published), false);
  const real = await load();
  real.status = "PRODUCTION_ACTIVE";
  assert.equal(validateChangeSummaryDemo(real), false);
});

test("highlight segments cover the text exactly and mark only quoted spans", async () => {
  const data = await load();
  const spans = spansForSide(data.summary.statements, "after");
  const segments = highlightSegments(data.after_text, spans);
  assert.equal(segments.map((s) => s.text).join(""), data.after_text);
  assert.deepEqual(segments.filter((s) => s.quoted).map((s) => s.text), spans.map((s) => s.quote));
  assert.deepEqual(highlightSegments("abc", [{ start: 0, end: 2 }, { start: 1, end: 3 }]).map((s) => s.text), ["ab", "c"]);
});

test("the page labels the AI draft as unreviewed fixture data", async () => {
  const page = await readFile(pageUrl, "utf8");
  assert.match(page, /FIXTURE_ONLY/);
  assert.match(page, /僅候選草稿/);
  assert.match(page, /尚未經人工核對/);
  assert.match(page, /validateChangeSummaryDemo/);
});
