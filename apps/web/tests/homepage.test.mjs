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
