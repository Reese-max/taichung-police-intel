// apps/web/lib/council-prep.js
// Council preparation response contract, evidence-type validation,
// GAP-001 policy enforcement, producer/verifier separation, and degraded states.
//
// Reuses the existing evidence drawer; does not rewrite it.
// The council-preparation response connects an agenda item to historical
// questions, oral answers, proposals, and project reports with evidence locators.

// ── Evidence types ────────────────────────────────────────────────────────────
export const EVIDENCE_TYPES = Object.freeze([
  "ORAL_OFFICIAL",
  "WRITTEN_OFFICIAL",
  "RESOLUTION",
  "GROQ_ASR",
  "AI_SYNTHESIS",
]);

// ── Verification statuses ─────────────────────────────────────────────────────
export const VERIFICATION_STATUSES = Object.freeze([
  "AUTO_PASS",
  "AUTO_RETRY",
  "AI_DISAGREEMENT",
  "CLAIM_REJECTED",
  "QUARANTINED",
]);

// ── Degraded states for media/ASR/verifier ────────────────────────────────────
export const DEGRADED_STATES = Object.freeze([
  "TRANSCRIPT_PENDING",
  "TRANSCRIPT_DISPUTED",
  "VALIDATION_PENDING",
  "AI_DISAGREEMENT",
  "QUARANTINED",
  "CLAIM_REJECTED",
]);

// ── Required labels ───────────────────────────────────────────────────────────
export const REQUIRED_LABELS = Object.freeze({
  ORAL_OFFICIAL: "ORAL_OFFICIAL",
  WRITTEN_OFFICIAL: "WRITTEN_OFFICIAL",
  RESOLUTION: "RESOLUTION",
  GROQ_ASR: "GROQ_ASR",
  UNVERIFIED_AFTER_MEETING: "UNVERIFIED_AFTER_MEETING",
});

// ── GAP-001 policy ────────────────────────────────────────────────────────────
// No systematic official post-meeting implementation evidence exists.
// Any attempt to infer post-meeting progress must be rejected.
export const GAP_001 = Object.freeze({
  gap_id: "GAP-001",
  title: "Post-meeting implementation status unknown",
  status: "UNVERIFIED_AFTER_MEETING",
  detail: "No systematic official post-meeting response or implementation source exists for this council question.",
});

/**
 * Validates that a council-preparation evidence record has a supported evidence type.
 * @param {object} evidence
 * @returns {boolean}
 */
export function isValidEvidenceType(evidence) {
  return EVIDENCE_TYPES.includes(evidence?.evidence_type);
}

/**
 * Validates a locator is present and non-empty for a given evidence record.
 * Locators can be a page number, paragraph index, timestamp, or ASR segment index.
 * @param {object} evidence
 * @returns {boolean}
 */
export function hasValidLocator(evidence) {
  if (!evidence) return false;
  const { locator } = evidence;
  if (locator === null || locator === undefined || locator === "") return false;
  return true;
}

/**
 * Validates a complete evidence record for council preparation.
 * Rejects records with missing evidence_type, evidence_id, locator, or official_url.
 * @param {object} evidence
 * @returns {{ valid: boolean, reason?: string }}
 */
export function validateCouncilEvidence(evidence) {
  if (!evidence || typeof evidence !== "object") {
    return { valid: false, reason: "MISSING_EVIDENCE" };
  }
  if (!evidence.evidence_id) {
    return { valid: false, reason: "MISSING_EVIDENCE_ID" };
  }
  if (!isValidEvidenceType(evidence)) {
    return { valid: false, reason: "INVALID_EVIDENCE_TYPE" };
  }
  if (!hasValidLocator(evidence)) {
    return { valid: false, reason: "MISSING_LOCATOR" };
  }
  if (!evidence.official_url || !evidence.official_url.startsWith("https://")) {
    return { valid: false, reason: "MISSING_OFFICIAL_URL" };
  }
  return { valid: true };
}

/**
 * Enforces GAP-001 policy: rejects any claim that infers post-meeting
 * implementation progress from model knowledge alone.
 * @param {object} claim
 * @returns {{ allowed: boolean, status?: string, gap?: object }}
 */
export function enforceGap001(claim) {
  if (!claim) return { allowed: false, status: "CLAIM_REJECTED" };

  // If the claim attempts to fill GAP-001 (post-meeting follow-up)
  if (claim.infers_post_meeting_progress === true || claim.fills_gap === "GAP-001") {
    return {
      allowed: false,
      status: "CLAIM_REJECTED",
      gap: GAP_001,
    };
  }
  // If no post-meeting evidence source is bound
  if (claim.post_meeting_evidence_source === null || claim.post_meeting_evidence_source === undefined) {
    return {
      allowed: true,
      status: "UNVERIFIED_AFTER_MEETING",
      gap: GAP_001,
    };
  }
  return { allowed: true };
}

/**
 * Validates producer/verifier separation for a council-preparation claim.
 * Producer and verifier must have distinct run IDs.
 * @param {object} claim
 * @returns {{ valid: boolean, reason?: string }}
 */
export function validateProducerVerifierSeparation(claim) {
  if (!claim) return { valid: false, reason: "MISSING_CLAIM" };
  if (!claim.producer_run_id) return { valid: false, reason: "MISSING_PRODUCER_RUN_ID" };
  if (!claim.verifier_run_id) return { valid: false, reason: "MISSING_VERIFIER_RUN_ID" };
  if (claim.producer_run_id === claim.verifier_run_id) {
    return { valid: false, reason: "PRODUCER_VERIFIER_NOT_INDEPENDENT" };
  }
  return { valid: true };
}

/**
 * Determines the verification state transition with one-retry policy.
 * A second disagreement becomes QUARANTINED.
 * @param {string|null} currentStatus
 * @param {string} event - PASS, DISAGREE, or REJECT
 * @returns {string}
 */
export function transitionVerification(currentStatus, event) {
  if (event === "PASS") return "AUTO_PASS";
  if (event === "REJECT") return "CLAIM_REJECTED";
  // DISAGREE path
  if (currentStatus === null || currentStatus === "AUTO_PASS") {
    return "AI_DISAGREEMENT";
  }
  if (currentStatus === "AI_DISAGREEMENT" || currentStatus === "AUTO_RETRY") {
    return "QUARANTINED";
  }
  return "QUARANTINED";
}

/**
 * Determines whether a derived claim should be suppressed due to degraded state.
 * Official links and verified text remain reachable; disputed derived text is hidden.
 * @param {object} claim
 * @returns {{ show_derived: boolean, show_official: boolean, degraded_reason?: string }}
 */
export function resolveDegradedState(claim) {
  if (!claim) return { show_derived: false, show_official: false, degraded_reason: "MISSING_CLAIM" };

  const status = claim.verification_status;
  // Official links always remain reachable
  const show_official = true;

  if (DEGRADED_STATES.includes(status)) {
    return { show_derived: false, show_official, degraded_reason: status };
  }
  if (claim.media_status === "FAILED" || claim.asr_status === "FAILED") {
    return { show_derived: false, show_official, degraded_reason: "MEDIA_OR_ASR_FAILED" };
  }
  if (status === "AUTO_PASS") {
    return { show_derived: true, show_official };
  }
  // Default: suppress derived, keep official
  return { show_derived: false, show_official, degraded_reason: status || "UNKNOWN" };
}

// ── Council preparation fixture ───────────────────────────────────────────────
// Fixed end-to-end fixture: agenda -> historical oral answer -> project report.
// Every displayed fact has an evidence ID and official locator.
export const COUNCIL_FIXTURE = Object.freeze({
  agenda_item: {
    source_id: "S-006",
    session_date: "2026-04-27",
    title: "第4屆第7次定期會業務質詢：警消環衛部分",
    questioning_order_url: "https://www.tccc.gov.tw/wb_download13.asp?uno=&cno=50",
  },
  historical_question: {
    evidence_id: "EV-S010-20260427-Q001",
    evidence_type: "ORAL_OFFICIAL",
    source_id: "S-010",
    official_url: "https://vod.tccc.gov.tw/wb_news02.asp?url=92&ano=14170&pageno=1",
    locator: "timestamp:1080-1380",
    summary: "Councillor questioned incentive gap between officers and civilian tip-off reporters for apprehending unaccounted-for migrant workers.",
    content_label: "ORAL_OFFICIAL",
    clip_start_seconds: 1080,
    clip_duration_seconds: 300,
  },
  oral_answer: {
    evidence_id: "EV-S010-20260427-A001",
    evidence_type: "ORAL_OFFICIAL",
    source_id: "S-010",
    official_url: "https://vod.tccc.gov.tw/wb_news02.asp?url=92&ano=14170&pageno=1",
    locator: "timestamp:1335-1365",
    summary: "Chief answered: 'Will handle after reviewing the relevant regulations.'",
    content_label: "ORAL_OFFICIAL",
  },
  project_report: {
    evidence_id: "EV-S029-20260724-R001",
    evidence_type: "WRITTEN_OFFICIAL",
    source_id: "S-029",
    official_url: "https://www.rdec.taichung.gov.tw/12047/12142/12145",
    locator: "page:1",
    summary: "City government council project report listing police-related sessions.",
    content_label: "WRITTEN_OFFICIAL",
  },
  meeting_record: {
    evidence_id: "EV-S007-20260427-MR001",
    evidence_type: "WRITTEN_OFFICIAL",
    source_id: "S-007",
    official_url: "https://yishi.tccc.gov.tw/meeting-records/292a8e4a-0e1e-4429-8889-72bc56bc895d",
    locator: "record:292a8e4a",
    summary: "Official meeting record of the fire-police-sanitation business Q&A session.",
    content_label: "WRITTEN_OFFICIAL",
  },
  derived_transcript: {
    evidence_id: "EV-S010-20260427-ASR001",
    evidence_type: "GROQ_ASR",
    source_id: "S-010",
    official_url: "https://vod.tccc.gov.tw/wb_news02.asp?url=92&ano=14170&pageno=1",
    locator: "asr_segment:0-85",
    summary: "Groq whisper-large-v3 transcript: 86 segments, 1036 word timestamps. Navigation only.",
    content_label: "GROQ_ASR",
    derivation_note: "Navigation text only; formal citation returns to official media.",
  },
  gap_001: GAP_001,
  // Producer/verifier record for this fixture
  validation: {
    producer_run_id: "PROD-FIXTURE-20260814-001",
    verifier_run_id: "VERI-FIXTURE-20260814-001",
    verification_status: "AUTO_PASS",
    post_meeting_label: "UNVERIFIED_AFTER_MEETING",
  },
});

/**
 * Returns all evidence records from the council fixture.
 * @returns {object[]}
 */
export function getFixtureEvidenceChain() {
  return [
    COUNCIL_FIXTURE.historical_question,
    COUNCIL_FIXTURE.oral_answer,
    COUNCIL_FIXTURE.project_report,
    COUNCIL_FIXTURE.meeting_record,
    COUNCIL_FIXTURE.derived_transcript,
  ];
}

/**
 * Validates the full fixture chain: every evidence has an ID and official locator.
 * @returns {{ valid: boolean, errors: string[] }}
 */
export function validateFixtureChain() {
  const errors = [];
  const chain = getFixtureEvidenceChain();
  if (!COUNCIL_FIXTURE.agenda_item.source_id) {
    errors.push("agenda: MISSING_SOURCE_ID");
  }
  if (!COUNCIL_FIXTURE.agenda_item.session_date) {
    errors.push("agenda: MISSING_SESSION_DATE");
  }
  if (!COUNCIL_FIXTURE.agenda_item.questioning_order_url?.startsWith("https://")) {
    errors.push("agenda: MISSING_QUESTIONING_ORDER_URL");
  }
  for (const evidence of chain) {
    const result = validateCouncilEvidence(evidence);
    if (!result.valid) {
      errors.push(`${evidence.evidence_id || "UNKNOWN"}: ${result.reason}`);
    }
  }
  // Validate producer/verifier separation
  const pvResult = validateProducerVerifierSeparation(COUNCIL_FIXTURE.validation);
  if (!pvResult.valid) {
    errors.push(`validation: ${pvResult.reason}`);
  }
  if (COUNCIL_FIXTURE.validation.verification_status !== "AUTO_PASS") {
    errors.push("validation: FIXTURE_NOT_AUTO_PASS");
  }
  if (COUNCIL_FIXTURE.validation.post_meeting_label !== REQUIRED_LABELS.UNVERIFIED_AFTER_MEETING) {
    errors.push("validation: MISSING_POST_MEETING_LABEL");
  }
  return { valid: errors.length === 0, errors };
}

/**
 * Binds the browser evidence payload to the same council fixture used by the
 * homepage card. The payload still supplies the existing ASR/media data; the
 * fixture supplies the authoritative identity, URL, and clip bounds.
 * @param {object} payload
 * @returns {{ valid: boolean, reason?: string, errors?: string[], evidence_ids?: string[] }}
 */
export function validateCouncilDrawerPayload(payload) {
  const fixtureResult = validateFixtureChain();
  if (!fixtureResult.valid) {
    return { valid: false, reason: "INVALID_COUNCIL_FIXTURE", errors: fixtureResult.errors };
  }
  if (!payload || payload.status !== "PASS") {
    return { valid: false, reason: "EVIDENCE_NOT_PASS" };
  }

  const source = payload.source;
  const expected = COUNCIL_FIXTURE.historical_question;
  if (!source || source.title !== COUNCIL_FIXTURE.agenda_item.title) {
    return { valid: false, reason: "SOURCE_TITLE_MISMATCH" };
  }
  if (source.official_page_url !== expected.official_url) {
    return { valid: false, reason: "OFFICIAL_URL_MISMATCH" };
  }
  if (Number(source.clip_start_seconds) !== Number(expected.clip_start_seconds)) {
    return { valid: false, reason: "CLIP_START_MISMATCH" };
  }
  if (Number(source.clip_duration_seconds) !== Number(expected.clip_duration_seconds)) {
    return { valid: false, reason: "CLIP_DURATION_MISMATCH" };
  }

  return {
    valid: true,
    evidence_ids: getFixtureEvidenceChain().map(({ evidence_id }) => evidence_id),
  };
}
