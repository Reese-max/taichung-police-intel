// Same closed schema as the canonical Python public brief projection.
const scalars = names => Object.fromEntries(names.split(" ").map(name => [name, null]));
const ATTACHMENT = { ...scalars("attachment_id content_sha256 etag last_modified") };
const VERSION = { ...scalars("version normalized_sha256 official_url document_version_id body_sha256 normalized_text_sha256 published_at source_modified_at effective_at expires_at observed_at last_checked_at"), attachments: [ATTACHMENT] };
const AFFECTED_CLAIM = { ...scalars("brief_id brief_version claim_id source_version source_document_version evidence_locator") };
const INVALIDATION = { ...scalars("invalidation_id event_id reason detected_at resolved_in_handoff_id"), changed_fields: [null], before: VERSION, after: VERSION, affected_claims: [AFFECTED_CLAIM] };
const PROFILE_RELEVANCE = { ...scalars("profile_id profile_version profile_hash ranking_policy_version label score"), reason_codes: [null] };
const ITEM = { ...scalars("event_id identity watch_id stable_key source_id source_name change_type headline what_changed why_it_matters recommended_action deadline temporal_basis time_basis date_status detected_at source_version source_sha256 source_document_version official_url verification_status evidence_status publication_tier tracking_id watch_status reason_code last_reviewed_version last_handoff_id source_health source_freshness"), affected_roles: [null], changed_fields: [null], source_gaps: [null], profile_relevance: PROFILE_RELEVANCE, invalidation: INVALIDATION };
const BRIEF = { ...scalars("schema_version mode generator_version generated_at source_collection_run_id source_status_generated_at publication_status snapshot_complete status_message"), overview: { ...scalars("archive_total current_change_count legacy_home_candidate_count other_change_count priority_count tracking_count tracking_total") }, priority_items: [ITEM], tracking_items: [ITEM], other_changes: [ITEM], source_health: { ...scalars("status pass_count stale_count failed_count gap_count") } };
const SOURCE = { ...scalars("source_id source_name source_url source_health window_completeness result freshness_status data_as_of last_checked_at last_success_at current_source_run_id manifest_sha256 data_as_of_basis data_as_of_scope"), data_as_of_evidence: scalars("date_basis document_revision_at official_url content_sha256 page_number"), intelligence_gaps: [null] };

function closedValue(value, shape) {
  if (value === null || value === undefined) return null;
  if (shape && !Array.isArray(shape) && typeof shape === "object") {
    if (typeof value !== "object" || Array.isArray(value) || Object.keys(value).some(key => !Object.hasOwn(shape, key))) throw Error("closed public projection violation");
    return Object.fromEntries(Object.entries(value).map(([key, item]) => [key, closedValue(item, shape[key])]));
  }
  if (Array.isArray(shape)) {
    if (!Array.isArray(value)) throw Error("closed public projection violation");
    return value.map(item => closedValue(item, shape[0]));
  }
  if (!["string", "boolean", "number"].includes(typeof value) || (typeof value === "number" && !Number.isFinite(value))) throw Error("closed public projection violation");
  return value;
}
export function projectPublicBrief(brief) {
  return closedValue(Object.fromEntries(Object.keys(BRIEF).map(key => [key, brief[key] ?? null])), BRIEF);
}
export function projectPublicSource(row) {
  return closedValue(Object.fromEntries(Object.keys(SOURCE).filter(key => key in row).map(key => [key, row[key]])), SOURCE);
}
export function briefProjectionFields() {
  const fields = new Set();
  function walk(shape) { if (Array.isArray(shape)) walk(shape[0]); else if (shape) for (const [key, child] of Object.entries(shape)) { fields.add(key); walk(child); } }
  walk(BRIEF); return [...fields].sort();
}
