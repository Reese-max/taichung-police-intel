import { executeDocumentResearch } from "./document-research.js";
import { parseResearchInput, researchProviderState, researchTerms, selectResearchSources, ResearchError, readBoundedJson } from "./research.js";
import { gateAnswer } from "../../../apps/web/lib/answer-evidence-gate.js";
import { projectPublicBrief, projectPublicSource } from "./public-brief.js";
import { projectPublicationDates, projectSourceDates } from "../../../apps/web/lib/publication-dates.js";
import sourceCatalog from "../../../docs/govintel/source-catalog.v2.json" with { type: "json" };
import approvedSourcePolicy from "../../../docs/govintel/source-policy.approved.json" with { type: "json" };
import retentionMatrix from "../../../docs/govintel/retention-rights-policy.v1.json" with { type: "json" };

const MAX_REQUEST_BYTES = 64 * 1024;
const MAX_RESPONSE_BYTES = 256 * 1024;
const MAX_SNAPSHOT_AGE_MS = 16 * 60 * 60 * 1000;
const SERVER_VERSION = "query-gateway-v1-workers";
const ANSWER_VALIDATOR_VERSION = "answer-evidence-gate/3";
const MCP_PROTOCOL_VERSION = "2025-06-18";
const PUBLIC_PROJECTION = "METADATA_LINK_ONLY";
const RETENTION_POLICY = {
  policy_version: 1,
  policy_hash: "aa2f3f22f49927c673866c8c3ebdea816150fe60b03822cde4ff0bd104f87034",
  public_projection: PUBLIC_PROJECTION,
  full_text_allowed: false,
};
function retentionPolicy(snapshot) {
  if (snapshot.policy.schema_version !== 2) return RETENTION_POLICY;
  const ref = snapshot.policy.governance_binding.retention_policy;
  return {...RETENTION_POLICY, policy_version: ref.policy_version, policy_hash: ref.policy_hash};
}
const MAX_RATE = 60;
const DEGRADED_RETRY_MS = 5_000;
const rateWindows = new Map();
let snapshotCache = null;
let snapshotBuild = null;
let degradedSince = null;

const CAPABILITY_DEFINITIONS = [
  ["publication_metadata", "只代表 policy 中已啟用來源，不代表世界完整性。", () => true],
  ["source_health", "健康來源不等於該問題領域具完整覆蓋。", () => true],
  ["council_affairs", "只涵蓋 policy 中符合議會語意且已啟用的來源。", (row) => sourceText(row).includes("議會") || sourceText(row).includes("質詢")],
  ["traffic_events", "僅涵蓋已啟用且明確屬交通事件的來源；不代表所有臨時交通事件。", (row) => row.role === "PRIMARY_EVENT" && ["交通", "道路", "公車"].some((term) => sourceText(row).includes(term))],
  ["fraud_reference", "僅作已啟用官方資料範圍內的反詐參考，不推論本地案件量。", (row) => ["PRIMARY_REFERENCE", "PRIMARY_EVENT"].includes(row.role) && ["反詐", "詐騙", "涉詐"].some((term) => sourceText(row).includes(term))],
];
const DOMAIN_CAPABILITIES = ["search_events", "get_event", "compare_event_versions", "query_statistics"];

function sourceText(row) {
  return [row.source_id, row.name, row.authority, row.role].filter(Boolean).join(" ");
}

function canonicalJson(value) {
  if (Array.isArray(value)) return `[${value.map(canonicalJson).join(",")}]`;
  if (value && typeof value === "object") {
    return `{${Object.keys(value).sort().map((key) => `${JSON.stringify(key)}:${canonicalJson(value[key])}`).join(",")}}`;
  }
  return JSON.stringify(value);
}

async function sha256(value) {
  const bytes = typeof value === "string" ? new TextEncoder().encode(value) : new Uint8Array(value);
  const digest = await crypto.subtle.digest("SHA-256", bytes);
  return [...new Uint8Array(digest)].map((byte) => byte.toString(16).padStart(2, "0")).join("");
}

function parseInstant(value) {
  if (typeof value !== "string" || !/(Z|[+-]\d{2}:\d{2})$/i.test(value)) return NaN;
  return Date.parse(value);
}

function isoNow() {
  return new Date().toISOString();
}

function freshness(dataStatus) {
  return { SNAPSHOT_RECENT: "RECENT", SOURCE_NOT_AVAILABLE: "UNKNOWN" }[dataStatus] || dataStatus;
}

function verificationSummary(dataStatus) {
  return {
    RECENT: "僅代表已核對的公開快照範圍，不代表所有現實事件。",
    STALE: "公開快照已過期，不能宣稱目前最新，也不能用零結果代表沒有事件。",
    PARTIAL: "監測或發布範圍不完整，不能用零結果排除其他事件。",
    UNKNOWN: "資料時效或完整性無法核對，不能把結果解讀成完整現況。",
    SOURCE_NOT_AVAILABLE: "指定來源不在核准快照，不能把結果解讀成沒有資料。",
  }[dataStatus] || "資料狀態未能核對，不能作出完整現況結論。";
}

function jsonError(code, message) {
  return { schema_version: 1, error: { code, message } };
}

function assertHash(value, name) {
  if (typeof value !== "string" || !/^[a-f0-9]{64}$/.test(value)) throw new Error(`${name} must be a SHA-256 hex digest`);
}

function approvedDateOrigins(sourceId) {
  const row = sourceCatalog.sources.find(source => source.source_id === sourceId);
  return row ? [new URL(row.entrypoint).origin] : [];
}

function projectFeedItem(item, feedHash, sourceFreshness = null) {
  if (!item || typeof item !== "object" || typeof item.stable_id !== "string" || typeof item.title !== "string" || typeof item.source_id !== "string") {
    throw new Error("invalid feed row");
  }
  assertHash(item.content_sha256, "content_sha256");
  if (item.official_url !== null && item.official_url !== undefined &&
      (!String(item.official_url).startsWith("https://") || new URL(item.official_url).username || new URL(item.official_url).password)) {
    throw new Error(`invalid official_url for ${item.stable_id}`);
  }
  for (const key of ["published_at", "data_as_of", "fetched_at"]) {
    if (item[key] !== null && item[key] !== undefined && !Number.isFinite(parseInstant(item[key]))) throw new Error(`invalid ${key}`);
  }
  if (item.change_type != null && (typeof item.change_type !== "string" || !["NEW", "REVISED", "STATUS_CHANGED", "DEADLINE_CHANGED", "CONFIRMED", "UNCHANGED", "LKG", "REMOVED"].includes(item.change_type))) throw new Error("invalid projected change_type");
  if (item.freshness_status != null && (typeof item.freshness_status !== "string" || !["FRESH", "RECENT", "STALE", "VERY_STALE", "UNKNOWN", "NO_DATA"].includes(item.freshness_status.toUpperCase()))) throw new Error("invalid projected freshness_status");
  // An item without its own freshness inherits the source row's, which is the same
  // effective freshness the evidence catalog and answer gate use.
  const freshness = String(item.freshness_status || sourceFreshness || "UNKNOWN").toUpperCase();
  return {
    record_type: "publication_item",
    canonical_id: item.stable_id,
    title: item.title,
    source_id: item.source_id,
    source_role: typeof item.source_role === "string" ? item.source_role : null,
    official_url: item.official_url ?? null,
    published_at: item.published_at ?? null,
    data_as_of: item.data_as_of ?? null,
    fetched_at: item.fetched_at ?? null,
    ...projectPublicationDates(item, approvedDateOrigins(item.source_id)),
    change_type: item.change_type ?? null,
    freshness_status: item.freshness_status ?? null,
    source_health: item.source_health ?? null,
    window_completeness: item.window_completeness ?? null,
    committee: String(item.committee || ""),
    next_milestone: item.next_milestone ?? null,
    evidence_count: Number.isInteger(item.evidence_count) && item.evidence_count >= 0 ? item.evidence_count : 0,
    content_sha256: item.content_sha256,
    trust_tier: "CANONICAL_PUBLICATION",
    verification_status: ["FRESH", "RECENT"].includes(freshness) ? "VERIFIED" : "STALE",
    canonical_ref: {
      artifact: "intelligence-feed.json", artifact_sha256: feedHash, stable_id: item.stable_id,
      document_version_id: `DOCV-${item.content_sha256.slice(0, 20).toUpperCase()}`,
      evidence_id: `PUB-${item.stable_id}`,
    },
  };
}

function projectSource(source, statusHash) {
  if (!source || typeof source.source_id !== "string" || typeof source.source_name !== "string") throw new Error("invalid source row");
  const enums = {
    source_health: ["PASS", "DEGRADED", "FAILED", "QUARANTINED", "NOT_RUN", "UNKNOWN"],
    window_completeness: ["COMPLETE_WITH_ITEMS", "COMPLETE_ZERO", "PARTIAL", "NOT_RUN", "UNKNOWN"],
    result: ["NEW_ITEMS", "NO_NEW_ITEM", "PARTIAL", "FAILED", "NOT_RUN", "UNKNOWN"],
    freshness_status: ["FRESH", "RECENT", "STALE", "VERY_STALE", "UNKNOWN", "NO_DATA"],
  };
  for (const key of [...Object.keys(enums), "last_checked_at", "data_as_of"]) {
    const value = source[key];
    if (value != null && typeof value !== "string") throw new Error(`source field ${key} must be a scalar string or null`);
    if (Object.hasOwn(enums, key) && value != null && !enums[key].includes(value.toUpperCase())) throw new Error(`source field ${key} has an unsupported enum`);
    if (["last_checked_at", "data_as_of"].includes(key) && value != null && !Number.isFinite(parseInstant(value))) throw new Error(`source timestamp ${key} is invalid`);
  }
  return {
    source_id: source.source_id,
    name: source.source_name,
    source_health: source.source_health ?? null,
    window_completeness: source.window_completeness ?? null,
    result: source.result ?? null,
    last_checked_at: source.last_checked_at ?? null,
    data_as_of: source.data_as_of ?? null,
    freshness_status: source.freshness_status ?? null,
    ...projectSourceDates(source, approvedDateOrigins(source.source_id)),
    canonical_ref: { artifact: "source-status.json", artifact_sha256: statusHash, source_id: source.source_id },
  };
}

function makeCapabilities(policy) {
  const active = new Set(policy.active_source_ids);
  const activeRows = sourceCatalog.sources.filter((row) => active.has(row.source_id));
  return CAPABILITY_DEFINITIONS.map(([capabilityId, limitation, selector]) => ({
    capability_id: capabilityId,
    required_sources: activeRows.filter(selector).map((row) => row.source_id).sort(),
    coverage_limitation: limitation,
  }));
}

async function validatePolicy(policy) {
  if (!policy || ![1, 2].includes(policy.schema_version) || !Array.isArray(policy.active_source_ids) || !policy.active_source_ids.length) throw new Error("invalid source policy");
  const ids = [...policy.active_source_ids].sort();
  if (JSON.stringify(ids) !== JSON.stringify(policy.active_source_ids) || new Set(ids).size !== ids.length) throw new Error("source policy IDs must be sorted and unique");
  if (sourceCatalog.sources.filter((row) => ids.includes(row.source_id) && row.status === "PRODUCTION_ACTIVE").length !== ids.length) throw new Error("source policy does not match catalog");
  assertHash(policy.policy_hash, "policy_hash");
  assertHash(policy.catalog_hash, "catalog_hash");
  if (policy.schema_version === 2) {
    if (canonicalJson(policy) !== canonicalJson(approvedSourcePolicy)) throw new Error("governed policy is not the repository-approved snapshot");
    const { policy_hash: _hash, ...core } = policy;
    if (await sha256(canonicalJson(core)) !== policy.policy_hash) throw new Error("governed policy self-hash mismatch");
    const retentionCore = { schema_version: retentionMatrix.schema_version, policy_version: retentionMatrix.policy_version,
      updated_at: retentionMatrix.updated_at, retention_windows: retentionMatrix.retention_windows,
      classes: retentionMatrix.classes, source_classes: retentionMatrix.source_classes,
      prohibited_public_fields: [...retentionMatrix.prohibited_public_fields].sort() };
    if (await sha256(canonicalJson(retentionCore)) !== policy.governance_binding?.retention_policy?.policy_hash) throw new Error("governed rights matrix binding mismatch");
  }
}

function formalAdmission(policy) {
  if (policy.schema_version !== 2) return { status: "UNKNOWN", reason: "APPROVED_GOVERNANCE_MISSING", governance_hash: null,
    blocked_sources: policy.active_source_ids, per_source: Object.fromEntries(policy.active_source_ids.map(id => [id, "APPROVED_GOVERNANCE_MISSING"])) };
  const prohibited = new Set(policy.governance_binding.prohibited_public_fields);
  const perSource = {};
  for (const row of policy.active_sources) {
    const rights = row.rights_retention_public_policy_refs;
    if (!["VERIFIED_METADATA_PERMISSION", "OPEN_DATA_LICENSED"].includes(rights.rights_status) || rights.review_required !== false) perSource[row.source_id] = "RIGHTS_UNKNOWN_OR_UNREVIEWED";
    else if (rights.full_text_allowed || rights.excerpt_allowed || rights.public_fields.some(field => prohibited.has(field))) perSource[row.source_id] = "PUBLIC_FIELDS_PROHIBITED";
  }
  return { status: Object.keys(perSource).length ? "RIGHTS_BLOCKED" : "ADMITTED", reason: null,
    governance_hash: policy.governance_binding.governance_hash, blocked_sources: Object.keys(perSource).sort(), per_source: perSource };
}

function briefAdmission(policy, brief) {
  const summary = policy.governance_binding?.derived_summary_policy;
  if (!summary || summary.review_required !== false || !["PROJECT_CONTROLLED", "VERIFIED_METADATA_PERMISSION", "OPEN_DATA_LICENSED"].includes(summary.rights_status)
      || summary.public_projection !== "EVIDENCE_BOUND_SUMMARY_ONLY") return false;
  const fields = new Set(summary.public_fields);
  function allowed(value) {
    if (Array.isArray(value)) return value.every(allowed);
    if (value && typeof value === "object") return Object.entries(value).every(([key, child]) => fields.has(key) && allowed(child));
    return true;
  }
  if (!allowed(brief)) return false;
  const sources = new Map(policy.active_sources.map(row => [row.source_id, row]));
  for (const key of ["priority_items", "tracking_items", "other_changes"]) for (const row of brief[key] || []) {
    const source = sources.get(row.source_id);
    if (!source || source.rights_retention_public_policy_refs.public_projection !== "EVIDENCE_BOUND_SUMMARY_ONLY") return false;
    try { if (!source.approved_origins.includes(new URL(row.official_url).origin)) return false; }
    catch { return false; }
  }
  return true;
}

async function fetchArtifact(origin, name) {
  const url = `${origin.replace(/\/$/, "")}/data/${name}`;
  const response = await fetch(url, { cf: { cacheTtl: 30, cacheEverything: true } });
  if (!response.ok) {
    if (name === "release.json" && response.status === 404) throw new SnapshotIntegrityError("release manifest is missing");
    throw new Error(`publication artifact ${name} returned HTTP ${response.status}`);
  }
  const bytes = await response.arrayBuffer();
  return { name, bytes, hash: await sha256(bytes) };
}

export async function buildSnapshot(env, readArtifact = null) {
  const origin = env.PUBLIC_ORIGIN;
  if (!origin) throw new Error("PUBLIC_ORIGIN is not configured");
  const fetched = await Promise.all(
    ["intelligence-feed.json", "source-status.json", "v2-daily-brief.json", "source-policy.json"]
      .map(async name => ({ name, ...(await (readArtifact ? readArtifact(name) : fetchArtifact(origin, name))) })),
  );
  // Served bytes are validated within the integrity boundary. Network failures
  // alone may fall back to a previously verified publication.
  try {
    return await buildSnapshotFromArtifacts(fetched);
  } catch (error) {
    throw error instanceof SnapshotIntegrityError ? error : new SnapshotIntegrityError(String(error?.message || error));
  }
}

async function buildSnapshotFromArtifacts(fetched) {
  // A served artifact that cannot be parsed, or that exceeds its byte budget, is
  // a broken publication rather than an outage, so it stays inside this boundary.
  const documents = {};
  for (const artifact of fetched) {
    if (artifact.bytes !== undefined) {
      if (artifact.bytes.byteLength > 32 * 1024 * 1024) throw new Error(`publication artifact ${artifact.name} exceeds byte budget`);
      documents[artifact.name] = { value: JSON.parse(new TextDecoder().decode(artifact.bytes)), hash: artifact.hash };
    } else {
      documents[artifact.name] = { value: artifact.value, hash: artifact.hash };
    }
  }
  const feedDoc = documents["intelligence-feed.json"];
  const statusDoc = documents["source-status.json"];
  const briefDoc = documents["v2-daily-brief.json"];
  const policyDoc = documents["source-policy.json"];
  const feed = feedDoc.value;
  const status = statusDoc.value;
  const brief = briefDoc.value;
  const policy = policyDoc.value;
  if (![feed, status, brief].every((value) => value?.schema_version === 1)) throw new Error("unsupported publication schema");
  await validatePolicy(policy);
  if (policy.schema_version === 2) {
    const expected = { policy_version: policy.policy_version, policy_hash: policy.policy_hash, catalog_hash: policy.catalog_hash,
      active_source_ids: policy.active_source_ids, governance_hash: policy.governance_binding.governance_hash,
      retention_policy_hash: policy.governance_binding.retention_policy.policy_hash };
    if ([feed, status, brief].some(value => canonicalJson(value.source_policy) !== canonicalJson(expected))) throw new Error("canonical artifact governed policy binding mismatch");
  }
  const run = feed.collection_run_id;
  if (!run || run !== status.latest_collection_run?.collection_run_id || run !== brief.source_collection_run_id ||
      feed.generated_at !== status.generated_at || status.generated_at !== brief.source_status_generated_at) {
    throw new Error("cross-generation publication artifacts");
  }
  for (const value of [feed.generated_at, status.generated_at, brief.generated_at]) {
    if (!Number.isFinite(parseInstant(value))) throw new Error("publication timestamp is invalid");
  }
  if ([status.latest_collection_run.status, brief.publication_status].some(value => value != null && typeof value !== "string")) throw new Error("publication status fields must be scalar strings or null");
  if (brief.snapshot_complete != null && typeof brief.snapshot_complete !== "boolean") throw new Error("publication snapshot_complete must be boolean or null");
  if (!Array.isArray(feed.items) || feed.items.length > 10000 || !Array.isArray(status.sources)) throw new Error("publication arrays are invalid");
  // Only object rows contribute a freshness fallback; malformed rows are still rejected below.
  const sourceFreshness = new Map(status.sources.filter((source) => source && typeof source === "object")
    .map((source) => [source.source_id, String(source.freshness_status || "UNKNOWN").toUpperCase()]));
  let items = feed.items.map((item) => projectFeedItem(item, feedDoc.hash, sourceFreshness.get(item.source_id) || null)).sort((a, b) => a.canonical_id.localeCompare(b.canonical_id));
  if (new Set(items.map((item) => item.canonical_id)).size !== items.length) throw new Error("duplicate canonical_id");
  const sources = status.sources.map((source) => projectSource(source, statusDoc.hash)).sort((a, b) => a.source_id.localeCompare(b.source_id));
  const active = [...policy.active_source_ids].sort();
  if (JSON.stringify(sources.map((source) => source.source_id)) !== JSON.stringify(active)) throw new Error("source coverage does not match policy");
  if (items.some((item) => !active.includes(item.source_id))) throw new Error("feed references an unapproved source");
  const admission = formalAdmission(policy);
  items = items.filter(item => !admission.blocked_sources.includes(item.source_id));
  if (policy.schema_version === 2) {
    const bySource = new Map(policy.active_sources.map(row => [row.source_id, row]));
    if (items.some(item => item.official_url && !bySource.get(item.source_id).approved_origins.includes(new URL(item.official_url).origin))) throw new Error("formal query item origin is not approved for its source");
    const systemFields = new Set(["record_type", "canonical_id", "source_role", "trust_tier", "verification_status", "canonical_ref"]);
    items = items.map(item => Object.fromEntries(Object.entries(item).filter(([key]) => systemFields.has(key) || bySource.get(item.source_id).rights_retention_public_policy_refs.public_fields.includes(key))));
  }
  const policyBinding = {
    policy_version: policy.policy_version,
    policy_hash: policy.policy_hash,
    catalog_hash: policy.catalog_hash,
    active_source_ids: active,
    ...(policy.schema_version === 2 ? {governance_hash: policy.governance_binding.governance_hash,
      retention_policy_hash: policy.governance_binding.retention_policy.policy_hash} : {}),
  };
  const artifactHashes = { feed: feedDoc.hash, status: statusDoc.hash, brief: briefDoc.hash };
  const material = { schema_version: 2, projection_version: "publication-metadata-v4-governance", artifact_hashes: artifactHashes, policy: policyBinding, formal_admission: admission };
  const generationId = await sha256(canonicalJson(material));
  const generatedFrom = {
    collection_run_id: run,
    feed_generated_at: feed.generated_at,
    status_generated_at: status.generated_at,
    brief_generated_at: brief.generated_at,
    feed_sha256: feedDoc.hash,
    status_sha256: statusDoc.hash,
    brief_sha256: briefDoc.hash,
    policy_hash: policy.policy_hash,
    collection_status: status.latest_collection_run.status,
    publication_status: brief.publication_status,
    snapshot_complete: brief.snapshot_complete,
  };
  const snapshot = { feed, status, brief, policy, policyBinding, formalAdmission: admission, capabilityDefinitions: makeCapabilities(policy), items, sources, generatedFrom, generationId };
  // Bind the published catalog at publication time; live freshness still uses the server clock.
  snapshot.evidenceCatalogHash = await sha256(canonicalJson(trustedEvidence(snapshot, parseInstant(brief.generated_at))));
  return snapshot;
}

export async function createReleaseManifest(snapshot, codeSha, builtAt = isoNow()) {
  if (typeof codeSha !== "string" || !/^[a-f0-9]{40}$/.test(codeSha)) throw new Error("release code SHA must be a full Git SHA");
  if (!Number.isFinite(parseInstant(builtAt))) throw new Error("release built_at must include a timezone");
  const publication = snapshot.generatedFrom;
  const binding = {
    code_sha: codeSha,
    publication_generation: publication.collection_run_id,
    publication_hash: publication.brief_sha256,
    artifact_hashes: { feed: publication.feed_sha256, status: publication.status_sha256, brief: publication.brief_sha256 },
    source_policy_hash: snapshot.policyBinding.policy_hash,
    query_generation: snapshot.generationId,
    evidence_catalog_hash: snapshot.evidenceCatalogHash,
  };
  return {
    schema_version: 1, release_id: await sha256(canonicalJson(binding)), ...binding, built_at: builtAt,
    worker_version: null, pages_deployment: null, deployed_at: null, anonymous_http_verified_at: null,
    evidence_level: "BUILD_ONLY", production_verified: false,
  };
}

async function buildBoundSnapshot(env) {
  // A network failure must not win a race against an artifact integrity failure
  // and turn a broken publication into a last-known-good response.
  const results = await Promise.allSettled([
    buildSnapshot(env),
    fetchArtifact(env.PUBLIC_ORIGIN, "release.json"),
  ]);
  const failures = results.filter(result => result.status === "rejected").map(result => result.reason);
  const integrityFailure = failures.find(error => error instanceof SnapshotIntegrityError);
  if (integrityFailure) throw integrityFailure;
  if (failures.length) throw failures[0];
  const [snapshot, artifact] = results.map(result => result.value);
  try {
    if (artifact.bytes.byteLength > 64 * 1024) throw new Error("release exceeds byte budget");
    const manifest = JSON.parse(new TextDecoder().decode(artifact.bytes));
    const expected = await createReleaseManifest(snapshot, env.CF_VERSION_METADATA.tag, manifest?.built_at);
    for (const [field, expectedValue] of Object.entries(expected)) {
      if (canonicalJson(manifest?.[field]) !== canonicalJson(expectedValue)) throw new Error(`release ${field} mismatch`);
    }
    snapshot.release = { ...expected, worker_version: env.CF_VERSION_METADATA.id, evidence_level: "RUNTIME_BOUND" };
    return snapshot;
  } catch (error) {
    throw new SnapshotIntegrityError("release binding mismatch");
  }
}

async function getSnapshot(env) {
  const now = Date.now();
  const version = env.CF_VERSION_METADATA;
  if (!version?.id || !/^[a-f0-9]{40}$/.test(version?.tag ?? "")) {
    throw new GatewayError("QUERY_TEMPORARILY_UNAVAILABLE", "release binding is unavailable", 503);
  }
  const key = JSON.stringify([env.PUBLIC_ORIGIN, version.tag, version.id]);
  if (snapshotCache?.key === key && snapshotCache.expiresAt > now) return snapshotCache.degraded || snapshotCache.value;
  if (!snapshotBuild || snapshotBuild.key !== key) {
    const build = { key, value: buildBoundSnapshot(env) };
    snapshotBuild = build;
    build.value.finally(() => { if (snapshotBuild === build) snapshotBuild = null; }).catch(() => {});
  }
  try {
    const value = await snapshotBuild.value;
    snapshotCache = { key, value, expiresAt: Date.now() + 30_000 };
    degradedSince = null;
    return value;
  } catch (error) {
    // Inconsistent publication or release artifacts always fail closed, even
    // when this Worker has a previously verified generation in its cache.
    if (error instanceof SnapshotIntegrityError) {
      // Once the origin has contradicted this generation, an ensuing outage
      // cannot revive it through the last-known-good fallback.
      if (snapshotCache?.key === key) snapshotCache = null;
      throw new GatewayError("QUERY_TEMPORARILY_UNAVAILABLE", "release binding is unavailable", 503);
    }
    // A different deployment must never reuse another version's cache.
    if (snapshotCache?.key !== key) throw error;
    console.warn("query-gateway: index rebuild failed; serving last usable snapshot");
    degradedSince = degradedSince || new Date().toISOString();
    const degraded = { ...snapshotCache.value, degradedSince };
    snapshotCache = { key, value: snapshotCache.value, degraded, expiresAt: Date.now() + DEGRADED_RETRY_MS };
    return degraded;
  }
}

function assessScope(snapshot, sourceId, now = Date.now()) {
  const selected = snapshot.sources.filter((source) => !sourceId || source.source_id === sourceId);
  if (!selected.length) return { dataStatus: "SOURCE_NOT_AVAILABLE", gaps: [{ source_id: sourceId, reason: "NOT_IN_APPROVED_SNAPSHOT" }] };
  const gaps = [];
  const meta = snapshot.generatedFrom;
  // A projection served after a failed rebuild is degraded even when the
  // artifacts themselves are still young, so a zero-match query cannot be read
  // as a current, complete answer.
  if (snapshot.degradedSince) gaps.push({ source_id: null, reason: "INDEX_REBUILD_FAILED", since: snapshot.degradedSince });
  if (meta.collection_status !== "SUCCEEDED" || meta.publication_status !== "READY" || meta.snapshot_complete !== true) gaps.push({ source_id: null, reason: "INCOMPLETE_PUBLICATION" });
  const stamps = [meta.feed_generated_at, meta.status_generated_at, meta.brief_generated_at].map(parseInstant);
  if (stamps.some((stamp) => !Number.isFinite(stamp))) gaps.push({ source_id: null, reason: "UNKNOWN_PUBLICATION_TIME" });
  else if (stamps.some((stamp) => stamp > now)) gaps.push({ source_id: null, reason: "FUTURE_PUBLICATION_TIME" });
  else if (now - Math.min(...stamps) > MAX_SNAPSHOT_AGE_MS) gaps.push({ source_id: null, reason: "STALE_SNAPSHOT" });
  for (const source of selected) {
    // `result` is part of the incomplete-source determination exactly as in the
    // Python store: a source that reported a partial run is not complete scope.
    if (source.source_health !== "PASS" || !["COMPLETE_ZERO", "COMPLETE_WITH_ITEMS"].includes(source.window_completeness) ||
        !["NEW_ITEMS", "NO_NEW_ITEM"].includes(source.result)) gaps.push({ source_id: source.source_id, reason: "SOURCE_INCOMPLETE", source_health: source.source_health });
    const freshnessValue = String(source.freshness_status || "UNKNOWN").toUpperCase();
    if (["STALE", "VERY_STALE"].includes(freshnessValue)) gaps.push({ source_id: source.source_id, reason: "STALE_SOURCE_DATA", freshness_status: freshnessValue });
    else if (!["FRESH", "RECENT"].includes(freshnessValue)) gaps.push({ source_id: source.source_id, reason: "UNKNOWN_SOURCE_FRESHNESS", freshness_status: freshnessValue });
    const checked = parseInstant(source.last_checked_at);
    if (!Number.isFinite(checked)) gaps.push({ source_id: source.source_id, reason: "UNKNOWN_CHECK_TIME" });
    else if (checked > now) gaps.push({ source_id: source.source_id, reason: "FUTURE_CHECK_TIME" });
    else if (now - checked > MAX_SNAPSHOT_AGE_MS) gaps.push({ source_id: source.source_id, reason: "STALE_SOURCE_CHECK" });
  }
  if (gaps.some((gap) => gap.reason.includes("UNKNOWN") || gap.reason.includes("FUTURE"))) return { dataStatus: "UNKNOWN", gaps };
  if (gaps.some((gap) => gap.reason.includes("INCOMPLETE"))) return { dataStatus: "PARTIAL", gaps };
  return { dataStatus: gaps.length ? "STALE" : "SNAPSHOT_RECENT", gaps };
}

function queryCoverage(snapshot, capabilityId, requestedScope = {}) {
  const definition = snapshot.capabilityDefinitions.find((item) => item[0] === capabilityId || item.capability_id === capabilityId);
  const supportedCapabilities = snapshot.capabilityDefinitions.filter((item) => item.required_sources?.length).map((item) => item.capability_id);
  if (!definition || !definition.required_sources?.length) return {
    status: "CAPABILITY_NOT_AVAILABLE", policy_version: snapshot.policyBinding.policy_version, policy_hash: snapshot.policyBinding.policy_hash,
    capability_id: capabilityId, required_sources: definition?.required_sources || [], missing_required_sources: [], stale_required_sources: [],
    can_state_bounded_no_match: false, coverage_limitations: ["目前 source policy 沒有足以支援此能力的已啟用來源。"],
    supported_capabilities: supportedCapabilities, covered_sources: [], collection_completeness: {}, requested_scope: requestedScope,
  };
  const required = definition.required_sources;
  const sourceMap = new Map(snapshot.sources.map((source) => [source.source_id, source]));
  const missing = [];
  const stale = [];
  for (const sourceId of required) {
    const source = sourceMap.get(sourceId);
    if (!source) { missing.push(sourceId); continue; }
    if (source.source_health !== "PASS" || !["COMPLETE_ZERO", "COMPLETE_WITH_ITEMS"].includes(source.window_completeness) ||
        !["NEW_ITEMS", "NO_NEW_ITEM"].includes(source.result)) missing.push(sourceId);
    const state = String(source.freshness_status || "UNKNOWN").toUpperCase();
    if (["STALE", "VERY_STALE"].includes(state)) stale.push(sourceId);
    else if (!["FRESH", "RECENT"].includes(state)) missing.push(sourceId);
  }
  const uniqueMissing = [...new Set(missing)].sort();
  const uniqueStale = [...new Set(stale)].sort();
  // A projection served after a failed rebuild cannot license a bounded
  // no-match statement, even while its sources still look current.
  const degraded = Boolean(snapshot.degradedSince);
  const collectionStatus = uniqueMissing.length || degraded ? "PARTIAL" : uniqueStale.length ? "STALE" : "COVERED_BOUNDED_SCOPE";
  const rightsBlocked = capabilityId === "source_health" ? [] : required.filter(id => snapshot.formalAdmission.blocked_sources.includes(id));
  const status = rightsBlocked.length ? snapshot.formalAdmission.status : collectionStatus;
  return {
    status, policy_version: snapshot.policyBinding.policy_version, policy_hash: snapshot.policyBinding.policy_hash, capability_id: capabilityId,
    required_sources: required, missing_required_sources: uniqueMissing, stale_required_sources: uniqueStale,
    ...(capabilityId !== "source_health" ? { formal_admission: snapshot.formalAdmission, rights_blocked_sources: rightsBlocked, collection_coverage_status: collectionStatus } : {}),
    can_state_bounded_no_match: status === "COVERED_BOUNDED_SCOPE",
    coverage_limitations: rightsBlocked.length ? [definition.coverage_limitation, "來源治理／公開權利尚未核准，不能從正式查詢零結果推論沒有事件。"] : degraded
      ? [definition.coverage_limitation, "查詢索引上一次重建失敗，本次回應使用上一個可用 generation，不能據此回答目前沒有相關事件。"]
      : [definition.coverage_limitation],
    supported_capabilities: supportedCapabilities, covered_sources: required.filter((sourceId) => !uniqueMissing.includes(sourceId) && !uniqueStale.includes(sourceId) && !rightsBlocked.includes(sourceId)),
    ...(capabilityId !== "source_health" ? {collection_covered_sources: required.filter(id => !uniqueMissing.includes(id) && !uniqueStale.includes(id)),
      formally_admitted_sources: required.filter(id => !uniqueMissing.includes(id) && !uniqueStale.includes(id) && !rightsBlocked.includes(id))} : {}),
    collection_completeness: Object.fromEntries(required.map((sourceId) => [sourceId, sourceMap.get(sourceId)?.window_completeness]).filter(([, value]) => value !== undefined)),
    requested_scope: requestedScope,
  };
}

async function queryStore(snapshot, { q: text = null, canonical_id: canonicalId = null, source_id: sourceId = null, change_type: changeType = null, limit = 20, cursor = null, expected_generation: expectedGeneration = null } = {}) {
  if (!Number.isInteger(limit) || limit < 1 || limit > 100) throw new GatewayError("INVALID_ARGUMENTS", "limit must be an integer between 1 and 100");
  for (const [name, value, max] of [["text", text, 512], ["canonical_id", canonicalId, 256], ["source_id", sourceId, 64], ["change_type", changeType, 64]]) {
    if (value !== null && (typeof value !== "string" || value.length > max)) throw new GatewayError("INVALID_ARGUMENTS", `invalid ${name}`);
  }
  if (canonicalId !== null && !canonicalId.trim()) throw new GatewayError("INVALID_ARGUMENTS", "invalid canonical_id");
  const changes = new Set(["NEW", "REVISED", "STATUS_CHANGED", "DEADLINE_CHANGED", "CONFIRMED", "UNCHANGED", "LKG", "REMOVED"]);
  if (changeType && !changes.has(changeType)) throw new GatewayError("INVALID_ARGUMENTS", "unknown change_type");
  if (expectedGeneration !== null && expectedGeneration !== snapshot.generationId) throw new GatewayError("INVALID_ARGUMENTS", "query generation mismatch; retry against the requested snapshot");
  const needle = text ? text.toLocaleLowerCase().trim() : null;
  const filterHash = await sha256(canonicalJson([needle, canonicalId, sourceId, changeType]));
  let offset = 0;
  if (cursor !== null) {
    try {
      if (typeof cursor !== "string" || cursor.length > 1024) throw new Error();
      const token = JSON.parse(new TextDecoder().decode(Uint8Array.from(atob(cursor.replace(/-/g, "+").replace(/_/g, "/")), (char) => char.charCodeAt(0))));
      if (token.generation !== snapshot.generationId || token.filters !== filterHash || !Number.isInteger(token.offset) || token.offset < 0 || token.offset > 10000) throw new Error();
      offset = token.offset;
    } catch { throw new GatewayError("INVALID_ARGUMENTS", "invalid cursor: generation/filter/offset mismatch"); }
  }
  const selected = snapshot.items.filter((row) => (!canonicalId || row.canonical_id === canonicalId) && (!sourceId || row.source_id === sourceId) && (!changeType || row.change_type === changeType) &&
    (!needle || [row.title, row.committee, row.source_id, row.canonical_id].map((value) => String(value || "")).join(" ").toLocaleLowerCase().includes(needle)));
  selected.sort((a, b) => (parseInstant(b.published_at) || -Infinity) - (parseInstant(a.published_at) || -Infinity) || a.canonical_id.localeCompare(b.canonical_id));
  if (offset > selected.length) throw new GatewayError("INVALID_ARGUMENTS", "cursor offset exceeds result set");
  const result = selected.slice(offset, offset + limit);
  const nextCursor = offset + result.length < selected.length
    ? btoa(JSON.stringify({ generation: snapshot.generationId, filters: filterHash, offset: offset + result.length })).replace(/\+/g, "-").replace(/\//g, "_")
    : null;
  const scope = assessScope(snapshot, sourceId);
  return {
    schema_version: 2, query_generation_id: snapshot.generationId,
    canonical_artifact_hashes: { feed: snapshot.generatedFrom.feed_sha256, status: snapshot.generatedFrom.status_sha256, brief: snapshot.generatedFrom.brief_sha256 },
    publication_deployment_verified: false, policy: snapshot.policyBinding,
    query_coverage: queryCoverage(snapshot, "publication_metadata", Object.fromEntries(
      Object.entries({ text, canonical_id: canonicalId, source_id: sourceId, change_type: changeType }).filter(([, value]) => value !== null && value !== undefined),
    )),
    data_status: scope.dataStatus, source_gaps: scope.gaps,
    source_status: snapshot.sources.filter((source) => !sourceId || source.source_id === sourceId),
    answerable_no_match: selected.length === 0 && scope.gaps.length === 0 && queryCoverage(snapshot, "publication_metadata").can_state_bounded_no_match,
    answer_scope: "Matching publication metadata in this indexed snapshot; not all real-world events.",
    queried_at: isoNow(), search_scope: "TITLE_COMMITTEE_SOURCE_ID_ONLY", total_matches: selected.length,
    result_count: result.length, offset, truncated: selected.length > result.length, has_more: offset + result.length < selected.length,
    next_cursor: nextCursor, results: result,
  };
}

function publicSource(row) {
  try { return projectPublicSource(row); }
  catch { throw new GatewayError("PUBLIC_PROJECTION_INVALID", "canonical source violates public projection schema", 503); }
}

const OFFICIAL_EVIDENCE_SOURCE_IDS = new Set(
  sourceCatalog.sources
    .filter((row) => ["PRIMARY_EVENT", "PRIMARY_REFERENCE"].includes(row.role)
      && ["PRODUCTION_ACTIVE", "AUDITED_EXISTING"].includes(row.status))
    .map((row) => row.source_id),
);

function trustedEvidence(snapshot, now = Date.now()) {
  const sourceStatus = Object.fromEntries(snapshot.sources.map((source) => [source.source_id, assessScope(snapshot, source.source_id, now).dataStatus]));
  return snapshot.items.filter((item) =>
    item.source_role === "PRIMARY_OFFICIAL" &&
    OFFICIAL_EVIDENCE_SOURCE_IDS.has(item.source_id) &&
    typeof item.official_url === "string" &&
    item.official_url.startsWith("https://"),
  ).map((item) => {

    const source = snapshot.sources.find((row) => row.source_id === item.source_id) || {};
    const freshnessValue = String(item.freshness_status || source.freshness_status || "UNKNOWN").toUpperCase();
    const current = sourceStatus[item.source_id] === "SNAPSHOT_RECENT" && source.source_health === "PASS" &&
      ["COMPLETE_ZERO", "COMPLETE_WITH_ITEMS"].includes(source.window_completeness) && ["FRESH", "RECENT"].includes(freshnessValue) &&
      // The projected status already resolved the item/source freshness fallback,
      // so the label on the row and this decision cannot drift.
      item.verification_status === "VERIFIED";
    return {
      schema_version: 1, evidence_id: `PUB-${item.canonical_id}`, evidence_type: "WRITTEN_OFFICIAL", source_id: item.source_id,
      locator: `${item.official_url}#publication:${item.canonical_id}`, document_version: item.content_sha256, content_sha256: item.content_sha256,
      trust_tier: item.trust_tier, verification_status: item.verification_status, freshness: freshnessValue, is_current: current,
      published_at: item.published_at ?? null,
      observed_at: item.fetched_at ?? null,
      ...(item.document_revision_at != null ? { document_revision_at: item.document_revision_at } : {}),
      ...(item.date_basis != null ? { date_basis: item.date_basis } : {}),
      assertions: [
        { subject: `publication:${item.canonical_id}:title`, value: item.title },
        { subject: `publication:${item.canonical_id}:source_id`, value: item.source_id },
      ],
    };
  }).sort((a, b) => a.evidence_id.localeCompare(b.evidence_id));
}

export async function validateAnswer(snapshot, claims, gate = gateAnswer) {
  if (snapshot.formalAdmission?.status !== "ADMITTED") throw new GatewayError("RIGHTS_BLOCKED", "Current formal answer evidence lacks approved governance or publication rights", 503);
  if (claims.some(claim => /^publication:.*:(?:title|source_id)$/.test(String(claim?.proposition?.subject || "").normalize("NFC").replace(/\s+/g, "")) && claim.claim_type !== "STATUS")) throw new GatewayError("INVALID_ARGUMENTS", "Publication metadata cannot authorize other factual claim types");
  const evidence = trustedEvidence(snapshot);
  const publicationHash = snapshot.generatedFrom.brief_sha256;
  const evidenceCatalogHash = await sha256(canonicalJson(evidence));
  const result = gate({ claims, evidence, generated_at: snapshot.brief.generated_at });
  const receipt = result?.receipt;
  if (result?.gate_status === "BLOCKED") {
    throw new GatewayError("GATE_FAILED", `answer evidence gate refused the draft: ${receipt?.failure_reason ?? "unknown"}`, 503);
  }
  if (!receipt || typeof receipt !== "object") throw new GatewayError("GATE_FAILED", "answer evidence receipt is missing", 503);
  if (receipt.indexed_evidence_count !== evidence.length) {
    throw new GatewayError("GATE_FAILED", "answer evidence gate did not index the full evidence catalog", 503);
  }
  if (receipt.validator_version !== ANSWER_VALIDATOR_VERSION) throw new GatewayError("GATE_FAILED", "answer evidence validator version is not recognized", 503);
  const { error_detail: _errorDetail, ...publicReceipt } = receipt;
  const answer = (receipt.claims || []).map(controlledText).filter(Boolean);
  return {
    ...result,
    answer,
    final_claims: (receipt.claims || []).map((entry) => ({ claim_id: entry.claim_id, claim_type: entry.claim_type, support_status: entry.support_status, text: controlledText(entry) })),
    receipt: { ...publicReceipt, publication_hash: publicationHash, evidence_catalog_hash: evidenceCatalogHash, renderer_version: "controlled-answer-renderer/1", answer_sha256: await sha256(JSON.stringify(answer)) },
  };
}

function controlledText(entry) {
    const facts = (entry.propositions || []).filter((value) => value && value.subject !== undefined && value.value !== undefined).map((value) => `${String(value.subject)}=${String(value.value)}`);
    if (!["TIME", "LOCATION", "STATUS", "CAUSE", "STATISTIC", "AGENCY"].includes(entry.claim_type) || !facts.length) return null;
    const subjects = facts.map((fact) => fact.split("=")[0]).join("、");
    if (entry.support_status === "CONFLICT") {
      const values = (entry.conflict_values || []).map((value) => `${value.source_id}：${value.value}`);
      return `官方來源對 ${subjects} 記載不一致${values.length ? `：${values.join("；")}` : ""}。`;
    }
    if (entry.support_status === "STALE") return "官方資料可能已過期，未作為目前情況回答。";
    if (entry.support_status === "PARTIAL") return "官方來源僅部分支持，未核對部分不納入回答。";
    if (entry.support_status === "UNSUPPORTED" && entry.claim_type === "CAUSE") return "官方來源未說明原因。";
    if (entry.support_status === "UNSUPPORTED") return "未找到可驗證的官方證據，此項說法已移除。";
    return `官方來源已核對：${facts.join("；")}。`;
}

function envelope(snapshot, tool, args, scope, payload, resultCount = 0, truncated = false, resultType = "publication_metadata") {
  return {
    release: snapshot.release,
    schema_version: 1, query_id: crypto.randomUUID(), tool_name: tool, publication_id: snapshot.generatedFrom.collection_run_id,
    publication_hash: snapshot.generatedFrom.brief_sha256, query_generation_id: snapshot.generationId, generated_at: snapshot.brief.generated_at,
    queried_at: isoNow(), freshness: freshness(scope.dataStatus), verification_summary: verificationSummary(scope.dataStatus),
    event_ids: [], evidence_ids: [], source_gaps: scope.gaps, query_coverage: scope.coverage,
    discovery_unverified_count: 0, truncated, policy: snapshot.policyBinding, retention: retentionPolicy(snapshot), result_type: resultType,
    receipt: { schema_version: 1, tool_name: tool, arguments_sha256: null, publication_hash: snapshot.generatedFrom.brief_sha256, query_generation_id: snapshot.generationId, result_count: resultCount, truncated, server_version: SERVER_VERSION, issued_at: isoNow() },
    ...payload,
  };
}

async function execute(snapshot, tool, rawArgs = {}) {
  if (!rawArgs || typeof rawArgs !== "object" || Array.isArray(rawArgs)) throw new GatewayError("INVALID_ARGUMENTS", "arguments must be an object");
  const allowed = {
    search_evidence: ["q", "canonical_id", "source_id", "change_type", "limit", "cursor", "expected_generation"],
    get_current_brief: [], get_publication_receipt: [], get_source_health: ["source_id"], validate_answer: ["claims", "expected_generation"],
  }[tool];
  if (!allowed) throw new GatewayError("CAPABILITY_NOT_AVAILABLE", `${tool} is not implemented; available capabilities: search_evidence, get_current_brief, get_publication_receipt, get_source_health, validate_answer`, 422);
  const unknown = Object.keys(rawArgs).filter((key) => !allowed.includes(key));
  if (unknown.length) throw new GatewayError("INVALID_ARGUMENTS", `unsupported argument(s): ${unknown.sort().join(", ")}`);
  const args = { ...rawArgs };
  if (tool === "search_evidence") {
    const result = await queryStore(snapshot, args);
    const scope = { dataStatus: result.data_status, gaps: result.source_gaps, coverage: result.query_coverage };
    const argumentsHash = await sha256(canonicalJson(args));
    return { ...envelope(snapshot, tool, args, scope, { search_scope: result.search_scope, answerable_no_match: result.answerable_no_match, total_matches: result.total_matches, result_count: result.result_count, offset: result.offset, has_more: result.has_more, next_cursor: result.next_cursor, results: result.results }, result.result_count, result.truncated), receipt: { ...envelope(snapshot, tool, args, scope, {}, result.result_count, result.truncated).receipt, arguments_sha256: argumentsHash } };
  }
  const scopeRaw = assessScope(snapshot, args.source_id);
  const scope = { ...scopeRaw, coverage: queryCoverage(snapshot, tool === "get_source_health" ? "source_health" : "publication_metadata", args.source_id ? { source_id: args.source_id } : {}) };
  const argumentsHash = await sha256(canonicalJson(args));
  if (tool === "get_current_brief") {
    if (snapshot.formalAdmission.status !== "ADMITTED") throw new GatewayError("RIGHTS_BLOCKED", "Current formal brief lacks approved governance or publication rights", 503);
    let brief;
    try { brief = projectPublicBrief(snapshot.brief); }
    catch { throw new GatewayError("PUBLIC_PROJECTION_INVALID", "canonical brief violates public projection schema", 503); }
    if (!briefAdmission(snapshot.policy, brief)) throw new GatewayError("RIGHTS_BLOCKED", "Current derived brief lacks reviewed summary fields and source permissions", 503);
    return { ...envelope(snapshot, tool, args, scope, { current_as_of_server_clock: scope.dataStatus === "SNAPSHOT_RECENT", brief }, 1), receipt: { ...envelope(snapshot, tool, args, scope, {}, 1).receipt, arguments_sha256: argumentsHash } };
  }
  if (tool === "get_publication_receipt") {
    const publication = snapshot.generatedFrom;
    const publicationReceipt = { schema_version: 1, receipt_type: "PUBLICATION_PROJECTION", publication_id: publication.collection_run_id, publication_hash: publication.brief_sha256, generation_id: snapshot.generationId, artifact_hashes: { feed: publication.feed_sha256, status: publication.status_sha256, brief: publication.brief_sha256 }, generated_at: publication.brief_generated_at, collection_status: publication.collection_status, publication_status: publication.publication_status, snapshot_complete: publication.snapshot_complete, freshness: freshness(scope.dataStatus), current_as_of_server_clock: scope.dataStatus === "SNAPSHOT_RECENT", source_gaps: scope.gaps, policy_hash: snapshot.policyBinding.policy_hash };
    return { ...envelope(snapshot, tool, args, scope, { publication_receipt: publicationReceipt }, 1, false, "publication_receipt"), receipt: { ...envelope(snapshot, tool, args, scope, {}, 1).receipt, arguments_sha256: argumentsHash } };
  }
  if (tool === "get_source_health") {
    const selected = snapshot.status.sources.filter((row) => !args.source_id || row.source_id === args.source_id).map(publicSource);
    const sourceRights = selected.map(row => { const rights = retentionMatrix.classes[retentionMatrix.source_classes[row.source_id]];
      return {source_id: row.source_id, rights_status: rights.rights_status, review_required: rights.review_required}; });
    return { ...envelope(snapshot, tool, args, scope, { sources: selected, source_rights: sourceRights,
      formal_admission: snapshot.formalAdmission }, selected.length), receipt: { ...envelope(snapshot, tool, args, scope, {}, selected.length).receipt, arguments_sha256: argumentsHash } };
  }
  if (!Array.isArray(args.claims) || args.claims.length < 1 || args.claims.length > 32 || (args.claims.some((claim) => !claim || typeof claim !== "object" || Object.keys(claim).some((key) => !["schema_version", "claim_id", "text", "claim_type", "temporal_scope", "proposition", "cited_evidence_ids"].includes(key))))) throw new GatewayError("INVALID_ARGUMENTS", "validate_answer requires 1 to 32 structured claims");
  if (args.expected_generation && args.expected_generation !== snapshot.generationId) throw new GatewayError("INVALID_ARGUMENTS", "query generation mismatch; retry against the requested snapshot");
  const gate = await validateAnswer(snapshot, args.claims);
  return { ...envelope(snapshot, tool, args, scope, { gate_status: gate.gate_status, answer: gate.answer, final_claims: gate.final_claims, evidence_ids: gate.receipt.evidence_ids || [], answer_evidence_receipt: gate.receipt }, gate.final_claims?.length || 0, false, "answer_evidence"), receipt: { ...envelope(snapshot, tool, args, scope, {}, gate.final_claims?.length || 0).receipt, arguments_sha256: argumentsHash } };
}

// Metadata uses the unchanged formal answer gate. Optional document research
// uses separately pinned server evidence and never enters that formal gate.
export async function executeResearch(snapshot, rawInput, env, request, fetchImpl = fetch) {
  const input = parseResearchInput(rawInput);
  const scope = assessScope(snapshot);
  const state = researchProviderState(env);
  const base = { schema_version: 1, mode: input.mode, release: snapshot.release, status: "METADATA_ONLY", reason_code: null,
    provider: { name: "MiniMax", state }, question: input.question, answer: [], sources: [],
    source_gaps: scope.gaps, query_generation_id: snapshot.generationId, data_status: scope.dataStatus,
    coverage_limitation: "僅檢索核准快照的標題、議會欄位及來源代碼，不是全文研究；引用僅支持索引記載，不證明標題中的事件事實。零結果不表示現實中沒有事件。" };
  if (snapshot.formalAdmission?.status !== "ADMITTED") return { ...base, reason_code: "RIGHTS_BLOCKED", ...(input.mode !== "metadata" ? { provider_transmission_attempted: false } : {}) };
  if (input.mode !== "metadata") return executeDocumentResearch(snapshot, input, env, request, base, fetchImpl);
  const terms = researchTerms(input);
  const candidates = new Map();
  for (const term of terms) {
    const found = await queryStore(snapshot, { q: term, limit: 6 });
    for (const row of found.results) candidates.set(row.canonical_id, row);
  }
  const rows = [...candidates.values()].sort((a, b) => {
    const score = row => terms.filter(term => String(row.title || "").toLowerCase().includes(term)).length;
    return score(b) - score(a) || a.canonical_id.localeCompare(b.canonical_id);
  }).slice(0, 6);
  const source = row => ({ evidence_id: `PUB-${row.canonical_id}`, source_id: row.source_id,
    title: row.title, official_url: row.official_url, published_at: row.published_at,
    data_as_of: row.data_as_of, fetched_at: row.fetched_at });
  const result = { ...base, sources: rows.map(source) };
  if (!rows.length) return { ...result, reason_code: "NO_MATCH" };
  if (state !== "READY") return { ...result, reason_code: state };
  const trusted = new Set(trustedEvidence(snapshot).filter(row => row.is_current).map(row => row.evidence_id));
  const approved = rows.filter(row => trusted.has(`PUB-${row.canonical_id}`));
  if (!approved.length) return { ...result, reason_code: "NO_APPROVED_SOURCES" };
  try {
    const ids = await selectResearchSources(input, approved.map(source), env, request, fetchImpl);
    if (!ids.length) return { ...result, reason_code: "NO_RELEVANT_SELECTION" };
    const selected = ids.map(id => approved.find(row => `PUB-${row.canonical_id}` === id));
    const claims = selected.map((row, index) => ({ schema_version: 1, claim_id: `research-${index + 1}`,
      text: row.title, claim_type: "STATUS", temporal_scope: "CURRENT",
      proposition: { subject: `publication:${row.canonical_id}:title`, value: row.title }, cited_evidence_ids: [`PUB-${row.canonical_id}`] }));
    const checked = await validateAnswer(snapshot, claims);
    if (checked.gate_status !== "PASS" || checked.final_claims.some(claim => claim.support_status !== "SUPPORTED")) return { ...result, reason_code: "OUTPUT_REJECTED" };
    return { ...result, status: "ANSWER_READY", sources: selected.map(source), answer: checked.answer,
      answer_evidence_receipt: checked.receipt, reason_code: null };
  } catch (error) {
    return { ...result, reason_code: error instanceof ResearchError ? error.code : "OUTPUT_REJECTED" };
  }
}

class GatewayError extends Error {
  constructor(code, message, status = 400) { super(message); this.code = code; this.status = status; }
}

// Publication artifacts that are present but mutually inconsistent: the index
// cannot be rebuilt, and answering from an older generation would hide it.
class SnapshotIntegrityError extends Error {}

function mcpTools() {
  const readonly = { readOnlyHint: true, openWorldHint: false, destructiveHint: false };
  return [
    { name: "search_evidence", description: "Search approved publication metadata; this is not full-text or PublicEvent search.", inputSchema: { type: "object", additionalProperties: false, properties: { q: { type: "string", maxLength: 512 }, canonical_id: { type: "string", maxLength: 256 }, source_id: { type: "string", maxLength: 64 }, change_type: { type: "string", maxLength: 64 }, limit: { type: "integer", minimum: 1, maximum: 100 }, cursor: { type: "string", maxLength: 1024 }, expected_generation: { type: "string", maxLength: 128 } } }, annotations: readonly },
    { name: "get_current_brief", description: "Read the checked-in canonical brief with its freshness and publication receipt.", inputSchema: { type: "object", additionalProperties: false, properties: {} }, annotations: readonly },
    { name: "get_publication_receipt", description: "Read the current publication and canonical artifact hashes without exposing raw content.", inputSchema: { type: "object", additionalProperties: false, properties: {} }, annotations: readonly },
    { name: "get_source_health", description: "Read approved source health, freshness, completeness, and gaps.", inputSchema: { type: "object", additionalProperties: false, properties: { source_id: { type: "string", maxLength: 64 } } }, annotations: readonly },
    { name: "validate_answer", description: "Validate structured answer claims against the server-controlled canonical evidence catalog.", inputSchema: { type: "object", additionalProperties: false, properties: { claims: { type: "array", maxItems: 32, items: { type: "object" } }, expected_generation: { type: "string", maxLength: 128 } }, required: ["claims"] }, annotations: readonly },
  ];
}

async function dispatchMcp(snapshot, request) {
  if (request?.jsonrpc !== "2.0" || !("id" in (request || {}))) throw new GatewayError("INVALID_JSON_RPC", "request must be JSON-RPC 2.0 with an id");
  if (request.method === "initialize") return { jsonrpc: "2.0", id: request.id, result: { protocolVersion: MCP_PROTOCOL_VERSION, capabilities: { tools: { listChanged: false } }, serverInfo: { name: "govintel-query-gateway", version: SERVER_VERSION }, release: snapshot.release } };
  if (request.method === "tools/list") return { jsonrpc: "2.0", id: request.id, result: { tools: mcpTools(), release: snapshot.release } };
  if (request.method !== "tools/call" || !request.params || typeof request.params.name !== "string") throw new GatewayError("METHOD_NOT_FOUND", `unsupported MCP method: ${request.method}`, 404);
  try {
    const payload = await execute(snapshot, request.params.name, request.params.arguments);
    return { jsonrpc: "2.0", id: request.id, result: { isError: false, content: [{ type: "text", text: JSON.stringify(payload) }], structuredContent: payload } };
  } catch (error) {
    if (!(error instanceof GatewayError)) console.error("mcp dispatch failed");
    const gatewayError = error instanceof GatewayError ? error : new GatewayError("INVALID_ARGUMENTS", "request could not be processed");
    return { jsonrpc: "2.0", id: request.id, result: { isError: true, content: [{ type: "text", text: JSON.stringify(jsonError(gatewayError.code, gatewayError.message)) }] } };
  }
}

function originAllowed(request, env) {
  const origin = request.headers.get("Origin");
  if (!origin) return { origin: null, allowed: true };
  const allowed = new Set(String(env.ALLOWED_ORIGINS || "").split(",").map((value) => value.trim()).filter(Boolean));
  return { origin, allowed: allowed.has(origin) };
}

function corsHeaders(request, env) {
  const { origin } = originAllowed(request, env);
  return origin ? { "Access-Control-Allow-Origin": origin, "Vary": "Origin" } : {};
}

function responseJson(payload, status, request, env) {
  const body = JSON.stringify(payload);
  if (new TextEncoder().encode(body).byteLength > MAX_RESPONSE_BYTES) return new Response(JSON.stringify(jsonError("RESPONSE_TOO_LARGE", "response exceeds byte budget")), { status: 500, headers: { "Content-Type": "application/json", ...corsHeaders(request, env) } });
  return new Response(body, { status, headers: { "Content-Type": "application/json; charset=utf-8", "Cache-Control": "no-store", ...corsHeaders(request, env) } });
}

function limited(request) {
  const key = request.headers.get("CF-Connecting-IP") || request.headers.get("X-Forwarded-For") || "anonymous";
  const now = Date.now();
  const current = rateWindows.get(key);
  if (!current || now - current.startedAt >= 60_000) { rateWindows.set(key, { startedAt: now, count: 1 }); return false; }
  current.count += 1;
  return current.count > MAX_RATE;
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    const originState = originAllowed(request, env);
    if (!originState.allowed) return responseJson(jsonError("ORIGIN_NOT_ALLOWED", "browser origin is not allowed"), 403, request, env);
    if (request.method === "OPTIONS") return new Response(null, { status: 204, headers: { ...corsHeaders(request, env), "Access-Control-Allow-Methods": "GET,POST,OPTIONS", "Access-Control-Allow-Headers": "Content-Type", "Access-Control-Max-Age": "600" } });
    if (limited(request)) return responseJson(jsonError("RATE_LIMITED", "request rate limit exceeded"), 429, request, env);
    try {
      const snapshot = await getSnapshot(env);
      if (request.method === "GET" && url.pathname === "/health") {
        const scope = assessScope(snapshot);
        return responseJson({ schema_version: 1, service: "govintel-query-gateway", server_version: SERVER_VERSION, status: snapshot.degradedSince ? "degraded" : "ok", release: snapshot.release, publication_freshness: freshness(scope.dataStatus), publication_id: snapshot.generatedFrom.collection_run_id, publication_hash: snapshot.generatedFrom.brief_sha256, query_coverage: queryCoverage(snapshot, "publication_metadata"), policy: snapshot.policyBinding, retention: retentionPolicy(snapshot), source_gaps: scope.gaps, read_only: true }, 200, request, env);

      }
      if (request.method === "GET" && url.pathname === "/capabilities") return responseJson({ schema_version: 1, server_version: SERVER_VERSION, read_only: true, release: snapshot.release, capabilities: ["search_evidence", "get_current_brief", "get_publication_receipt", "get_source_health", "validate_answer"], unavailable_capabilities: DOMAIN_CAPABILITIES, policy: snapshot.policyBinding, retention: retentionPolicy(snapshot) }, 200, request, env);
      if (request.method !== "POST" || !["/query", "/mcp", "/research"].includes(url.pathname)) return responseJson(jsonError("NOT_FOUND", "route not found"), 404, request, env);
      if (url.pathname === "/research") {
        const input = await readBoundedJson(request, 8192);
        if (typeof input?.release_id !== "string" || input.release_id !== snapshot.release.release_id) throw new GatewayError("QUERY_TEMPORARILY_UNAVAILABLE", "Research release mismatch; reload the publication", 503);
        return responseJson(await executeResearch(snapshot, input, env, request), 200, request, env);
      }
      const bytes = await request.arrayBuffer();
      if (bytes.byteLength > MAX_REQUEST_BYTES) return responseJson(jsonError("REQUEST_TOO_LARGE", "request exceeds byte budget"), 413, request, env);
      const input = JSON.parse(new TextDecoder().decode(bytes));
      if (input?.release_id !== undefined && input.release_id !== snapshot.release.release_id) throw new GatewayError("QUERY_TEMPORARILY_UNAVAILABLE", "Pages and Worker release mismatch; reload the publication", 503);
      if (url.pathname === "/query") return responseJson(await execute(snapshot, input?.tool, input?.arguments), 200, request, env);
      return responseJson(await dispatchMcp(snapshot, input), 200, request, env);
    } catch (error) {
      if (!(error instanceof GatewayError)) console.error("gateway request failed");
      const gatewayError = error instanceof GatewayError || error instanceof ResearchError ? error : new GatewayError("UPSTREAM_UNAVAILABLE", "upstream publication is unavailable", 503);
      return responseJson(jsonError(gatewayError.code, gatewayError.message), gatewayError.status, request, env);
    }
  },
};
