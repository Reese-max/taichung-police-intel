import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

import {
  CENTRAL_POLICY_CARDS,
  CENTRAL_POLICY_LIMIT,
  COPY,
  PRIORITY_ITEM,
  SOURCE_NAMES_EN,
  formatPlaybackStatus,
  limitCentralPolicyCards,
  requiresOfficialFallback,
} from "../lib/homepage-data.js";

// ── R4: limitCentralPolicyCards pure function ─────────────────────────────────

test("limitCentralPolicyCards returns empty array for empty input", () => {
  assert.deepEqual(limitCentralPolicyCards([]), []);
});

test("limitCentralPolicyCards passes through 1 card unchanged", () => {
  const cards = [{ evidence_source_id: "X-001", official_stage: "draft" }];
  assert.deepEqual(limitCentralPolicyCards(cards), cards);
});

test("limitCentralPolicyCards passes through exactly 2 cards unchanged", () => {
  const cards = [
    { evidence_source_id: "X-001", official_stage: "draft" },
    { evidence_source_id: "X-002", official_stage: "enacted" },
  ];
  assert.deepEqual(limitCentralPolicyCards(cards), cards);
});

test("limitCentralPolicyCards with 3 cards returns only the first 2", () => {
  const cards = [
    { evidence_source_id: "X-001", official_stage: "draft" },
    { evidence_source_id: "X-002", official_stage: "enacted" },
    { evidence_source_id: "X-003", official_stage: "review" },
  ];
  const result = limitCentralPolicyCards(cards);
  assert.equal(result.length, 2, "Must cap at 2, not 3");
  assert.equal(result[0].evidence_source_id, "X-001");
  assert.equal(result[1].evidence_source_id, "X-002");
});

test("limitCentralPolicyCards with 5 cards returns only the first 2", () => {
  const cards = Array.from({ length: 5 }, (_, i) => ({
    evidence_source_id: `X-00${i + 1}`,
    official_stage: "stage",
  }));
  assert.equal(limitCentralPolicyCards(cards).length, 2);
});

test("CENTRAL_POLICY_LIMIT constant is exactly 2", () => {
  assert.equal(CENTRAL_POLICY_LIMIT, 2);
});

test("current CENTRAL_POLICY_CARDS does not exceed CENTRAL_POLICY_LIMIT", () => {
  assert.ok(
    CENTRAL_POLICY_CARDS.length <= CENTRAL_POLICY_LIMIT,
    `Expected ≤${CENTRAL_POLICY_LIMIT} central-policy cards, got ${CENTRAL_POLICY_CARDS.length}`,
  );
});

test("limitCentralPolicyCards does not mutate the input array", () => {
  const cards = [
    { evidence_source_id: "X-001", official_stage: "draft" },
    { evidence_source_id: "X-002", official_stage: "enacted" },
    { evidence_source_id: "X-003", official_stage: "review" },
  ];
  const original = [...cards];
  limitCentralPolicyCards(cards);
  assert.deepEqual(cards, original, "Input array must not be mutated");
});

test("playback status follows the selected language without resetting state", () => {
  const status = { kind: "seeked", timestamp: "18:00.00" };
  assert.equal(formatPlaybackStatus(status, COPY.en), "Seeked to official video 18:00.00.");
  assert.equal(formatPlaybackStatus(status, COPY.zh), "已定位至官方影音 18:00.00。");
  assert.equal(formatPlaybackStatus({ kind: "ready" }, COPY.en), COPY.en.playback_ready);
});

test("unavailable HLS keeps an explicit official-source fallback", async () => {
  assert.equal(requiresOfficialFallback({ kind: "hls_error" }), true);
  assert.equal(requiresOfficialFallback({ kind: "hls_unsupported" }), true);
  assert.equal(requiresOfficialFallback({ kind: "ready" }), false);

  const pageSource = await readFile(new URL("../app/page.js", import.meta.url), "utf8");
  assert.match(pageSource, /addEventListener\("error", failPlayback\)/);
  assert.match(pageSource, /setTimeout\(failPlayback, HLS_LOAD_TIMEOUT_MS\)/);
  assert.doesNotMatch(pageSource, /demo-video\.mp4/);
});

test("every source in the competition snapshot has an English display name", async () => {
  const status = JSON.parse(await readFile(new URL("../public/data/source-status.json", import.meta.url), "utf8"));
  assert.deepEqual(
    status.sources.map(({ source_id }) => source_id).filter((sourceId) => !SOURCE_NAMES_EN[sourceId]),
    [],
  );
});

// ── R6: shared evidence identifiers ──────────────────────────────────────────

test("PRIORITY_ITEM evidence_source_id is S-010", () => {
  assert.equal(PRIORITY_ITEM.evidence_source_id, "S-010");
});

test("PRIORITY_ITEM official_page_url is the Taichung council VOD URL", () => {
  assert.equal(
    PRIORITY_ITEM.official_page_url,
    "https://vod.tccc.gov.tw/wb_news02.asp?url=92&ano=14170&pageno=1",
  );
});

test("PRIORITY_ITEM meeting_records_url is the Taichung council yishi URL", () => {
  assert.equal(
    PRIORITY_ITEM.meeting_records_url,
    "https://yishi.tccc.gov.tw/meeting-records/292a8e4a-0e1e-4429-8889-72bc56bc895d",
  );
});

test("PRIORITY_ITEM verification_status is AUTO_PASS", () => {
  assert.equal(PRIORITY_ITEM.verification_status, "AUTO_PASS");
});

test("PRIORITY_ITEM content_label is ORAL_OFFICIAL", () => {
  assert.equal(PRIORITY_ITEM.content_label, "ORAL_OFFICIAL");
});

test("PRIORITY_ITEM post_meeting_label is UNVERIFIED_AFTER_MEETING", () => {
  assert.equal(PRIORITY_ITEM.post_meeting_label, "UNVERIFIED_AFTER_MEETING");
});

test("PRIORITY_ITEM derivation_type is GROQ_ASR", () => {
  assert.equal(PRIORITY_ITEM.derivation_type, "GROQ_ASR");
});

test("PRIORITY_ITEM clip_start_seconds is a finite positive number", () => {
  assert.ok(
    typeof PRIORITY_ITEM.clip_start_seconds === "number" &&
    Number.isFinite(PRIORITY_ITEM.clip_start_seconds) &&
    PRIORITY_ITEM.clip_start_seconds > 0,
  );
});

// ── R6: bilingual copy contract ───────────────────────────────────────────────

const EN_KEYS = Object.keys(COPY.en);
const ZH_KEYS = Object.keys(COPY.zh);

test("COPY.en and COPY.zh have the same set of keys", () => {
  const enSet = new Set(EN_KEYS);
  const zhSet = new Set(ZH_KEYS);
  const onlyInEn = EN_KEYS.filter((k) => !zhSet.has(k));
  const onlyInZh = ZH_KEYS.filter((k) => !enSet.has(k));
  assert.deepEqual(
    onlyInEn,
    [],
    `Keys in EN but not ZH: ${onlyInEn.join(", ")}`,
  );
  assert.deepEqual(
    onlyInZh,
    [],
    `Keys in ZH but not EN: ${onlyInZh.join(", ")}`,
  );
});

test("every COPY.en value is a non-empty string", () => {
  for (const key of EN_KEYS) {
    assert.ok(
      typeof COPY.en[key] === "string" && COPY.en[key].length > 0,
      `COPY.en.${key} must be a non-empty string`,
    );
  }
});

test("every COPY.zh value is a non-empty string", () => {
  for (const key of ZH_KEYS) {
    assert.ok(
      typeof COPY.zh[key] === "string" && COPY.zh[key].length > 0,
      `COPY.zh.${key} must be a non-empty string`,
    );
  }
});

// ── Language toggle labels ─────────────────────────────────────────────────────
// The toggle label must be the OTHER language (pressing it switches to that language).

test("COPY.en.lang_toggle_label is a non-empty string (button shown when viewing English)", () => {
  assert.ok(
    typeof COPY.en.lang_toggle_label === "string" && COPY.en.lang_toggle_label.length > 0,
  );
});

test("COPY.zh.lang_toggle_label is a non-empty string (button shown when viewing Chinese)", () => {
  assert.ok(
    typeof COPY.zh.lang_toggle_label === "string" && COPY.zh.lang_toggle_label.length > 0,
  );
});

test("COPY.en.lang_toggle_label and COPY.zh.lang_toggle_label are different strings", () => {
  assert.notEqual(
    COPY.en.lang_toggle_label,
    COPY.zh.lang_toggle_label,
    "Toggle labels must differ between language paths",
  );
});

// ── Key journey labels present in both paths ──────────────────────────────────

const REQUIRED_JOURNEY_KEYS = [
  "card_action_drawer",
  "card_action_records",
  "drawer_close",
  "transport_back",
  "follow_on",
  "follow_off",
  "source_back_to_official",
  "source_monitor_heading",
  "drawer_heading",
  "transcript_heading",
  "transcript_note",
  "workflow_body",
  "limitation_note",
  "playback_open_official",
];

for (const key of REQUIRED_JOURNEY_KEYS) {
  test(`COPY.en.${key} is present and non-empty`, () => {
    assert.ok(
      typeof COPY.en[key] === "string" && COPY.en[key].length > 0,
      `COPY.en.${key} missing or empty`,
    );
  });
  test(`COPY.zh.${key} is present and non-empty`, () => {
    assert.ok(
      typeof COPY.zh[key] === "string" && COPY.zh[key].length > 0,
      `COPY.zh.${key} missing or empty`,
    );
  });
}

// ── Limitation label must mention GROQ_ASR in both languages ─────────────────

test("COPY.en.limitation_note mentions GROQ_ASR", () => {
  assert.ok(
    COPY.en.limitation_note.includes("GROQ_ASR"),
    "EN limitation note must name the derivation type",
  );
});

test("COPY.zh.limitation_note mentions GROQ_ASR", () => {
  assert.ok(
    COPY.zh.limitation_note.includes("GROQ_ASR"),
    "ZH limitation note must name the derivation type",
  );
});

test("COPY.en.transcript_note labels the transcript as Chinese official-source navigation", () => {
  const note = COPY.en.transcript_note.toLowerCase();
  assert.ok(
    note.includes("chinese") || note.includes("official"),
    "EN transcript note must describe the Chinese official-source nature of the transcript",
  );
});

// ── Spec tasks: typed homepage response, eligibility, deterministic ordering ──

import {
  HOMEPAGE_ITEM_LIMIT,
  REASON_CODES,
  buildHomepageResponse,
  checkEligibility,
  deterministicSort,
  validateGeneratedWording,
  verifySentence,
} from "../lib/homepage-eligibility.js";

// ── Eligibility function ──────────────────────────────────────────────────────

test("HOMEPAGE_ITEM_LIMIT is exactly 10", () => {
  assert.equal(HOMEPAGE_ITEM_LIMIT, 10);
});

test("checkEligibility rejects non-AUTO_PASS items", () => {
  const item = {
    verification_status: "QUARANTINED",
    content_disposition: "HOME_CANDIDATE",
    evidence_ids: ["EV-001"],
    official_url: "https://example.com",
    reason_codes: ["RECURRING"],
  };
  const result = checkEligibility(item);
  assert.equal(result.eligible, false);
  assert.equal(result.reason, "NOT_AUTO_PASS");
});

test("checkEligibility rejects non-HOME_CANDIDATE items", () => {
  const item = {
    verification_status: "AUTO_PASS",
    content_disposition: "SEARCH_ONLY",
    evidence_ids: ["EV-001"],
    official_url: "https://example.com",
    reason_codes: ["HIGH_VALUE"],
  };
  assert.equal(checkEligibility(item).eligible, false);
  assert.equal(checkEligibility(item).reason, "NOT_HOME_CANDIDATE");
});

test("checkEligibility rejects items without evidence", () => {
  const item = {
    item_id: "HP-NO-EVIDENCE",
    verification_status: "AUTO_PASS",
    content_disposition: "HOME_CANDIDATE",
    evidence_ids: [],
    official_url: "https://example.com",
    reason_codes: ["HIGH_VALUE"],
  };
  assert.equal(checkEligibility(item).eligible, false);
  assert.equal(checkEligibility(item).reason, "NO_EVIDENCE");
});

test("checkEligibility accepts valid AUTO_PASS HOME_CANDIDATE item", () => {
  const item = {
    item_id: "HP-VALID",
    verification_status: "AUTO_PASS",
    content_disposition: "HOME_CANDIDATE",
    evidence_ids: ["EV-001"],
    official_url: "https://example.com",
    reason_codes: ["COUNCIL_ATTENTION"],
  };
  assert.equal(checkEligibility(item).eligible, true);
});

test("checkEligibility rejects candidates without a stable identity", () => {
  const item = {
    verification_status: "AUTO_PASS",
    content_disposition: "HOME_CANDIDATE",
    evidence_ids: ["EV-001"],
    official_url: "https://example.com",
    reason_codes: ["COUNCIL_ATTENTION"],
  };
  assert.equal(checkEligibility(item).reason, "MISSING_STABLE_ID");
});

test("checkEligibility rejects unknown reason codes", () => {
  const item = {
    item_id: "HP-UNKNOWN-REASON",
    verification_status: "AUTO_PASS",
    content_disposition: "HOME_CANDIDATE",
    evidence_ids: ["EV-001"],
    official_url: "https://example.com",
    reason_codes: ["MADE_UP"],
  };
  assert.equal(checkEligibility(item).reason, "INVALID_REASON_CODE");
});

// ── buildHomepageResponse enforces limits ─────────────────────────────────────

test("buildHomepageResponse returns at most 10 items", () => {
  const candidates = Array.from({ length: 15 }, (_, i) => ({
    item_id: `HP-${i}`,
    verification_status: "AUTO_PASS",
    content_disposition: "HOME_CANDIDATE",
    item_value_score: 80 - i,
    evidence_ids: [`EV-${i}`],
    official_url: "https://example.com",
    reason_codes: ["HIGH_VALUE"],
  }));
  const response = buildHomepageResponse(candidates);
  assert.ok(response.items.length <= 10, `Expected ≤10 items, got ${response.items.length}`);
  assert.equal(response.total_candidates, 15);
});

test("buildHomepageResponse rejects non-AUTO_PASS items", () => {
  const candidates = [
    {
      item_id: "HP-GOOD",
      verification_status: "AUTO_PASS",
      content_disposition: "HOME_CANDIDATE",
      item_value_score: 90,
      evidence_ids: ["EV-001"],
      official_url: "https://example.com",
      reason_codes: ["RECURRING"],
    },
    {
      item_id: "HP-BAD",
      verification_status: "QUARANTINED",
      content_disposition: "HOME_CANDIDATE",
      item_value_score: 95,
      evidence_ids: ["EV-002"],
      official_url: "https://example.com",
      reason_codes: ["HIGH_VALUE"],
    },
  ];
  const response = buildHomepageResponse(candidates);
  assert.equal(response.items.length, 1);
  assert.equal(response.items[0].item_id, "HP-GOOD");
  assert.equal(response.rejected.length, 1);
  assert.equal(response.rejected[0].rejection_reason, "NOT_AUTO_PASS");
});

// ── Deterministic ordering ────────────────────────────────────────────────────

test("deterministicSort produces identical order across repeated runs", () => {
  const items = [
    { item_id: "A", reason_codes: ["HIGH_VALUE"], item_value_score: 90 },
    { item_id: "B", reason_codes: ["COUNCIL_ATTENTION"], item_value_score: 80 },
    { item_id: "C", reason_codes: ["RECURRING"], item_value_score: 85 },
  ];
  const sorted1 = deterministicSort(items);
  const sorted2 = deterministicSort(items);
  assert.deepEqual(
    sorted1.map((i) => i.item_id),
    sorted2.map((i) => i.item_id),
  );
  // COUNCIL_ATTENTION has highest priority
  assert.equal(sorted1[0].item_id, "B");
  // RECURRING is next
  assert.equal(sorted1[1].item_id, "C");
  // HIGH_VALUE is lowest priority
  assert.equal(sorted1[2].item_id, "A");
});

test("deterministicSort breaks ties by score descending", () => {
  const items = [
    { item_id: "X", reason_codes: ["RECURRING"], item_value_score: 70 },
    { item_id: "Y", reason_codes: ["RECURRING"], item_value_score: 95 },
  ];
  const sorted = deterministicSort(items);
  assert.equal(sorted[0].item_id, "Y");
  assert.equal(sorted[1].item_id, "X");
});

test("deterministicSort breaks equal reason and score ties by stable item ID", () => {
  const items = [
    { item_id: "Z-ITEM", reason_codes: ["RECURRING"], item_value_score: 70 },
    { item_id: "A-ITEM", reason_codes: ["RECURRING"], item_value_score: 70 },
  ];
  assert.deepEqual(
    deterministicSort(items).map((item) => item.item_id),
    ["A-ITEM", "Z-ITEM"],
  );
  assert.deepEqual(
    deterministicSort([...items].reverse()).map((item) => item.item_id),
    ["A-ITEM", "Z-ITEM"],
  );
});

// ── Wording generation and verification ───────────────────────────────────────

test("validateGeneratedWording rejects missing text", () => {
  const result = validateGeneratedWording({}, []);
  assert.equal(result.valid, false);
  assert.equal(result.fallback_to_source, true);
  assert.equal(result.reason, "MISSING_GENERATED_TEXT");
});

test("validateGeneratedWording rejects unbound evidence references", () => {
  const generated = {
    text: "Some wording",
    evidence_ids: ["EV-999"],
    producer_run_id: "PROD-001",
  };
  const bound = [{ evidence_id: "EV-001" }];
  const result = validateGeneratedWording(generated, bound);
  assert.equal(result.valid, false);
  assert.equal(result.reason, "UNBOUND_EVIDENCE_REFERENCE");
});

test("validateGeneratedWording accepts valid wording with bound evidence", () => {
  const generated = {
    text: "Councillor raised the incentive gap issue.",
    evidence_ids: ["EV-001"],
    producer_run_id: "PROD-001",
  };
  const bound = [{ evidence_id: "EV-001" }];
  const result = validateGeneratedWording(generated, bound);
  assert.equal(result.valid, true);
  assert.equal(result.fallback_to_source, false);
});

test("verifySentence rejects when producer and verifier are the same", () => {
  const sentence = {
    text: "Test",
    evidence_ids: ["EV-001"],
    producer_run_id: "SAME",
    verifier_run_id: "SAME",
  };
  const result = verifySentence(sentence, [{ evidence_id: "EV-001", locator: "page:1" }]);
  assert.equal(result.verified, false);
  assert.equal(result.reason, "PRODUCER_VERIFIER_NOT_INDEPENDENT");
});

test("verifySentence rejects when evidence is not found", () => {
  const sentence = {
    text: "Test",
    evidence_ids: ["EV-MISSING"],
    producer_run_id: "PROD-001",
    verifier_run_id: "VERI-001",
  };
  const result = verifySentence(sentence, [{ evidence_id: "EV-001", locator: "page:1" }]);
  assert.equal(result.verified, false);
  assert.equal(result.reason, "EVIDENCE_NOT_FOUND");
});

test("verifySentence rejects when locator is missing on bound evidence", () => {
  const sentence = {
    text: "Test",
    evidence_ids: ["EV-001"],
    producer_run_id: "PROD-001",
    verifier_run_id: "VERI-001",
  };
  const result = verifySentence(sentence, [{ evidence_id: "EV-001" }]);
  assert.equal(result.verified, false);
  assert.equal(result.reason, "MISSING_LOCATOR");
});

test("verifySentence rejects a sentence without explicit verifier attestation", () => {
  const sentence = {
    text: "Arbitrary wording",
    evidence_ids: ["EV-001"],
    producer_run_id: "PROD-001",
    verifier_run_id: "VERI-001",
  };
  const result = verifySentence(sentence, [{ evidence_id: "EV-001", locator: "page:1" }]);
  assert.equal(result.verified, false);
  assert.equal(result.reason, "MISSING_VERIFIER_ATTESTATION");
});

test("verifySentence rejects an attestation that checked different evidence IDs", () => {
  const sentence = {
    text: "Arbitrary wording",
    evidence_ids: ["EV-001"],
    producer_run_id: "PROD-001",
    verifier_run_id: "VERI-001",
    verifier_attestation: {
      verifier_run_id: "VERI-001",
      decision: "PASS",
      checked_text: "Arbitrary wording",
      checked_evidence_ids: ["EV-002"],
    },
  };
  const result = verifySentence(sentence, [{ evidence_id: "EV-001", locator: "page:1" }]);
  assert.equal(result.verified, false);
  assert.equal(result.reason, "VERIFIER_EVIDENCE_MISMATCH");
});

test("verifySentence rejects an attestation from a different verifier run", () => {
  const sentence = {
    text: "Arbitrary wording",
    evidence_ids: ["EV-001"],
    producer_run_id: "PROD-001",
    verifier_run_id: "VERI-001",
    verifier_attestation: {
      verifier_run_id: "VERI-OTHER",
      decision: "PASS",
      checked_text: "Arbitrary wording",
      checked_evidence_ids: ["EV-001"],
    },
  };
  const result = verifySentence(sentence, [{ evidence_id: "EV-001", locator: "page:1" }]);
  assert.equal(result.verified, false);
  assert.equal(result.reason, "VERIFIER_RUN_MISMATCH");
});

test("verifySentence rejects an attestation for different text", () => {
  const sentence = {
    text: "Changed after review",
    evidence_ids: ["EV-001"],
    producer_run_id: "PROD-001",
    verifier_run_id: "VERI-001",
    verifier_attestation: {
      verifier_run_id: "VERI-001",
      decision: "PASS",
      checked_text: "Original reviewed text",
      checked_evidence_ids: ["EV-001"],
    },
  };
  const result = verifySentence(sentence, [{ evidence_id: "EV-001", locator: "page:1" }]);
  assert.equal(result.verified, false);
  assert.equal(result.reason, "VERIFIER_TEXT_MISMATCH");
});

test("verifySentence accepts valid independently verified sentence", () => {
  const sentence = {
    text: "Incentive gap is real.",
    evidence_ids: ["EV-001"],
    producer_run_id: "PROD-001",
    verifier_run_id: "VERI-001",
    verifier_attestation: {
      verifier_run_id: "VERI-001",
      decision: "PASS",
      checked_text: "Incentive gap is real.",
      checked_evidence_ids: ["EV-001"],
    },
  };
  const evidence = [{ evidence_id: "EV-001", locator: "timestamp:1080-1380" }];
  const result = verifySentence(sentence, evidence);
  assert.equal(result.verified, true);
  assert.equal(result.status, "AUTO_PASS");
});

test("production page builds formal cards through the homepage eligibility gate", async () => {
  const pageSource = await readFile(new URL("../app/page.js", import.meta.url), "utf8");
  assert.match(pageSource, /buildHomepageResponse\(\[PRIORITY_ITEM\]\)/);
  assert.match(pageSource, /FALLBACK_RESPONSE\.items/);
  assert.match(pageSource, /priorityItem &&/);
  assert.match(pageSource, /projectFeedToHomepageCandidates/);
  assert.match(pageSource, /intelligence-feed\.json/);
  // D: priority-card only shown BEFORE feed loads
  assert.match(pageSource, /!feedLoaded && priorityItem/);
  // D: feed-empty-state has evidence-demo-fallback, not priority card
  assert.match(pageSource, /evidence-demo-fallback/);
  assert.match(pageSource, /not live/i);
  // Live feed response does NOT merge PRIORITY_ITEM into candidates
  assert.match(pageSource, /buildHomepageResponse\(candidates\)/);
  assert.doesNotMatch(pageSource, /\[\.\.\.candidates,\s*PRIORITY_ITEM\]/);
});

test("homepage data derives PRIORITY_ITEM from the council fixture contract", async () => {
  const dataSource = await readFile(new URL("../lib/homepage-data.js", import.meta.url), "utf8");
  assert.match(dataSource, /from ["']\.\/council-prep\.js["']/);
  assert.match(dataSource, /COUNCIL_FIXTURE\.historical_question/);
  assert.match(dataSource, /COUNCIL_FIXTURE\.meeting_record/);
  assert.match(dataSource, /COUNCIL_EVIDENCE_CHAIN\.map/);
});

test("PRIORITY_ITEM is eligible only through the formal homepage contract", () => {
  const response = buildHomepageResponse([PRIORITY_ITEM]);
  assert.equal(response.items.length, 1);
  assert.equal(response.items[0].verification_status, "AUTO_PASS");
  assert.equal(response.items[0].content_disposition, "HOME_CANDIDATE");
  assert.ok(response.items[0].evidence_ids.length > 0);
  assert.ok(response.items[0].official_url.startsWith("https://"));
  assert.ok(response.items[0].reason_codes.length > 0);
});
