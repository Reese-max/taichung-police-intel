// apps/web/lib/answer-evidence-gate.js
// Answer Evidence Gate (#32): claim-level verification of an LLM answer draft
// against the canonical evidence index before anything is shown to the user.
//
// Pipeline position: Query result -> LLM draft -> claim extraction -> this gate
// -> remove/qualify unsupported claims -> final answer + ClaimEvidenceReceipt.
// Web Chat (#29) and the Evidence MCP (#15) must share this single module.
//
// A claim is a normalized proposition (subject/value), not free text, so the
// check stays deterministic — no model judgment is used to decide support.
// Only official evidence types may support a claim; media-derived navigation
// text (GROQ_ASR) and synthesis (AI_SYNTHESIS) are discovery aids, never proof.

import { createHash } from "node:crypto";

import { EVIDENCE_TYPES } from "./council-prep.js";

export const CLAIM_SCHEMA_VERSION = 1;
export const EVIDENCE_SCHEMA_VERSION = 1;
export const RECEIPT_SCHEMA_VERSION = 1;
export const VALIDATOR_VERSION = "answer-evidence-gate/1";

export const CLAIM_TYPES = Object.freeze([
  "TIME",
  "LOCATION",
  "STATUS",
  "CAUSE",
  "STATISTIC",
  "AGENCY",
  "OTHER",
]);
export const FACTUAL_CLAIM_TYPES = Object.freeze(CLAIM_TYPES.filter((type) => type !== "OTHER"));
export const SUPPORT_STATUSES = Object.freeze(["SUPPORTED", "PARTIAL", "UNSUPPORTED", "CONFLICT", "STALE"]);
export const GATE_STATUSES = Object.freeze(["PASS", "QUALIFIED", "BLOCKED"]);
export const TEMPORAL_SCOPES = Object.freeze(["CURRENT", "HISTORICAL"]);

export const OFFICIAL_EVIDENCE_TYPES = Object.freeze(["ORAL_OFFICIAL", "WRITTEN_OFFICIAL", "RESOLUTION"]);

const CURRENT_PHRASING = /目前|最新|現行|現在|本[日月年次]|current|latest|today/i;

const UNKNOWN_TEXT = Object.freeze({
  TIME: "官方來源未提供可驗證的時間",
  LOCATION: "官方來源未提供可驗證的地點",
  STATUS: "官方來源未提供可驗證的狀態",
  CAUSE: "官方來源未說明原因",
  STATISTIC: "官方來源未提供可驗證的數字",
  AGENCY: "官方來源未提供可驗證的機關歸屬",
  OTHER: "官方來源未提供可驗證資料",
});

function canonicalize(value) {
  if (Array.isArray(value)) return `[${value.map(canonicalize).join(",")}]`;
  if (value && typeof value === "object") {
    return `{${Object.keys(value)
      .sort()
      .map((key) => `${JSON.stringify(key)}:${canonicalize(value[key])}`)
      .join(",")}}`;
  }
  return JSON.stringify(value);
}

function sha256(text) {
  return createHash("sha256").update(text, "utf8").digest("hex");
}

function isTerm(value) {
  return (typeof value === "string" && value.trim() !== "") || Number.isFinite(value);
}

function normalizeTerm(value) {
  return String(value).normalize("NFC").replace(/\s+/g, "").trim();
}

function normalizeAssertion(assertion) {
  if (!assertion || typeof assertion !== "object") return null;
  if (!isTerm(assertion.subject) || !isTerm(assertion.value)) return null;
  return { subject: normalizeTerm(assertion.subject), value: normalizeTerm(assertion.value) };
}

function readEvidence(raw) {
  if (!raw || typeof raw !== "object") return null;
  if ((raw.schema_version ?? EVIDENCE_SCHEMA_VERSION) !== EVIDENCE_SCHEMA_VERSION) return null;
  if (!raw.evidence_id || !EVIDENCE_TYPES.includes(raw.evidence_type) || !raw.source_id) return null;
  const assertions = Array.isArray(raw.assertions)
    ? raw.assertions.map(normalizeAssertion).filter(Boolean)
    : [];
  return {
    evidence_id: String(raw.evidence_id),
    evidence_type: raw.evidence_type,
    source_id: String(raw.source_id),
    locator: raw.locator ?? null,
    document_version: raw.document_version ?? null,
    // `freshness: "STALE"` is an accepted alias for `is_current: false` so
    // upstream projections can pass either marker of non-current evidence.
    is_current: raw.is_current !== false && raw.freshness !== "STALE",
    official: OFFICIAL_EVIDENCE_TYPES.includes(raw.evidence_type),
    published_at: raw.published_at ?? raw.effective_at ?? null,
    assertions,
  };
}

export function publicationHash(evidence) {
  const entries = (Array.isArray(evidence) ? evidence : [])
    .map(readEvidence)
    .filter(Boolean)
    .map((record) => ({
      evidence_id: record.evidence_id,
      evidence_type: record.evidence_type,
      source_id: record.source_id,
      locator: record.locator,
      document_version: record.document_version,
      is_current: record.is_current,
      assertions: [...record.assertions].sort(
        (a, b) => a.subject.localeCompare(b.subject) || a.value.localeCompare(b.value),
      ),
    }))
    .sort((a, b) => a.evidence_id.localeCompare(b.evidence_id));
  return sha256(canonicalize(entries));
}

function claimId(raw) {
  const seed =
    raw && typeof raw === "object" && raw.text && raw.proposition !== undefined
      ? canonicalize({
          claim_type: raw.claim_type,
          text: String(raw.text).trim(),
          proposition: raw.proposition,
          temporal_scope: raw.temporal_scope ?? null,
          cited_evidence_ids: Array.isArray(raw.cited_evidence_ids)
            ? [...raw.cited_evidence_ids].sort()
            : [],
        })
      : canonicalize(raw ?? null);
  return `CLM-${sha256(seed).slice(0, 16).toUpperCase()}`;
}

function readClaim(raw) {
  if (!raw || typeof raw !== "object") return { error: "INVALID_CLAIM" };
  if ((raw.schema_version ?? CLAIM_SCHEMA_VERSION) !== CLAIM_SCHEMA_VERSION) {
    return { error: "INVALID_SCHEMA_VERSION" };
  }
  const text = String(raw.text ?? "").trim();
  if (!text) return { error: "MISSING_TEXT" };
  if (!CLAIM_TYPES.includes(raw.claim_type)) return { error: "INVALID_CLAIM_TYPE" };
  const factual = raw.claim_type !== "OTHER";
  const rawProps = Array.isArray(raw.proposition)
    ? raw.proposition
    : raw.proposition === undefined || raw.proposition === null
      ? []
      : [raw.proposition];
  const propositions = rawProps.map((prop) =>
    prop && typeof prop === "object" && isTerm(prop.subject) && isTerm(prop.value)
      ? { subject: normalizeTerm(prop.subject), value: normalizeTerm(prop.value) }
      : null,
  );
  if (factual && propositions.some((prop) => prop === null)) return { error: "INVALID_PROPOSITION" };
  if (factual && !propositions.length) return { error: "MISSING_PROPOSITION" };
  if (
    raw.cited_evidence_ids !== undefined &&
    (!Array.isArray(raw.cited_evidence_ids) ||
      raw.cited_evidence_ids.some((id) => typeof id !== "string" || !id.trim()))
  ) {
    return { error: "INVALID_CITATIONS" };
  }
  const cited = (raw.cited_evidence_ids ?? []).map((id) => String(id));
  const temporalScope = raw.temporal_scope === "HISTORICAL" ? "HISTORICAL" : "CURRENT";
  return {
    claim: {
      claim_id: typeof raw.claim_id === "string" && raw.claim_id ? raw.claim_id : claimId(raw),
      text,
      claim_type: raw.claim_type,
      temporal_scope: temporalScope,
      asserts_current: temporalScope !== "HISTORICAL" || CURRENT_PHRASING.test(text),
      factual,
      propositions: propositions.filter(Boolean),
      cited_evidence_ids: cited,
    },
  };
}

function exactSupporters(records, subject, value) {
  return records.filter(
    (record) =>
      record.assertions.some(
        (assertion) => assertion.subject === subject && assertion.value === value,
      ) &&
      isTerm(record.locator) &&
      isTerm(record.document_version),
  );
}

function looseSupporters(records, subject, value) {
  return records.filter((record) =>
    record.assertions.some(
      (assertion) => assertion.subject === subject && assertion.value === value,
    ),
  );
}

function valuesForSubject(records, subject) {
  return [...new Set(
    records.flatMap((record) =>
      record.assertions.filter((a) => a.subject === subject).map((a) => a.value),
    ),
  )];
}

function evaluateProposition(prop, allRecords, pool, assertsCurrent) {
  // CONFLICT is judged over the whole index so a draft cannot cite only one
  // side of a genuine disagreement between current official sources.
  const conflictRecords = allRecords.filter(
    (record) =>
      record.official &&
      record.is_current &&
      record.assertions.some((assertion) => assertion.subject === prop.subject),
  );
  const conflictValues = valuesForSubject(conflictRecords, prop.subject);
  if (conflictValues.length >= 2) {
    const detail = conflictValues.map((value) => {
      const holder = conflictRecords.find((record) =>
        record.assertions.some((a) => a.subject === prop.subject && a.value === value),
      );
      return { value, source_id: holder.source_id, evidence_id: holder.evidence_id };
    });
    return { status: "CONFLICT", reason_code: "OFFICIAL_SOURCES_DISAGREE", conflict_values: detail };
  }

  const candidates = pool.filter((record) =>
    record.assertions.some((assertion) => assertion.subject === prop.subject),
  );
  const official = candidates.filter((record) => record.official);
  const current = official.filter((record) => record.is_current);
  const stale = official.filter((record) => !record.is_current);

  const exactCurrent = exactSupporters(current, prop.subject, prop.value);
  if (exactCurrent.length) {
    return { status: "SUPPORTED", supporting: exactCurrent };
  }
  if (looseSupporters(current, prop.subject, prop.value).length) {
    return {
      status: "PARTIAL",
      reason_code: "MISSING_EXACT_LOCATOR",
      supporting: looseSupporters(current, prop.subject, prop.value),
    };
  }
  const staleMatches = looseSupporters(stale, prop.subject, prop.value);
  if (staleMatches.length) {
    if (assertsCurrent) {
      // A current official record asserting a different value for the same
      // subject means the claim is contradicted by newer data — not merely
      // unconfirmed. Reporting STALE here would wrongly imply nothing newer
      // exists.
      const superseding = conflictRecords.filter((record) =>
        record.assertions.some(
          (assertion) => assertion.subject === prop.subject && assertion.value !== prop.value,
        ),
      );
      if (superseding.length) {
        return {
          status: "UNSUPPORTED",
          reason_code: "SUPERSEDED_BY_CURRENT",
          supporting: staleMatches,
          official_values: valuesForSubject(superseding, prop.subject),
        };
      }
      // Reaching here means any current official record asserts the claim
      // value itself, so newer confirmation exists even though the matched
      // (or cited) evidence is stale.
      return {
        status: "STALE",
        reason_code: "EVIDENCE_NOT_CURRENT",
        supporting: staleMatches,
        newer_confirmation: conflictRecords.length > 0,
      };
    }
    const exactStale = exactSupporters(stale, prop.subject, prop.value);
    if (exactStale.length) return { status: "SUPPORTED", supporting: exactStale, historical: true };
    return {
      status: "PARTIAL",
      reason_code: "MISSING_EXACT_LOCATOR",
      supporting: staleMatches,
    };
  }
  if (official.length) {
    return {
      status: "UNSUPPORTED",
      reason_code: "VALUE_MISMATCH",
      official_values: valuesForSubject(official, prop.subject),
    };
  }
  if (candidates.length) {
    return { status: "UNSUPPORTED", reason_code: "MEDIA_ONLY" };
  }
  return { status: "UNSUPPORTED", reason_code: "NO_EVIDENCE" };
}

function aggregateStatus(subResults) {
  const statuses = subResults.map((sub) => sub.status);
  if (statuses.includes("CONFLICT")) return "CONFLICT";
  if (statuses.every((status) => status === "UNSUPPORTED")) return "UNSUPPORTED";
  if (statuses.includes("UNSUPPORTED") || statuses.includes("PARTIAL")) return "PARTIAL";
  if (statuses.includes("STALE")) return "STALE";
  if (statuses.every((status) => status === "SUPPORTED")) return "SUPPORTED";
  return "PARTIAL";
}

function conflictText(claim, subResults) {
  const seen = new Map();
  for (const sub of subResults) {
    for (const entry of sub.conflict_values || []) {
      seen.set(`${entry.source_id}|${entry.value}`, entry);
    }
  }
  const parts = [...seen.values()].map((entry) => `${entry.source_id} 記載為 ${entry.value}`);
  return `官方來源記載不一致：${parts.join("；")}`;
}

function staleText(claim, subResults) {
  const fragments = claim.propositions
    .map((prop, index) => ({ prop, sub: subResults[index] }))
    .filter(({ sub }) => sub?.status === "STALE")
    .map(({ prop, sub }) => {
      const dates = (sub.supporting || [])
        .map((record) => String(record.published_at ?? ""))
        .filter(Boolean)
        .sort();
      const date = (dates.at(-1) || "未知日期").slice(0, 10);
      const suffix = sub.newer_confirmation
        ? "較新的官方資料亦記載相同內容"
        : "尚無更新的官方確認";
      return `官方資料（${date}，可能已過期）記載${prop.subject}為${prop.value}，${suffix}`;
    });
  return fragments.join("；");
}

function partialText(claim, subResults) {
  const unproven = claim.propositions
    .map((prop, index) => ({ prop, sub: subResults[index] }))
    .filter(({ sub }) => sub && sub.status !== "SUPPORTED")
    .map(({ prop, sub }) =>
      sub.status === "PARTIAL" && sub.reason_code === "MISSING_EXACT_LOCATOR"
        ? `${prop.subject}（證據定位不完整）`
        : sub.status === "STALE"
          ? `${prop.subject}（官方資料可能已過期）`
          : prop.subject,
    );
  return `${claim.text}（官方來源未證實：${unproven.join("、")}）`;
}

function supporterSummaries(subResults) {
  const seen = new Map();
  for (const sub of subResults) {
    for (const record of sub.supporting || []) {
      seen.set(record.evidence_id, {
        evidence_id: record.evidence_id,
        source_id: record.source_id,
        locator: record.locator,
        document_version: record.document_version,
      });
    }
  }
  return [...seen.values()].sort((a, b) => a.evidence_id.localeCompare(b.evidence_id));
}

function evaluateClaim(raw, index) {
  const { claim, error } = readClaim(raw);
  if (error) {
    return {
      claim_id: claimId(raw),
      text: String(raw?.text ?? ""),
      claim_type: CLAIM_TYPES.includes(raw?.claim_type) ? raw.claim_type : "OTHER",
      support_status: "UNSUPPORTED",
      reason_code: error,
      requires_evidence: true,
      qualified_text: "官方來源未提供可驗證資料",
      supporting_evidence: [],
      admitted: false,
    };
  }
  if (!claim.factual) {
    return {
      claim_id: claim.claim_id,
      text: claim.text,
      claim_type: claim.claim_type,
      support_status: "SUPPORTED",
      reason_code: "NON_FACTUAL",
      requires_evidence: false,
      supporting_evidence: [],
      admitted: true,
      final_text: claim.text,
    };
  }

  let pool = index;
  if (claim.cited_evidence_ids.length) {
    const byId = new Map(index.map((record) => [record.evidence_id, record]));
    if (claim.cited_evidence_ids.some((id) => !byId.has(id))) {
      return {
        claim_id: claim.claim_id,
        text: claim.text,
        claim_type: claim.claim_type,
        support_status: "UNSUPPORTED",
        reason_code: "UNKNOWN_EVIDENCE",
        requires_evidence: true,
        qualified_text: UNKNOWN_TEXT[claim.claim_type] || UNKNOWN_TEXT.OTHER,
        supporting_evidence: [],
        admitted: false,
      };
    }
    pool = claim.cited_evidence_ids.map((id) => byId.get(id));
  }

  const subResults = claim.propositions.map((prop) =>
    evaluateProposition(prop, index, pool, claim.asserts_current),
  );
  const status = aggregateStatus(subResults);
  const supporting = supporterSummaries(subResults);
  const conflictValues = [
    ...new Map(
      subResults
        .flatMap((sub) => sub.conflict_values || [])
        .map((entry) => [`${entry.evidence_id}|${entry.value}`, entry]),
    ).values(),
  ];
  const officialValues = [
    ...new Set(subResults.flatMap((sub) => sub.official_values || [])),
  ];
  const base = {
    claim_id: claim.claim_id,
    claim_type: claim.claim_type,
    requires_evidence: true,
    supporting_evidence: supporting,
    ...(conflictValues.length ? { conflict_values: conflictValues } : {}),
    ...(officialValues.length ? { official_values: officialValues } : {}),
  };

  if (status === "SUPPORTED") {
    return { ...base, text: claim.text, support_status: "SUPPORTED", admitted: true, final_text: claim.text };
  }
  if (status === "UNSUPPORTED") {
    const reason =
      subResults.find((sub) => sub.reason_code === "SUPERSEDED_BY_CURRENT")?.reason_code ??
      subResults[0]?.reason_code ??
      "NO_EVIDENCE";
    const supersededValues = [
      ...new Set(
        subResults
          .filter((sub) => sub.reason_code === "SUPERSEDED_BY_CURRENT")
          .flatMap((sub) => sub.official_values || []),
      ),
    ];
    return {
      ...base,
      text: claim.text,
      support_status: "UNSUPPORTED",
      reason_code: reason,
      qualified_text: supersededValues.length
        ? `官方來源未證實此說法；最新官方資料記載為 ${supersededValues.join("、")}`
        : UNKNOWN_TEXT[claim.claim_type] || UNKNOWN_TEXT.OTHER,
      admitted: false,
    };
  }
  const finalText =
    status === "CONFLICT"
      ? conflictText(claim, subResults)
      : status === "STALE"
        ? staleText(claim, subResults)
        : partialText(claim, subResults);
  return {
    ...base,
    text: claim.text,
    support_status: status,
    reason_code: subResults.find((sub) => sub.status !== "SUPPORTED")?.reason_code,
    qualified_text: finalText,
    admitted: true,
    final_text: finalText,
  };
}

function blockedReceipt(reason, error) {
  return {
    gate_status: "BLOCKED",
    final_claims: [],
    removed_claims: [],
    receipt: {
      schema_version: RECEIPT_SCHEMA_VERSION,
      validator_version: VALIDATOR_VERSION,
      gate_status: "BLOCKED",
      failure_reason: reason,
      publication_hash: null,
      ...(error !== undefined
        ? { error_detail: String(error?.message ?? error).slice(0, 200) }
        : {}),
      claim_ids: [],
      evidence_ids: [],
      source_document_versions: {},
      claims: [],
    },
  };
}

/**
 * Validates every factual claim in an answer draft against the evidence index.
 * Never throws: a validator failure fails closed and releases nothing.
 *
 * @param {{claims: object[], evidence: object[], generated_at?: string}} input
 * @returns {{gate_status: string, final_claims: object[], removed_claims: object[], receipt: object}}
 */
export function gateAnswer({ claims, evidence, generated_at } = {}) {
  try {
    if (!Array.isArray(claims) || !Array.isArray(evidence)) {
      return blockedReceipt("INVALID_INPUT");
    }
    const index = evidence.map(readEvidence).filter(Boolean);
    if (new Set(index.map((record) => record.evidence_id)).size !== index.length) {
      return blockedReceipt("DUPLICATE_EVIDENCE_ID");
    }
    const evaluated = claims.map((claim) => evaluateClaim(claim, index));

    const finalClaims = evaluated
      .filter((entry) => entry.admitted)
      .map((entry) => ({
        claim_id: entry.claim_id,
        text: entry.final_text,
        claim_type: entry.claim_type,
        support_status: entry.support_status,
      }));
    const removedClaims = evaluated
      .filter((entry) => !entry.admitted)
      .map((entry) => ({
        claim_id: entry.claim_id,
        text: entry.text,
        claim_type: entry.claim_type,
        support_status: entry.support_status,
        reason_code: entry.reason_code,
        qualified_text: entry.qualified_text,
      }));

    const evidenceIds = [
      ...new Set(evaluated.flatMap((entry) => entry.supporting_evidence.map((s) => s.evidence_id))),
    ].sort();
    const documentVersions = Object.create(null);
    for (const entry of evaluated) {
      for (const supporter of entry.supporting_evidence) {
        documentVersions[supporter.evidence_id] = supporter.document_version;
      }
    }
    const gateStatus = evaluated.every((entry) => entry.support_status === "SUPPORTED")
      ? "PASS"
      : "QUALIFIED";

    return {
      gate_status: gateStatus,
      final_claims: finalClaims,
      removed_claims: removedClaims,
      receipt: {
        schema_version: RECEIPT_SCHEMA_VERSION,
        validator_version: VALIDATOR_VERSION,
        gate_status: gateStatus,
        publication_hash: publicationHash(index),
        ...(generated_at ? { generated_at } : {}),
        claim_ids: evaluated.map((entry) => entry.claim_id),
        evidence_ids: evidenceIds,
        source_document_versions: documentVersions,
        claims: evaluated.map((entry) => ({
          claim_id: entry.claim_id,
          claim_type: entry.claim_type,
          support_status: entry.support_status,
          reason_code: entry.reason_code ?? null,
          requires_evidence: entry.requires_evidence,
          original_text: entry.text,
          final_text: entry.final_text ?? entry.qualified_text ?? null,
          supporting_evidence: entry.supporting_evidence,
          ...(entry.conflict_values ? { conflict_values: entry.conflict_values } : {}),
          ...(entry.official_values ? { official_values: entry.official_values } : {}),
        })),
      },
    };
  } catch (error) {
    return blockedReceipt("VALIDATOR_ERROR", error);
  }
}
