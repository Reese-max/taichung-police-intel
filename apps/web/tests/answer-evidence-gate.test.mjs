import assert from "node:assert/strict";
import test from "node:test";

import {
  CLAIM_SCHEMA_VERSION,
  CLAIM_TYPES,
  EVIDENCE_SCHEMA_VERSION,
  GATE_STATUSES,
  OFFICIAL_EVIDENCE_TYPES,
  RECEIPT_SCHEMA_VERSION,
  SUPPORT_STATUSES,
  TEMPORAL_SCOPES,
  VALIDATOR_VERSION,
  gateAnswer,
  publicationHash,
} from "../lib/answer-evidence-gate.js";

// ── Fixtures ──────────────────────────────────────────────────────────────────
// Regression scenario 1 baseline: official written evidence only proves the
// 17:00 -> 16:00 traffic-control time change; nothing proves the rain cause.

const TRAFFIC_TIME = {
  schema_version: EVIDENCE_SCHEMA_VERSION,
  evidence_id: "EV-S009-TRA-1600",
  evidence_type: "WRITTEN_OFFICIAL",
  source_id: "S-009",
  locator: "paragraph:2",
  official_url: "https://example.gov.tw/traffic/notice-1",
  document_version: 4,
  is_current: true,
  published_at: "2026-09-10T09:00:00+08:00",
  assertions: [{ subject: "交通管制開始時間", value: "16:00" }],
};

const TRAFFIC_TIME_OTHER_SOURCE = {
  schema_version: EVIDENCE_SCHEMA_VERSION,
  evidence_id: "EV-S014-TRA-1700",
  evidence_type: "ORAL_OFFICIAL",
  source_id: "S-014",
  locator: "timestamp:640-660",
  official_url: "https://example.gov.tw/council/video-7",
  document_version: 1,
  is_current: true,
  published_at: "2026-09-10T10:00:00+08:00",
  assertions: [{ subject: "交通管制開始時間", value: "17:00" }],
};

const MEDIA_ONLY = {
  schema_version: EVIDENCE_SCHEMA_VERSION,
  evidence_id: "EV-ASR-001",
  evidence_type: "GROQ_ASR",
  source_id: "S-010",
  locator: "asr_segment:12-14",
  official_url: "https://example.gov.tw/council/video-7",
  document_version: 1,
  is_current: true,
  assertions: [{ subject: "交通管制開始時間", value: "16:00" }],
};

const STALE_STATISTIC = {
  schema_version: EVIDENCE_SCHEMA_VERSION,
  evidence_id: "EV-S026-STAT-08",
  evidence_type: "WRITTEN_OFFICIAL",
  source_id: "S-026",
  locator: "page:3",
  official_url: "https://example.gov.tw/stats/monthly",
  document_version: 7,
  is_current: false,
  published_at: "2026-08-31T09:00:00+08:00",
  assertions: [{ subject: "交通違規舉發件數", value: "120" }],
};

function timeClaim(overrides = {}) {
  return {
    schema_version: CLAIM_SCHEMA_VERSION,
    text: "交通管制提前至 16:00 開始",
    claim_type: "TIME",
    temporal_scope: "CURRENT",
    proposition: { subject: "交通管制開始時間", value: "16:00" },
    ...overrides,
  };
}

// ── Schema / contract surface ─────────────────────────────────────────────────

test("exports a versioned claim/evidence/receipt schema", () => {
  assert.equal(CLAIM_SCHEMA_VERSION, 1);
  assert.equal(EVIDENCE_SCHEMA_VERSION, 1);
  assert.equal(RECEIPT_SCHEMA_VERSION, 1);
  assert.match(VALIDATOR_VERSION, /^answer-evidence-gate\//);
});

test("claim types cover time/location/status/cause/statistic/agency", () => {
  for (const type of ["TIME", "LOCATION", "STATUS", "CAUSE", "STATISTIC", "AGENCY"]) {
    assert.ok(CLAIM_TYPES.includes(type), `missing ${type}`);
  }
  assert.ok(CLAIM_TYPES.includes("OTHER"));
});

test("support statuses match the spec enum", () => {
  assert.deepEqual([...SUPPORT_STATUSES].sort(), ["CONFLICT", "PARTIAL", "STALE", "SUPPORTED", "UNSUPPORTED"]);
});

test("gate statuses are PASS, QUALIFIED, BLOCKED", () => {
  assert.deepEqual([...GATE_STATUSES].sort(), ["BLOCKED", "PASS", "QUALIFIED"]);
});

test("official evidence types exclude derived/media records", () => {
  for (const type of OFFICIAL_EVIDENCE_TYPES) {
    assert.ok(["ORAL_OFFICIAL", "WRITTEN_OFFICIAL", "RESOLUTION"].includes(type));
  }
  assert.ok(!OFFICIAL_EVIDENCE_TYPES.includes("GROQ_ASR"));
  assert.ok(!OFFICIAL_EVIDENCE_TYPES.includes("AI_SYNTHESIS"));
});

// ── SUPPORTED path ────────────────────────────────────────────────────────────

test("claim backed by official current evidence is SUPPORTED and admitted", () => {
  const result = gateAnswer({ claims: [timeClaim()], evidence: [TRAFFIC_TIME] });
  assert.equal(result.gate_status, "PASS");
  assert.equal(result.final_claims.length, 1);
  assert.equal(result.final_claims[0].support_status, "SUPPORTED");
  assert.equal(result.final_claims[0].text, "交通管制提前至 16:00 開始");
  assert.equal(result.removed_claims.length, 0);
});

test("supported claim receipt carries exact locator and document version", () => {
  const result = gateAnswer({ claims: [timeClaim()], evidence: [TRAFFIC_TIME] });
  const receipt = result.receipt.claims[0];
  assert.equal(receipt.support_status, "SUPPORTED");
  const supporter = receipt.supporting_evidence[0];
  assert.equal(supporter.evidence_id, "EV-S009-TRA-1600");
  assert.equal(supporter.locator, "paragraph:2");
  assert.equal(supporter.document_version, 4);
  assert.equal(supporter.source_id, "S-009");
});

test("every admitted claim exposes a stable claim_id", () => {
  const result = gateAnswer({ claims: [timeClaim()], evidence: [TRAFFIC_TIME] });
  assert.match(result.final_claims[0].claim_id, /^CLM-[0-9A-F]{16}$/);
  assert.deepEqual(result.receipt.claim_ids, [result.final_claims[0].claim_id]);
});

// ── Regression 1: unsupported causal claim is removed or qualified ────────────

test("time claim supported while unsupported cause is removed with the fixed phrase", () => {
  const causeClaim = {
    schema_version: CLAIM_SCHEMA_VERSION,
    text: "因豪雨提前一小時管制",
    claim_type: "CAUSE",
    temporal_scope: "CURRENT",
    proposition: { subject: "交通管制提前原因", value: "豪雨" },
  };
  const result = gateAnswer({ claims: [timeClaim(), causeClaim], evidence: [TRAFFIC_TIME] });
  assert.equal(result.gate_status, "QUALIFIED");
  assert.equal(result.final_claims.length, 1);
  assert.equal(result.final_claims[0].support_status, "SUPPORTED");
  assert.equal(result.removed_claims.length, 1);
  assert.equal(result.removed_claims[0].support_status, "UNSUPPORTED");
  assert.equal(result.removed_claims[0].qualified_text, "官方來源未說明原因");
});

// ── Regression 2: conflicting official sources produce CONFLICT ───────────────

test("two current official sources with different values produce CONFLICT", () => {
  const result = gateAnswer({ claims: [timeClaim()], evidence: [TRAFFIC_TIME, TRAFFIC_TIME_OTHER_SOURCE] });
  assert.equal(result.gate_status, "QUALIFIED");
  const receipt = result.receipt.claims[0];
  assert.equal(receipt.support_status, "CONFLICT");
  const values = receipt.conflict_values.map((entry) => entry.value);
  assert.deepEqual(values.sort(), ["16:00", "17:00"]);
});

test("CONFLICT claims are not collapsed into a single definitive answer", () => {
  const result = gateAnswer({ claims: [timeClaim()], evidence: [TRAFFIC_TIME, TRAFFIC_TIME_OTHER_SOURCE] });
  const claim = result.final_claims[0];
  assert.equal(claim.support_status, "CONFLICT");
  assert.match(claim.text, /不一致/);
  assert.match(claim.text, /16:00/);
  assert.match(claim.text, /17:00/);
  assert.match(claim.text, /S-009/);
  assert.match(claim.text, /S-014/);
  assert.notEqual(claim.text, "交通管制提前至 16:00 開始");
});

// ── Regression 3+4: stale evidence cannot back current wording ────────────────

test("stale evidence cannot support a CURRENT claim", () => {
  const statClaim = {
    schema_version: CLAIM_SCHEMA_VERSION,
    text: "本月交通違規舉發件數為 120 件",
    claim_type: "STATISTIC",
    temporal_scope: "CURRENT",
    proposition: { subject: "交通違規舉發件數", value: "120" },
  };
  const result = gateAnswer({ claims: [statClaim], evidence: [STALE_STATISTIC] });
  assert.equal(result.gate_status, "QUALIFIED");
  const claim = result.final_claims[0];
  assert.equal(claim.support_status, "STALE");
  assert.match(claim.text, /2026-08-31/);
  assert.match(claim.text, /過期|尚無更新/);
});

test("current phrasing in text is treated as a current assertion even if scope says HISTORICAL", () => {
  const sneaky = {
    schema_version: CLAIM_SCHEMA_VERSION,
    text: "目前最新統計為 120 件",
    claim_type: "STATISTIC",
    temporal_scope: "HISTORICAL",
    proposition: { subject: "交通違規舉發件數", value: "120" },
  };
  const result = gateAnswer({ claims: [sneaky], evidence: [STALE_STATISTIC] });
  assert.equal(result.final_claims[0].support_status, "STALE");
});

test("stale evidence may support an explicitly HISTORICAL claim", () => {
  const historical = {
    schema_version: CLAIM_SCHEMA_VERSION,
    text: "2026-08 月交通違規舉發件數為 120 件",
    claim_type: "STATISTIC",
    temporal_scope: "HISTORICAL",
    proposition: { subject: "交通違規舉發件數", value: "120" },
  };
  const result = gateAnswer({ claims: [historical], evidence: [STALE_STATISTIC] });
  assert.equal(result.gate_status, "PASS");
  assert.equal(result.final_claims[0].support_status, "SUPPORTED");
  assert.equal(result.final_claims[0].text, "2026-08 月交通違規舉發件數為 120 件");
});

test("stale evidence disagreeing with the claim value is UNSUPPORTED", () => {
  const wrongValue = {
    schema_version: CLAIM_SCHEMA_VERSION,
    text: "上月交通違規舉發件數為 99 件",
    claim_type: "STATISTIC",
    temporal_scope: "HISTORICAL",
    proposition: { subject: "交通違規舉發件數", value: "99" },
  };
  const result = gateAnswer({ claims: [wrongValue], evidence: [STALE_STATISTIC] });
  assert.equal(result.removed_claims[0].support_status, "UNSUPPORTED");
  assert.equal(result.removed_claims[0].reason_code, "VALUE_MISMATCH");
  assert.deepEqual(result.receipt.claims[0].official_values, ["120"]);
});

// ── Superseded: stale-matched value contradicted by newer official data ───────

const CURRENT_STATISTIC_NEW = {
  schema_version: EVIDENCE_SCHEMA_VERSION,
  evidence_id: "EV-S026-STAT-09",
  evidence_type: "WRITTEN_OFFICIAL",
  source_id: "S-026",
  locator: "page:1",
  official_url: "https://example.gov.tw/stats/monthly",
  document_version: 8,
  is_current: true,
  published_at: "2026-09-15T09:00:00+08:00",
  assertions: [{ subject: "交通違規舉發件數", value: "130" }],
};

function currentStatClaim(overrides = {}) {
  return {
    schema_version: CLAIM_SCHEMA_VERSION,
    text: "本月交通違規舉發件數為 120 件",
    claim_type: "STATISTIC",
    temporal_scope: "CURRENT",
    proposition: { subject: "交通違規舉發件數", value: "120" },
    ...overrides,
  };
}

test("stale-matched value contradicted by current official data is UNSUPPORTED, not STALE", () => {
  const result = gateAnswer({
    claims: [currentStatClaim()],
    evidence: [STALE_STATISTIC, CURRENT_STATISTIC_NEW],
  });
  const removed = result.removed_claims[0];
  assert.equal(removed.support_status, "UNSUPPORTED");
  assert.equal(removed.reason_code, "SUPERSEDED_BY_CURRENT");
  assert.match(removed.qualified_text, /130/);
  assert.doesNotMatch(removed.qualified_text, /尚無更新/);
  assert.deepEqual(result.receipt.claims[0].official_values, ["130"]);
});

test("citing stale evidence while newer official data disagrees is still superseded", () => {
  const claim = currentStatClaim({ cited_evidence_ids: ["EV-S026-STAT-08"] });
  const result = gateAnswer({
    claims: [claim],
    evidence: [STALE_STATISTIC, CURRENT_STATISTIC_NEW],
  });
  assert.equal(result.removed_claims[0].support_status, "UNSUPPORTED");
  assert.equal(result.removed_claims[0].reason_code, "SUPERSEDED_BY_CURRENT");
});

test("cited stale evidence stays STALE when current official data records the same value", () => {
  const currentSame = {
    ...CURRENT_STATISTIC_NEW,
    evidence_id: "EV-S026-STAT-09B",
    assertions: [{ subject: "交通違規舉發件數", value: "120" }],
  };
  const claim = currentStatClaim({ cited_evidence_ids: ["EV-S026-STAT-08"] });
  const result = gateAnswer({ claims: [claim], evidence: [STALE_STATISTIC, currentSame] });
  const admitted = result.final_claims[0];
  assert.equal(admitted.support_status, "STALE");
  assert.match(admitted.text, /較新的官方資料亦記載相同內容/);
  assert.doesNotMatch(admitted.text, /尚無更新的官方確認/);
});

test("HISTORICAL claims are not superseded by newer data", () => {
  const claim = currentStatClaim({
    text: "2026-08 月交通違規舉發件數為 120 件",
    temporal_scope: "HISTORICAL",
  });
  const result = gateAnswer({
    claims: [claim],
    evidence: [STALE_STATISTIC, CURRENT_STATISTIC_NEW],
  });
  assert.equal(result.final_claims[0].support_status, "SUPPORTED");
});

// ── Media discovery cannot support a verified claim ───────────────────────────

test("media-derived evidence alone leaves a claim UNSUPPORTED", () => {
  const result = gateAnswer({ claims: [timeClaim()], evidence: [MEDIA_ONLY] });
  assert.equal(result.gate_status, "QUALIFIED");
  assert.equal(result.final_claims.length, 0);
  assert.equal(result.removed_claims[0].support_status, "UNSUPPORTED");
  assert.equal(result.removed_claims[0].reason_code, "MEDIA_ONLY");
});

test("media evidence alongside official support does not block SUPPORTED", () => {
  const result = gateAnswer({ claims: [timeClaim()], evidence: [MEDIA_ONLY, TRAFFIC_TIME] });
  assert.equal(result.gate_status, "PASS");
  assert.equal(result.final_claims[0].support_status, "SUPPORTED");
});

// ── Citation handling ─────────────────────────────────────────────────────────

test("claims citing unknown evidence ids are UNSUPPORTED", () => {
  const claim = timeClaim({ cited_evidence_ids: ["EV-NOPE"] });
  const result = gateAnswer({ claims: [claim], evidence: [TRAFFIC_TIME] });
  assert.equal(result.removed_claims[0].support_status, "UNSUPPORTED");
  assert.equal(result.removed_claims[0].reason_code, "UNKNOWN_EVIDENCE");
});

test("cited evidence must actually assert the claim value", () => {
  const claim = timeClaim({ cited_evidence_ids: ["EV-S014-TRA-1700"] });
  const result = gateAnswer({ claims: [claim], evidence: [TRAFFIC_TIME_OTHER_SOURCE] });
  const receipt = result.receipt.claims[0];
  assert.equal(receipt.support_status, "UNSUPPORTED");
  assert.equal(receipt.reason_code, "VALUE_MISMATCH");
});

test("citing one side of a conflicting index still produces CONFLICT", () => {
  const claim = timeClaim({ cited_evidence_ids: ["EV-S009-TRA-1600"] });
  const result = gateAnswer({ claims: [claim], evidence: [TRAFFIC_TIME, TRAFFIC_TIME_OTHER_SOURCE] });
  const receipt = result.receipt.claims[0];
  assert.equal(receipt.support_status, "CONFLICT");
});

// ── Compound claims / PARTIAL ─────────────────────────────────────────────────

test("compound claim with one unproven proposition is PARTIAL and qualified", () => {
  const compound = {
    schema_version: CLAIM_SCHEMA_VERSION,
    text: "管制提前至 16:00 且範圍涵蓋中清路",
    claim_type: "TIME",
    temporal_scope: "CURRENT",
    proposition: [
      { subject: "交通管制開始時間", value: "16:00" },
      { subject: "交通管制範圍", value: "中清路" },
    ],
  };
  const result = gateAnswer({ claims: [compound], evidence: [TRAFFIC_TIME] });
  assert.equal(result.gate_status, "QUALIFIED");
  const claim = result.final_claims[0];
  assert.equal(claim.support_status, "PARTIAL");
  assert.match(claim.text, /交通管制範圍/);
});

// ── Receipt contract ──────────────────────────────────────────────────────────

test("receipt carries publication hash, claim ids, evidence ids, and validator version", () => {
  const result = gateAnswer({ claims: [timeClaim()], evidence: [TRAFFIC_TIME] });
  const receipt = result.receipt;
  assert.equal(receipt.schema_version, RECEIPT_SCHEMA_VERSION);
  assert.equal(receipt.validator_version, VALIDATOR_VERSION);
  assert.match(receipt.publication_hash, /^[0-9a-f]{64}$/);
  assert.equal(receipt.publication_hash, publicationHash([TRAFFIC_TIME]));
  assert.deepEqual(receipt.evidence_ids, ["EV-S009-TRA-1600"]);
  assert.deepEqual(
    Object.entries(receipt.source_document_versions),
    [["EV-S009-TRA-1600", 4]],
  );
  assert.equal(receipt.gate_status, "PASS");
});

test("gate output is deterministic for identical input", () => {
  const input = { claims: [timeClaim()], evidence: [TRAFFIC_TIME] };
  const first = gateAnswer(input);
  const second = gateAnswer(input);
  assert.deepEqual(first, second);
});

test("publication hash changes when the evidence set changes", () => {
  assert.notEqual(publicationHash([TRAFFIC_TIME]), publicationHash([TRAFFIC_TIME, MEDIA_ONLY]));
});

// ── Fail closed ───────────────────────────────────────────────────────────────

test("missing or malformed evidence bundle fails closed", () => {
  for (const evidence of [undefined, null, "not-an-array", 42]) {
    const result = gateAnswer({ claims: [timeClaim()], evidence });
    assert.equal(result.gate_status, "BLOCKED");
    assert.equal(result.final_claims.length, 0);
    assert.equal(result.receipt.gate_status, "BLOCKED");
  }
});

test("malformed claims are removed, not released", () => {
  const malformed = { schema_version: CLAIM_SCHEMA_VERSION, claim_type: "TIME" };
  const result = gateAnswer({ claims: [malformed, timeClaim()], evidence: [TRAFFIC_TIME] });
  assert.equal(result.removed_claims.length, 1);
  assert.equal(result.removed_claims[0].reason_code, "MISSING_TEXT");
  assert.equal(result.final_claims.length, 1);
});

test("claims with a foreign schema_version are rejected", () => {
  const claim = timeClaim({ schema_version: 99 });
  const result = gateAnswer({ claims: [claim], evidence: [TRAFFIC_TIME] });
  assert.equal(result.removed_claims[0].reason_code, "INVALID_SCHEMA_VERSION");
});

test("non-factual OTHER claims pass through without evidence", () => {
  const claim = {
    schema_version: CLAIM_SCHEMA_VERSION,
    text: "以上為本次官方資料整理",
    claim_type: "OTHER",
  };
  const result = gateAnswer({ claims: [claim], evidence: [] });
  assert.equal(result.gate_status, "PASS");
  assert.equal(result.final_claims[0].support_status, "SUPPORTED");
  assert.equal(result.receipt.claims[0].requires_evidence, false);
});

// ── Hardening regressions ─────────────────────────────────────────────────────

test("duplicate evidence_id in the bundle fails closed", () => {
  const duplicate = { ...TRAFFIC_TIME, locator: "page:9" };
  const result = gateAnswer({ claims: [timeClaim()], evidence: [TRAFFIC_TIME, duplicate] });
  assert.equal(result.gate_status, "BLOCKED");
  assert.equal(result.final_claims.length, 0);
  assert.equal(result.receipt.failure_reason, "DUPLICATE_EVIDENCE_ID");
});

test("non-array cited_evidence_ids is rejected instead of ignored", () => {
  const claim = timeClaim({ cited_evidence_ids: "EV-S009-TRA-1600" });
  const result = gateAnswer({ claims: [claim], evidence: [TRAFFIC_TIME] });
  assert.equal(result.removed_claims[0].support_status, "UNSUPPORTED");
  assert.equal(result.removed_claims[0].reason_code, "INVALID_CITATIONS");
});

test("object assertion values are dropped and never match by stringification", () => {
  const weird = {
    ...TRAFFIC_TIME,
    evidence_id: "EV-WEIRD",
    assertions: [{ subject: { nested: true }, value: { also: "object" } }],
  };
  const claim = timeClaim({ proposition: { subject: { nested: true }, value: { also: "object" } } });
  const result = gateAnswer({ claims: [claim], evidence: [weird] });
  assert.equal(result.removed_claims[0].reason_code, "INVALID_PROPOSITION");
  const validShape = timeClaim({ proposition: { subject: "交通管制開始時間", value: "16:00" } });
  const second = gateAnswer({ claims: [validShape], evidence: [weird] });
  assert.equal(second.removed_claims[0].reason_code, "NO_EVIDENCE");
});

test("non-string published_at on stale evidence does not crash the gate", () => {
  const numericDate = { ...STALE_STATISTIC, published_at: 20260831 };
  const statClaim = {
    schema_version: CLAIM_SCHEMA_VERSION,
    text: "目前交通違規舉發件數為 120 件",
    claim_type: "STATISTIC",
    temporal_scope: "CURRENT",
    proposition: { subject: "交通違規舉發件數", value: "120" },
  };
  const result = gateAnswer({ claims: [statClaim], evidence: [numericDate] });
  assert.equal(result.gate_status, "QUALIFIED");
  assert.equal(result.final_claims[0].support_status, "STALE");
});

test("compound claim mixing stale and unsupported propositions reports PARTIAL with both reasons", () => {
  const compound = {
    schema_version: CLAIM_SCHEMA_VERSION,
    text: "舉發件數 120 且罰鍰金額 900",
    claim_type: "STATISTIC",
    temporal_scope: "CURRENT",
    proposition: [
      { subject: "交通違規舉發件數", value: "120" },
      { subject: "罰鍰金額", value: "900" },
    ],
  };
  const result = gateAnswer({ claims: [compound], evidence: [STALE_STATISTIC] });
  const claim = result.final_claims[0];
  assert.equal(claim.support_status, "PARTIAL");
  assert.match(claim.text, /罰鍰金額/);
  assert.match(claim.text, /已過期/);
});

test("a __proto__ evidence_id cannot pollute the receipt prototype", () => {
  const proto = { ...TRAFFIC_TIME, evidence_id: "__proto__" };
  const result = gateAnswer({ claims: [timeClaim()], evidence: [proto] });
  assert.equal(result.gate_status, "PASS");
  assert.equal(result.receipt.source_document_versions["__proto__"], 4);
  assert.equal(Object.getPrototypeOf(result.receipt.source_document_versions), null);
  assert.doesNotThrow(() => JSON.stringify(result.receipt));
});

test("blank locator or document_version downgrades support to PARTIAL", () => {
  for (const record of [
    { ...TRAFFIC_TIME, evidence_id: "EV-BLANK-LOC", locator: "   " },
    { ...TRAFFIC_TIME, evidence_id: "EV-BLANK-VER", document_version: "" },
    { ...TRAFFIC_TIME, evidence_id: "EV-OBJ-VER", document_version: { v: 4 } },
  ]) {
    const result = gateAnswer({ claims: [timeClaim()], evidence: [record] });
    const claim = result.final_claims[0];
    assert.equal(claim.support_status, "PARTIAL", record.evidence_id);
    assert.equal(result.receipt.claims[0].reason_code, "MISSING_EXACT_LOCATOR");
  }
});

test("validator errors fail closed and keep a truncated error detail", () => {
  const throwing = { get text() { throw new Error("boom-internal-detail"); } };
  const result = gateAnswer({ claims: [throwing], evidence: [TRAFFIC_TIME] });
  assert.equal(result.gate_status, "BLOCKED");
  assert.equal(result.receipt.failure_reason, "VALIDATOR_ERROR");
  assert.equal(result.receipt.publication_hash, null);
  assert.match(result.receipt.error_detail, /boom-internal-detail/);
  assert.equal(result.final_claims.length, 0);
});
