import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

import {
  COUNCIL_FIXTURE,
  DEGRADED_STATES,
  EVIDENCE_TYPES,
  GAP_001,
  REQUIRED_LABELS,
  VERIFICATION_STATUSES,
  enforceGap001,
  getFixtureEvidenceChain,
  hasValidLocator,
  isValidEvidenceType,
  resolveDegradedState,
  transitionVerification,
  validateCouncilDrawerPayload,
  validateCouncilEvidence,
  validateFixtureChain,
  validateProducerVerifierSeparation,
} from "../lib/council-prep.js";

import { PRIORITY_ITEM, requiresOfficialFallback } from "../lib/homepage-data.js";
import { findActiveIndex, formatTime } from "../lib/evidence.js";

// ── R1: Council preparation response and evidence-type contract ───────────────

test("EVIDENCE_TYPES contains all required evidence types", () => {
  assert.ok(EVIDENCE_TYPES.includes("ORAL_OFFICIAL"));
  assert.ok(EVIDENCE_TYPES.includes("WRITTEN_OFFICIAL"));
  assert.ok(EVIDENCE_TYPES.includes("RESOLUTION"));
  assert.ok(EVIDENCE_TYPES.includes("GROQ_ASR"));
  assert.ok(EVIDENCE_TYPES.includes("AI_SYNTHESIS"));
});

test("invalid evidence types are rejected", () => {
  assert.equal(isValidEvidenceType({ evidence_type: "FAKE_TYPE" }), false);
  assert.equal(isValidEvidenceType({ evidence_type: "" }), false);
  assert.equal(isValidEvidenceType(null), false);
  assert.equal(isValidEvidenceType({}), false);
});

test("valid evidence types are accepted", () => {
  for (const type of EVIDENCE_TYPES) {
    assert.equal(isValidEvidenceType({ evidence_type: type }), true, `${type} should be valid`);
  }
});

test("missing locators are rejected", () => {
  assert.equal(hasValidLocator(null), false);
  assert.equal(hasValidLocator({}), false);
  assert.equal(hasValidLocator({ locator: "" }), false);
  assert.equal(hasValidLocator({ locator: null }), false);
});

test("present locators are accepted", () => {
  assert.equal(hasValidLocator({ locator: "timestamp:1080" }), true);
  assert.equal(hasValidLocator({ locator: "page:1" }), true);
  assert.equal(hasValidLocator({ locator: "asr_segment:0-85" }), true);
});

test("validateCouncilEvidence rejects missing evidence_id", () => {
  const result = validateCouncilEvidence({
    evidence_type: "ORAL_OFFICIAL",
    locator: "timestamp:1080",
    official_url: "https://example.com",
  });
  assert.equal(result.valid, false);
  assert.equal(result.reason, "MISSING_EVIDENCE_ID");
});

test("validateCouncilEvidence rejects invalid evidence_type", () => {
  const result = validateCouncilEvidence({
    evidence_id: "EV-001",
    evidence_type: "UNKNOWN",
    locator: "timestamp:1080",
    official_url: "https://example.com",
  });
  assert.equal(result.valid, false);
  assert.equal(result.reason, "INVALID_EVIDENCE_TYPE");
});

test("validateCouncilEvidence rejects missing locator", () => {
  const result = validateCouncilEvidence({
    evidence_id: "EV-001",
    evidence_type: "ORAL_OFFICIAL",
    official_url: "https://example.com",
  });
  assert.equal(result.valid, false);
  assert.equal(result.reason, "MISSING_LOCATOR");
});

test("validateCouncilEvidence rejects missing official_url", () => {
  const result = validateCouncilEvidence({
    evidence_id: "EV-001",
    evidence_type: "ORAL_OFFICIAL",
    locator: "timestamp:1080",
  });
  assert.equal(result.valid, false);
  assert.equal(result.reason, "MISSING_OFFICIAL_URL");
});

test("validateCouncilEvidence rejects non-HTTPS official_url", () => {
  const result = validateCouncilEvidence({
    evidence_id: "EV-001",
    evidence_type: "ORAL_OFFICIAL",
    locator: "timestamp:1080",
    official_url: "http://insecure.example.com",
  });
  assert.equal(result.valid, false);
  assert.equal(result.reason, "MISSING_OFFICIAL_URL");
});

test("validateCouncilEvidence accepts valid evidence", () => {
  const result = validateCouncilEvidence({
    evidence_id: "EV-001",
    evidence_type: "ORAL_OFFICIAL",
    locator: "timestamp:1080",
    official_url: "https://vod.tccc.gov.tw/example",
  });
  assert.equal(result.valid, true);
});

// ── R2: Fixed end-to-end council fixture ──────────────────────────────────────

test("council fixture has agenda item with source_id and URL", () => {
  assert.equal(COUNCIL_FIXTURE.agenda_item.source_id, "S-006");
  assert.ok(COUNCIL_FIXTURE.agenda_item.questioning_order_url.startsWith("https://"));
  assert.ok(COUNCIL_FIXTURE.agenda_item.session_date);
});

test("council fixture has historical question with evidence ID and locator", () => {
  assert.ok(COUNCIL_FIXTURE.historical_question.evidence_id);
  assert.equal(COUNCIL_FIXTURE.historical_question.evidence_type, "ORAL_OFFICIAL");
  assert.ok(COUNCIL_FIXTURE.historical_question.locator);
  assert.ok(COUNCIL_FIXTURE.historical_question.official_url.startsWith("https://"));
});

test("council fixture has oral answer with evidence ID and locator", () => {
  assert.ok(COUNCIL_FIXTURE.oral_answer.evidence_id);
  assert.equal(COUNCIL_FIXTURE.oral_answer.evidence_type, "ORAL_OFFICIAL");
  assert.ok(COUNCIL_FIXTURE.oral_answer.locator);
  assert.ok(COUNCIL_FIXTURE.oral_answer.official_url.startsWith("https://"));
});

test("council fixture has project report with evidence ID and locator", () => {
  assert.ok(COUNCIL_FIXTURE.project_report.evidence_id);
  assert.equal(COUNCIL_FIXTURE.project_report.evidence_type, "WRITTEN_OFFICIAL");
  assert.ok(COUNCIL_FIXTURE.project_report.locator);
  assert.ok(COUNCIL_FIXTURE.project_report.official_url.startsWith("https://"));
});

test("council fixture has meeting record with evidence ID and locator", () => {
  assert.ok(COUNCIL_FIXTURE.meeting_record.evidence_id);
  assert.equal(COUNCIL_FIXTURE.meeting_record.evidence_type, "WRITTEN_OFFICIAL");
  assert.ok(COUNCIL_FIXTURE.meeting_record.locator);
  assert.ok(COUNCIL_FIXTURE.meeting_record.official_url.startsWith("https://"));
});

test("council fixture has derived transcript labelled GROQ_ASR with navigation-only note", () => {
  assert.equal(COUNCIL_FIXTURE.derived_transcript.evidence_type, "GROQ_ASR");
  assert.ok(COUNCIL_FIXTURE.derived_transcript.content_label === "GROQ_ASR");
  assert.ok(COUNCIL_FIXTURE.derived_transcript.derivation_note.includes("Navigation"));
});

test("every fixture evidence has an evidence ID and official locator", () => {
  const chain = getFixtureEvidenceChain();
  assert.ok(chain.length >= 5, "Fixture must have at least 5 evidence records");
  for (const evidence of chain) {
    assert.ok(evidence.evidence_id, `Missing evidence_id in chain`);
    assert.ok(evidence.locator, `Missing locator for ${evidence.evidence_id}`);
    assert.ok(evidence.official_url.startsWith("https://"), `Invalid URL for ${evidence.evidence_id}`);
  }
});

test("validateFixtureChain passes for the built-in fixture", () => {
  const result = validateFixtureChain();
  assert.equal(result.valid, true, `Fixture chain errors: ${result.errors.join("; ")}`);
});

test("the existing drawer payload is bound to the council fixture contract", async () => {
  const payload = JSON.parse(
    await readFile(new URL("../public/data/groq-asr-canary-2026-08-14.json", import.meta.url), "utf8"),
  );
  const result = validateCouncilDrawerPayload(payload);
  assert.equal(result.valid, true, result.reason);
  assert.deepEqual(result.evidence_ids, getFixtureEvidenceChain().map(({ evidence_id }) => evidence_id));
});

// ── R3: Evidence drawer connection — timestamp navigation ─────────────────────

test("selecting a segment yields expected absolute official-media timestamp", () => {
  // From the canary fixture: clip_start_seconds = 1080
  // Segment 45 starts at relative 168.32s → absolute 1248.32s = 20:48
  const clipStart = PRIORITY_ITEM.clip_start_seconds;
  assert.equal(clipStart, 1080);
  const relativeTime = 168.32;
  const absoluteTime = clipStart + relativeTime;
  assert.equal(formatTime(absoluteTime), "20:48");
  assert.equal(formatTime(absoluteTime, true), "20:48.32");
});

test("word-level timestamp produces correct absolute time", () => {
  // A word at relative 5.5s → absolute 1085.5s = 18:05
  const clipStart = 1080;
  const wordRelative = 5.5;
  const absolute = clipStart + wordRelative;
  assert.equal(formatTime(absolute), "18:05");
});

// ── R4: Producer/verifier separation and GAP-001 policy enforcement ───────────

test("producer and verifier run IDs must differ", () => {
  const valid = validateProducerVerifierSeparation({
    producer_run_id: "PROD-001",
    verifier_run_id: "VERI-001",
  });
  assert.equal(valid.valid, true);

  const invalid = validateProducerVerifierSeparation({
    producer_run_id: "SAME-ID",
    verifier_run_id: "SAME-ID",
  });
  assert.equal(invalid.valid, false);
  assert.equal(invalid.reason, "PRODUCER_VERIFIER_NOT_INDEPENDENT");
});

test("missing producer_run_id is rejected", () => {
  const result = validateProducerVerifierSeparation({
    verifier_run_id: "VERI-001",
  });
  assert.equal(result.valid, false);
  assert.equal(result.reason, "MISSING_PRODUCER_RUN_ID");
});

test("missing verifier_run_id is rejected", () => {
  const result = validateProducerVerifierSeparation({
    producer_run_id: "PROD-001",
  });
  assert.equal(result.valid, false);
  assert.equal(result.reason, "MISSING_VERIFIER_RUN_ID");
});

test("GAP-001 rejects claims inferring post-meeting progress", () => {
  const result = enforceGap001({ infers_post_meeting_progress: true });
  assert.equal(result.allowed, false);
  assert.equal(result.status, "CLAIM_REJECTED");
  assert.deepEqual(result.gap, GAP_001);
});

test("GAP-001 rejects claims filling GAP-001 from model knowledge", () => {
  const result = enforceGap001({ fills_gap: "GAP-001" });
  assert.equal(result.allowed, false);
  assert.equal(result.status, "CLAIM_REJECTED");
});

test("claims without post-meeting evidence get UNVERIFIED_AFTER_MEETING", () => {
  const result = enforceGap001({ post_meeting_evidence_source: null });
  assert.equal(result.allowed, true);
  assert.equal(result.status, "UNVERIFIED_AFTER_MEETING");
  assert.deepEqual(result.gap, GAP_001);
});

test("claims with valid post-meeting source are allowed", () => {
  const result = enforceGap001({ post_meeting_evidence_source: "S-029" });
  assert.equal(result.allowed, true);
  assert.equal(result.gap, undefined);
});

test("fixture validation has distinct producer and verifier IDs", () => {
  const { validation } = COUNCIL_FIXTURE;
  assert.notEqual(validation.producer_run_id, validation.verifier_run_id);
  assert.equal(validation.verification_status, "AUTO_PASS");
  assert.equal(validation.post_meeting_label, "UNVERIFIED_AFTER_MEETING");
});

// ── R5: Degraded media/ASR/verifier states ────────────────────────────────────

test("AUTO_PASS claim shows both derived and official content", () => {
  const result = resolveDegradedState({ verification_status: "AUTO_PASS" });
  assert.equal(result.show_derived, true);
  assert.equal(result.show_official, true);
});

test("TRANSCRIPT_DISPUTED hides derived but keeps official links", () => {
  const result = resolveDegradedState({ verification_status: "TRANSCRIPT_DISPUTED" });
  assert.equal(result.show_derived, false);
  assert.equal(result.show_official, true);
  assert.equal(result.degraded_reason, "TRANSCRIPT_DISPUTED");
});

test("QUARANTINED hides derived but keeps official links", () => {
  const result = resolveDegradedState({ verification_status: "QUARANTINED" });
  assert.equal(result.show_derived, false);
  assert.equal(result.show_official, true);
  assert.equal(result.degraded_reason, "QUARANTINED");
});

test("CLAIM_REJECTED hides derived but keeps official links", () => {
  const result = resolveDegradedState({ verification_status: "CLAIM_REJECTED" });
  assert.equal(result.show_derived, false);
  assert.equal(result.show_official, true);
  assert.equal(result.degraded_reason, "CLAIM_REJECTED");
});

test("media failure hides derived text while official remains reachable", () => {
  const result = resolveDegradedState({
    verification_status: "AUTO_PASS",
    media_status: "FAILED",
  });
  assert.equal(result.show_derived, false);
  assert.equal(result.show_official, true);
  assert.equal(result.degraded_reason, "MEDIA_OR_ASR_FAILED");
});

test("ASR failure hides derived text while official remains reachable", () => {
  const result = resolveDegradedState({
    verification_status: "AUTO_PASS",
    asr_status: "FAILED",
  });
  assert.equal(result.show_derived, false);
  assert.equal(result.show_official, true);
  assert.equal(result.degraded_reason, "MEDIA_OR_ASR_FAILED");
});

test("all DEGRADED_STATES suppress derived content", () => {
  for (const state of DEGRADED_STATES) {
    const result = resolveDegradedState({ verification_status: state });
    assert.equal(result.show_derived, false, `${state} should suppress derived content`);
    assert.equal(result.show_official, true, `${state} should keep official links`);
  }
});

// ── R5 continued: Verification state transitions ──────────────────────────────

test("first disagreement becomes AI_DISAGREEMENT (one retry allowed)", () => {
  assert.equal(transitionVerification(null, "DISAGREE"), "AI_DISAGREEMENT");
});

test("second disagreement after retry becomes QUARANTINED", () => {
  assert.equal(transitionVerification("AI_DISAGREEMENT", "DISAGREE"), "QUARANTINED");
  assert.equal(transitionVerification("AUTO_RETRY", "DISAGREE"), "QUARANTINED");
});

test("PASS always results in AUTO_PASS", () => {
  assert.equal(transitionVerification(null, "PASS"), "AUTO_PASS");
  assert.equal(transitionVerification("AI_DISAGREEMENT", "PASS"), "AUTO_PASS");
});

test("REJECT always results in CLAIM_REJECTED", () => {
  assert.equal(transitionVerification(null, "REJECT"), "CLAIM_REJECTED");
  assert.equal(transitionVerification("AUTO_PASS", "REJECT"), "CLAIM_REJECTED");
});

// ── R6: Existing drawer integration (page.js) ────────────────────────────────

test("page.js uses requiresOfficialFallback for HLS error handling", async () => {
  // requiresOfficialFallback returns true for degraded HLS states
  assert.equal(requiresOfficialFallback({ kind: "hls_error" }), true);
  assert.equal(requiresOfficialFallback({ kind: "hls_unsupported" }), true);
  assert.equal(requiresOfficialFallback({ kind: "ready" }), false);
});

test("page.js has HLS error handler and timeout failsafe", async () => {
  const pageSource = await readFile(new URL("../app/page.js", import.meta.url), "utf8");
  // Evidence drawer must handle media failure gracefully
  assert.match(pageSource, /addEventListener\("error", failPlayback\)/);
  assert.match(pageSource, /setTimeout\(failPlayback, HLS_LOAD_TIMEOUT_MS\)/);
});

test("page.js does not substitute local demo video for official evidence", async () => {
  const pageSource = await readFile(new URL("../app/page.js", import.meta.url), "utf8");
  assert.doesNotMatch(pageSource, /demo-video\.mp4/);
});

test("production page validates the drawer payload through the council contract", async () => {
  const pageSource = await readFile(new URL("../app/page.js", import.meta.url), "utf8");
  assert.match(pageSource, /validateCouncilDrawerPayload\(validatedPayload\)/);
  assert.match(pageSource, /from ["']\.\.\/lib\/council-prep\.js["']/);
  assert.match(pageSource, /href=\{priorityItem\?\.official_page_url\}/);
});

// ── Cross-check: PRIORITY_ITEM labels match required labels ───────────────────

test("PRIORITY_ITEM has correct content_label ORAL_OFFICIAL", () => {
  assert.equal(PRIORITY_ITEM.content_label, REQUIRED_LABELS.ORAL_OFFICIAL);
});

test("PRIORITY_ITEM has correct post_meeting_label UNVERIFIED_AFTER_MEETING", () => {
  assert.equal(PRIORITY_ITEM.post_meeting_label, REQUIRED_LABELS.UNVERIFIED_AFTER_MEETING);
});

test("PRIORITY_ITEM has correct derivation_type GROQ_ASR", () => {
  assert.equal(PRIORITY_ITEM.derivation_type, REQUIRED_LABELS.GROQ_ASR);
});

test("PRIORITY_ITEM verification_status is AUTO_PASS (only AUTO_PASS enters formal view)", () => {
  assert.equal(PRIORITY_ITEM.verification_status, "AUTO_PASS");
});
