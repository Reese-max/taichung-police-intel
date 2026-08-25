// apps/web/lib/homepage-eligibility.js
// Deterministic eligibility, ordering, and wording verification for the homepage.
// Imports shared constants from homepage-data.js (single source of truth).

import { CENTRAL_POLICY_LIMIT } from "./homepage-data.js";

// ── Constants ─────────────────────────────────────────────────────────────────
export const HOMEPAGE_ITEM_LIMIT = 10;

// Re-export for callers that need the limit without importing homepage-data directly
export { CENTRAL_POLICY_LIMIT };

// ── Reason codes (deterministic ordering) ─────────────────────────────────────
export const REASON_CODES = Object.freeze({
  RECURRING: "RECURRING",
  COUNCIL_ATTENTION: "COUNCIL_ATTENTION",
  CROSS_SOURCE: "CROSS_SOURCE",
  NEAR_MILESTONE: "NEAR_MILESTONE",
  HIGH_VALUE: "HIGH_VALUE",
  POLICY_CHANGE: "POLICY_CHANGE",
});
const VALID_REASON_CODES = new Set(Object.values(REASON_CODES));

// Priority order for deterministic sorting — identical across repeated runs
const REASON_PRIORITY = Object.freeze([
  "COUNCIL_ATTENTION",
  "RECURRING",
  "NEAR_MILESTONE",
  "CROSS_SOURCE",
  "POLICY_CHANGE",
  "HIGH_VALUE",
]);

// ── Eligible statuses ─────────────────────────────────────────────────────────
export const ELIGIBLE_VERIFICATION_STATUSES = Object.freeze(["AUTO_PASS"]);
export const ELIGIBLE_DISPOSITIONS = Object.freeze(["HOME_CANDIDATE"]);

/**
 * Determines if an item is eligible for the formal homepage.
 * Only AUTO_PASS + HOME_CANDIDATE items with evidence and official links qualify.
 * Non-AUTO_PASS, suppressed, pending, disputed, or quarantined items are rejected.
 * @param {object} item
 * @returns {{ eligible: boolean, reason?: string }}
 */
export function checkEligibility(item) {
  if (!item || typeof item !== "object") {
    return { eligible: false, reason: "MISSING_ITEM" };
  }
  if (!ELIGIBLE_VERIFICATION_STATUSES.includes(item.verification_status)) {
    return { eligible: false, reason: "NOT_AUTO_PASS" };
  }
  if (!ELIGIBLE_DISPOSITIONS.includes(item.content_disposition)) {
    return { eligible: false, reason: "NOT_HOME_CANDIDATE" };
  }
  const stableId = item.stable_id ?? item.item_id ?? item.evidence_id ?? item.evidence_source_id ?? item.id;
  if (typeof stableId !== "string" || !stableId.trim()) {
    return { eligible: false, reason: "MISSING_STABLE_ID" };
  }
  if (
    !Array.isArray(item.evidence_ids)
    || item.evidence_ids.length === 0
    || item.evidence_ids.some((id) => typeof id !== "string" || !id.trim())
  ) {
    return { eligible: false, reason: "NO_EVIDENCE" };
  }
  if (typeof item.official_url !== "string" || !item.official_url.startsWith("https://")) {
    return { eligible: false, reason: "NO_OFFICIAL_URL" };
  }
  if (
    !Array.isArray(item.reason_codes)
    || item.reason_codes.length === 0
    || item.reason_codes.some((code) => typeof code !== "string" || !code.trim())
  ) {
    return { eligible: false, reason: "NO_REASON_CODES" };
  }
  if (item.reason_codes.some((code) => !VALID_REASON_CODES.has(code))) {
    return { eligible: false, reason: "INVALID_REASON_CODE" };
  }
  return { eligible: true };
}

/**
 * Deterministic sort: items ordered by reason code priority, then score descending.
 * Identical across repeated runs for the same input.
 * @param {object[]} items
 * @returns {object[]}
 */
export function deterministicSort(items) {
  return [...items].sort((a, b) => {
    const aPriority = Math.min(
      ...(a.reason_codes || []).map((c) => REASON_PRIORITY.indexOf(c)).filter((i) => i >= 0),
      999,
    );
    const bPriority = Math.min(
      ...(b.reason_codes || []).map((c) => REASON_PRIORITY.indexOf(c)).filter((i) => i >= 0),
      999,
    );
    if (aPriority !== bPriority) return aPriority - bPriority;
    const scoreDifference = (b.item_value_score || 0) - (a.item_value_score || 0);
    if (scoreDifference !== 0) return scoreDifference;

    // Do not rely on the input order when every higher-order key ties. The
    // stable item identity is the final deterministic key.
    const aId = String(a.stable_id ?? a.item_id ?? a.evidence_id ?? a.evidence_source_id ?? a.id ?? "");
    const bId = String(b.stable_id ?? b.item_id ?? b.evidence_id ?? b.evidence_source_id ?? b.id ?? "");
    if (aId < bId) return -1;
    if (aId > bId) return 1;
    return 0;
  });
}

/**
 * Builds the typed homepage response from a candidate list.
 * Enforces: max 10 items, only AUTO_PASS + HOME_CANDIDATE, deterministic order.
 * @param {object[]} candidates
 * @returns {{ items: object[], rejected: object[], total_candidates: number }}
 */
export function buildHomepageResponse(candidates) {
  const eligible = [];
  const rejected = [];

  for (const item of candidates) {
    const result = checkEligibility(item);
    if (result.eligible) {
      eligible.push(item);
    } else {
      rejected.push({ ...item, rejection_reason: result.reason });
    }
  }

  const sorted = deterministicSort(eligible);
  const items = sorted.slice(0, HOMEPAGE_ITEM_LIMIT);

  return {
    items,
    rejected,
    total_candidates: candidates.length,
  };
}

// ── Wording generation validation ─────────────────────────────────────────────
// Generator may shorten verified claims but may NOT add facts, dates, causal
// explanations, or local impact absent from bound evidence.
// On failure, verified source text remains available (fallback).

/**
 * Validates generated wording against evidence bounds.
 * Rejects unsupported text; verified source text remains available on failure.
 * @param {object} generated - { text, evidence_ids, producer_run_id }
 * @param {object[]} boundEvidence - Evidence records bound to this item
 * @returns {{ valid: boolean, fallback_to_source: boolean, reason?: string }}
 */
export function validateGeneratedWording(generated, boundEvidence) {
  if (!generated || !generated.text) {
    return { valid: false, fallback_to_source: true, reason: "MISSING_GENERATED_TEXT" };
  }
  if (!generated.producer_run_id) {
    return { valid: false, fallback_to_source: true, reason: "MISSING_PRODUCER_RUN_ID" };
  }
  if (!generated.evidence_ids || !generated.evidence_ids.length) {
    return { valid: false, fallback_to_source: true, reason: "NO_BOUND_EVIDENCE" };
  }
  const boundIds = new Set(boundEvidence.map((e) => e.evidence_id));
  const unboundRefs = generated.evidence_ids.filter((id) => !boundIds.has(id));
  if (unboundRefs.length > 0) {
    return { valid: false, fallback_to_source: true, reason: "UNBOUND_EVIDENCE_REFERENCE" };
  }
  return { valid: true, fallback_to_source: false };
}

/**
 * Verifies a sentence independently against the original evidence.
 * The verifier must use a separate run ID from the producer.
 * @param {object} sentence - { text, evidence_ids, producer_run_id, verifier_run_id, verifier_attestation }
 * @param {object[]} originalEvidence - Evidence with evidence_id and locator
 * @returns {{ verified: boolean, status: string, reason?: string }}
 */
export function verifySentence(sentence, originalEvidence) {
  if (!sentence || !sentence.verifier_run_id) {
    return { verified: false, status: "CLAIM_REJECTED", reason: "MISSING_VERIFIER" };
  }
  if (!sentence.producer_run_id) {
    return { verified: false, status: "CLAIM_REJECTED", reason: "MISSING_PRODUCER" };
  }
  if (sentence.producer_run_id === sentence.verifier_run_id) {
    return { verified: false, status: "CLAIM_REJECTED", reason: "PRODUCER_VERIFIER_NOT_INDEPENDENT" };
  }
  if (!sentence.evidence_ids || !sentence.evidence_ids.length) {
    return { verified: false, status: "CLAIM_REJECTED", reason: "NO_EVIDENCE_BOUND" };
  }
  if (!sentence.text || typeof sentence.text !== "string" || !sentence.text.trim()) {
    return { verified: false, status: "CLAIM_REJECTED", reason: "MISSING_TEXT" };
  }

  if (!Array.isArray(originalEvidence)) {
    return { verified: false, status: "CLAIM_REJECTED", reason: "EVIDENCE_NOT_FOUND" };
  }
  const evidenceMap = new Set(originalEvidence.map((e) => e.evidence_id));
  const missing = sentence.evidence_ids.filter((id) => !evidenceMap.has(id));
  if (missing.length > 0) {
    return { verified: false, status: "CLAIM_REJECTED", reason: "EVIDENCE_NOT_FOUND" };
  }
  for (const id of sentence.evidence_ids) {
    const ev = originalEvidence.find((e) => e.evidence_id === id);
    if (!ev || !ev.locator) {
      return { verified: false, status: "CLAIM_REJECTED", reason: "MISSING_LOCATOR" };
    }
  }

  // A verifier run ID by itself is only metadata. AUTO_PASS requires an
  // explicit independent attestation that names every evidence ID actually
  // checked for this sentence.
  const attestation = sentence.verifier_attestation;
  if (!attestation || typeof attestation !== "object") {
    return { verified: false, status: "CLAIM_REJECTED", reason: "MISSING_VERIFIER_ATTESTATION" };
  }
  if (attestation.verifier_run_id !== sentence.verifier_run_id) {
    return { verified: false, status: "CLAIM_REJECTED", reason: "VERIFIER_RUN_MISMATCH" };
  }
  if (attestation.decision !== "PASS") {
    return { verified: false, status: "CLAIM_REJECTED", reason: "VERIFIER_ATTESTATION_REQUIRED" };
  }
  if (attestation.checked_text !== sentence.text) {
    return { verified: false, status: "CLAIM_REJECTED", reason: "VERIFIER_TEXT_MISMATCH" };
  }
  const checkedIds = attestation.checked_evidence_ids;
  if (!Array.isArray(checkedIds) || checkedIds.length === 0) {
    return { verified: false, status: "CLAIM_REJECTED", reason: "MISSING_VERIFIER_CHECKS" };
  }
  const sentenceIds = new Set(sentence.evidence_ids);
  const checkedIdSet = new Set(checkedIds);
  if (
    sentenceIds.size !== sentence.evidence_ids.length
    || checkedIdSet.size !== checkedIds.length
    || sentenceIds.size !== checkedIdSet.size
    || [...sentenceIds].some((id) => !checkedIdSet.has(id))
  ) {
    return { verified: false, status: "CLAIM_REJECTED", reason: "VERIFIER_EVIDENCE_MISMATCH" };
  }
  return { verified: true, status: "AUTO_PASS" };
}
