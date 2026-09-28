import { gateAnswer } from "../../../apps/web/lib/answer-evidence-gate.js";
import sourceCatalog from "../../../docs/govintel/source-catalog.v2.json" with { type: "json" };

const MAX_REQUEST_BYTES = 64 * 1024;
const MAX_RESPONSE_BYTES = 256 * 1024;
const MAX_SNAPSHOT_AGE_MS = 16 * 60 * 60 * 1000;
const SERVER_VERSION = "query-gateway-v1-workers";
const MCP_PROTOCOL_VERSION = "2025-06-18";
const PUBLIC_PROJECTION = "METADATA_LINK_ONLY";
const RETENTION_POLICY = {
  policy_version: 1,
  policy_hash: "aa2f3f22f49927c673866c8c3ebdea816150fe60b03822cde4ff0bd104f87034",
  public_projection: PUBLIC_PROJECTION,
  full_text_allowed: false,
};
const MAX_RATE = 60;
const rateWindows = new Map();
let snapshotCache = null;

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

function projectFeedItem(item, feedHash) {
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
  return {
    record_type: "publication_item",
    canonical_id: item.stable_id,
    title: item.title,
    source_id: item.source_id,
    official_url: item.official_url ?? null,
    published_at: item.published_at ?? null,
    data_as_of: item.data_as_of ?? null,
    fetched_at: item.fetched_at ?? null,
    change_type: item.change_type ?? null,
    freshness_status: item.freshness_status ?? null,
    source_health: item.source_health ?? null,
    window_completeness: item.window_completeness ?? null,
    committee: String(item.committee || ""),
    next_milestone: item.next_milestone ?? null,
    evidence_count: Number.isInteger(item.evidence_count) && item.evidence_count >= 0 ? item.evidence_count : 0,
    content_sha256: item.content_sha256,
    trust_tier: "CANONICAL_PUBLICATION",
    canonical_ref: { artifact: "intelligence-feed.json", artifact_sha256: feedHash, stable_id: item.stable_id },
  };
}

function projectSource(source, statusHash) {
  if (!source || typeof source.source_id !== "string" || typeof source.source_name !== "string") throw new Error("invalid source row");
  return {
    source_id: source.source_id,
    name: source.source_name,
    source_health: source.source_health ?? null,
    window_completeness: source.window_completeness ?? null,
    result: source.result ?? null,
    last_checked_at: source.last_checked_at ?? null,
    data_as_of: source.data_as_of ?? null,
    freshness_status: source.freshness_status ?? null,
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

function validatePolicy(policy) {
  if (!policy || policy.schema_version !== 1 || !Array.isArray(policy.active_source_ids) || !policy.active_source_ids.length) throw new Error("invalid source policy");
  const ids = [...policy.active_source_ids].sort();
  if (JSON.stringify(ids) !== JSON.stringify(policy.active_source_ids) || new Set(ids).size !== ids.length) throw new Error("source policy IDs must be sorted and unique");
  if (sourceCatalog.sources.filter((row) => ids.includes(row.source_id) && row.status === "PRODUCTION_ACTIVE").length !== ids.length) throw new Error("source policy does not match catalog");
  assertHash(policy.policy_hash, "policy_hash");
  assertHash(policy.catalog_hash, "catalog_hash");
}

async function fetchJson(origin, name) {
  const url = `${origin.replace(/\/$/, "")}/data/${name}`;
  const response = await fetch(url, { cf: { cacheTtl: 30, cacheEverything: true } });
  if (!response.ok) throw new Error(`publication artifact ${name} returned HTTP ${response.status}`);
  const bytes = await response.arrayBuffer();
  if (bytes.byteLength > 32 * 1024 * 1024) throw new Error(`publication artifact ${name} exceeds byte budget`);
  return { value: JSON.parse(new TextDecoder().decode(bytes)), hash: await sha256(bytes) };
}

async function buildSnapshot(env) {
  const origin = env.PUBLIC_ORIGIN;
  if (!origin) throw new Error("PUBLIC_ORIGIN is not configured");
  const [feedDoc, statusDoc, briefDoc, policyDoc] = await Promise.all([
    fetchJson(origin, "intelligence-feed.json"),
    fetchJson(origin, "source-status.json"),
    fetchJson(origin, "v2-daily-brief.json"),
    fetchJson(origin, "source-policy.json"),
  ]);
  const feed = feedDoc.value;
  const status = statusDoc.value;
  const brief = briefDoc.value;
  const policy = policyDoc.value;
  if (![feed, status, brief].every((value) => value?.schema_version === 1)) throw new Error("unsupported publication schema");
  validatePolicy(policy);
  const run = feed.collection_run_id;
  if (!run || run !== status.latest_collection_run?.collection_run_id || run !== brief.source_collection_run_id ||
      feed.generated_at !== status.generated_at || status.generated_at !== brief.source_status_generated_at) {
    throw new Error("cross-generation publication artifacts");
  }
  for (const value of [feed.generated_at, status.generated_at, brief.generated_at]) {
    if (!Number.isFinite(parseInstant(value))) throw new Error("publication timestamp is invalid");
  }
  if (!Array.isArray(feed.items) || feed.items.length > 10000 || !Array.isArray(status.sources)) throw new Error("publication arrays are invalid");
  const items = feed.items.map((item) => projectFeedItem(item, feedDoc.hash)).sort((a, b) => a.canonical_id.localeCompare(b.canonical_id));
  if (new Set(items.map((item) => item.canonical_id)).size !== items.length) throw new Error("duplicate canonical_id");
  const sources = status.sources.map((source) => projectSource(source, statusDoc.hash)).sort((a, b) => a.source_id.localeCompare(b.source_id));
  const active = [...policy.active_source_ids].sort();
  if (JSON.stringify(sources.map((source) => source.source_id)) !== JSON.stringify(active)) throw new Error("source coverage does not match policy");
  if (items.some((item) => !active.includes(item.source_id))) throw new Error("feed references an unapproved source");
  const policyBinding = {
    policy_version: policy.policy_version,
    policy_hash: policy.policy_hash,
    catalog_hash: policy.catalog_hash,
    active_source_ids: active,
  };
  const artifactHashes = { feed: feedDoc.hash, status: statusDoc.hash, brief: briefDoc.hash };
  const material = { schema_version: 2, projection_version: "publication-metadata-v2", artifact_hashes: artifactHashes, policy: policyBinding };
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
  return { feed, status, brief, policy, policyBinding, capabilityDefinitions: makeCapabilities(policy), items, sources, generatedFrom, generationId };
}

async function getSnapshot(env) {
  const now = Date.now();
  if (snapshotCache && snapshotCache.expiresAt > now) return snapshotCache.value;
  const value = buildSnapshot(env);
  snapshotCache = { value, expiresAt: now + 30_000 };
  try { return await value; } catch (error) { snapshotCache = null; throw error; }
}

function assessScope(snapshot, sourceId, now = Date.now()) {
  const selected = snapshot.sources.filter((source) => !sourceId || source.source_id === sourceId);
  if (!selected.length) return { dataStatus: "SOURCE_NOT_AVAILABLE", gaps: [{ source_id: sourceId, reason: "NOT_IN_APPROVED_SNAPSHOT" }] };
  const gaps = [];
  const meta = snapshot.generatedFrom;
  if (meta.collection_status !== "SUCCEEDED" || meta.publication_status !== "READY" || meta.snapshot_complete !== true) gaps.push({ source_id: null, reason: "INCOMPLETE_PUBLICATION" });
  const stamps = [meta.feed_generated_at, meta.status_generated_at, meta.brief_generated_at].map(parseInstant);
  if (stamps.some((stamp) => !Number.isFinite(stamp))) gaps.push({ source_id: null, reason: "UNKNOWN_PUBLICATION_TIME" });
  else if (stamps.some((stamp) => stamp > now)) gaps.push({ source_id: null, reason: "FUTURE_PUBLICATION_TIME" });
  else if (now - Math.min(...stamps) > MAX_SNAPSHOT_AGE_MS) gaps.push({ source_id: null, reason: "STALE_SNAPSHOT" });
  for (const source of selected) {
    if (source.source_health !== "PASS" || !["COMPLETE_ZERO", "COMPLETE_WITH_ITEMS"].includes(source.window_completeness)) gaps.push({ source_id: source.source_id, reason: "SOURCE_INCOMPLETE", source_health: source.source_health });
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
    if (source.source_health !== "PASS" || !["COMPLETE_ZERO", "COMPLETE_WITH_ITEMS"].includes(source.window_completeness)) missing.push(sourceId);
    const state = String(source.freshness_status || "UNKNOWN").toUpperCase();
    if (["STALE", "VERY_STALE"].includes(state)) stale.push(sourceId);
    else if (!["FRESH", "RECENT"].includes(state)) missing.push(sourceId);
  }
  const uniqueMissing = [...new Set(missing)].sort();
  const uniqueStale = [...new Set(stale)].sort();
  const status = uniqueMissing.length ? "PARTIAL" : uniqueStale.length ? "STALE" : "COVERED_BOUNDED_SCOPE";
  return {
    status, policy_version: snapshot.policyBinding.policy_version, policy_hash: snapshot.policyBinding.policy_hash, capability_id: capabilityId,
    required_sources: required, missing_required_sources: uniqueMissing, stale_required_sources: uniqueStale,
    can_state_bounded_no_match: status === "COVERED_BOUNDED_SCOPE", coverage_limitations: [definition.coverage_limitation],
    supported_capabilities: supportedCapabilities, covered_sources: required.filter((sourceId) => !uniqueMissing.includes(sourceId) && !uniqueStale.includes(sourceId)),
    collection_completeness: Object.fromEntries(required.map((sourceId) => [sourceId, sourceMap.get(sourceId)?.window_completeness]).filter(([, value]) => value !== undefined)),
    requested_scope: requestedScope,
  };
}

async function queryStore(snapshot, { text = null, source_id: sourceId = null, change_type: changeType = null, limit = 20, cursor = null, expected_generation: expectedGeneration = null } = {}) {
  if (!Number.isInteger(limit) || limit < 1 || limit > 100) throw new GatewayError("INVALID_ARGUMENTS", "limit must be an integer between 1 and 100");
  for (const [name, value, max] of [["text", text, 512], ["source_id", sourceId, 64], ["change_type", changeType, 64]]) {
    if (value !== null && (typeof value !== "string" || value.length > max)) throw new GatewayError("INVALID_ARGUMENTS", `invalid ${name}`);
  }
  const changes = new Set(["NEW", "REVISED", "STATUS_CHANGED", "DEADLINE_CHANGED", "CONFIRMED", "UNCHANGED", "LKG", "REMOVED"]);
  if (changeType && !changes.has(changeType)) throw new GatewayError("INVALID_ARGUMENTS", "unknown change_type");
  if (expectedGeneration !== null && expectedGeneration !== snapshot.generationId) throw new GatewayError("INVALID_ARGUMENTS", "query generation mismatch; retry against the requested snapshot");
  const needle = text ? text.toLocaleLowerCase().trim() : null;
  const filterHash = await sha256(canonicalJson([needle, sourceId, changeType]));
  let offset = 0;
  if (cursor !== null) {
    try {
      if (typeof cursor !== "string" || cursor.length > 1024) throw new Error();
      const token = JSON.parse(new TextDecoder().decode(Uint8Array.from(atob(cursor.replace(/-/g, "+").replace(/_/g, "/")), (char) => char.charCodeAt(0))));
      if (token.generation !== snapshot.generationId || token.filters !== filterHash || !Number.isInteger(token.offset) || token.offset < 0 || token.offset > 10000) throw new Error();
      offset = token.offset;
    } catch { throw new GatewayError("INVALID_ARGUMENTS", "invalid cursor: generation/filter/offset mismatch"); }
  }
  const selected = snapshot.items.filter((row) => (!sourceId || row.source_id === sourceId) && (!changeType || row.change_type === changeType) &&
    (!needle || [row.title, row.committee, row.source_id, row.canonical_id].map((value) => String(value || "")).join(" ").toLocaleLowerCase().includes(needle)));
  selected.sort((a, b) => (parseInstant(b.published_at) || -Infinity) - (parseInstant(a.published_at) || -Infinity) || a.canonical_id.localeCompare(b.canonical_id));
  const result = selected.slice(offset, offset + limit);
  const nextCursor = offset + result.length < selected.length
    ? btoa(JSON.stringify({ generation: snapshot.generationId, filters: filterHash, offset: offset + result.length })).replace(/\+/g, "-").replace(/\//g, "_")
    : null;
  const scope = assessScope(snapshot, sourceId);
  return {
    schema_version: 2, query_generation_id: snapshot.generationId,
    canonical_artifact_hashes: { feed: snapshot.generatedFrom.feed_sha256, status: snapshot.generatedFrom.status_sha256, brief: snapshot.generatedFrom.brief_sha256 },
    publication_deployment_verified: false, policy: snapshot.policyBinding,
    query_coverage: queryCoverage(snapshot, "publication_metadata", { text, source_id: sourceId, change_type: changeType }),
    data_status: scope.dataStatus, source_gaps: scope.gaps,
    source_status: snapshot.sources.filter((source) => !sourceId || source.source_id === sourceId),
    answerable_no_match: selected.length === 0 && scope.gaps.length === 0,
    answer_scope: "Matching publication metadata in this indexed snapshot; not all real-world events.",
    queried_at: isoNow(), search_scope: "TITLE_COMMITTEE_SOURCE_ID_ONLY", total_matches: selected.length,
    result_count: result.length, offset, truncated: selected.length > result.length, has_more: offset + result.length < selected.length,
    next_cursor: nextCursor, results: result,
  };
}

function publicSource(row) {
  const fields = ["source_id", "source_name", "source_url", "source_health", "window_completeness", "result", "freshness_status", "data_as_of", "last_checked_at", "last_success_at", "intelligence_gaps", "current_source_run_id", "manifest_sha256"];
  return Object.fromEntries(fields.filter((key) => key in row).map((key) => [key, row[key]]));
}

function trustedEvidence(snapshot) {
  const sourceStatus = Object.fromEntries(snapshot.sources.map((source) => [source.source_id, assessScope(snapshot, source.source_id).dataStatus]));
  return snapshot.items.filter((item) => typeof item.official_url === "string" && item.official_url.startsWith("https://")).map((item) => {
    const source = snapshot.sources.find((row) => row.source_id === item.source_id) || {};
    const freshnessValue = String(item.freshness_status || source.freshness_status || "UNKNOWN").toUpperCase();
    const current = sourceStatus[item.source_id] === "SNAPSHOT_RECENT" && source.source_health === "PASS" &&
      ["COMPLETE_ZERO", "COMPLETE_WITH_ITEMS"].includes(source.window_completeness) && ["FRESH", "RECENT"].includes(freshnessValue);
    return {
      schema_version: 1, evidence_id: `PUB-${item.canonical_id}`, evidence_type: "WRITTEN_OFFICIAL", source_id: item.source_id,
      locator: `${item.official_url}#publication:${item.canonical_id}`, document_version: item.content_sha256, content_sha256: item.content_sha256,
      trust_tier: item.trust_tier, verification_status: "CONFIRMED_OFFICIAL", freshness: freshnessValue, is_current: current,
      published_at: item.published_at || item.data_as_of || item.fetched_at,
      assertions: [
        { subject: `publication:${item.canonical_id}:title`, value: item.title },
        { subject: `publication:${item.canonical_id}:source_id`, value: item.source_id },
      ],
    };
  }).sort((a, b) => a.evidence_id.localeCompare(b.evidence_id));
}

async function validateAnswer(snapshot, claims) {
  const evidence = trustedEvidence(snapshot);
  const publicationHash = snapshot.generatedFrom.brief_sha256;
  const evidenceCatalogHash = await sha256(canonicalJson(evidence));
  const result = gateAnswer({ claims, evidence, generated_at: snapshot.brief.generated_at });
  const receipt = result.receipt;
  if (!receipt || typeof receipt !== "object") throw new GatewayError("GATE_FAILED", "answer evidence receipt is missing", 503);
  const answer = (receipt.claims || []).map(controlledText).filter(Boolean);
  return {
    ...result,
    answer,
    final_claims: (receipt.claims || []).map((entry) => ({ claim_id: entry.claim_id, claim_type: entry.claim_type, support_status: entry.support_status, text: controlledText(entry) })),
    receipt: { ...receipt, publication_hash: publicationHash, evidence_catalog_hash: evidenceCatalogHash, renderer_version: "controlled-answer-renderer/1", answer_sha256: await sha256(JSON.stringify(answer)) },
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
    if (entry.support_status === "UNSUPPORTED") return "未找到可驗證的官方證據，此項說法已移除。";
    return `官方來源已核對：${facts.join("；")}。`;
}

function envelope(snapshot, tool, args, scope, payload, resultCount = 0, truncated = false, resultType = "publication_metadata") {
  return {
    schema_version: 1, query_id: crypto.randomUUID(), tool_name: tool, publication_id: snapshot.generatedFrom.collection_run_id,
    publication_hash: snapshot.generatedFrom.brief_sha256, query_generation_id: snapshot.generationId, generated_at: snapshot.brief.generated_at,
    queried_at: isoNow(), freshness: freshness(scope.dataStatus), verification_summary: verificationSummary(scope.dataStatus),
    event_ids: [], evidence_ids: [], source_gaps: scope.gaps, query_coverage: scope.coverage,
    discovery_unverified_count: 0, truncated, policy: snapshot.policyBinding, retention: RETENTION_POLICY, result_type: resultType,
    receipt: { schema_version: 1, tool_name: tool, arguments_sha256: null, publication_hash: snapshot.generatedFrom.brief_sha256, query_generation_id: snapshot.generationId, result_count: resultCount, truncated, server_version: SERVER_VERSION, issued_at: isoNow() },
    ...payload,
  };
}

async function execute(snapshot, tool, rawArgs = {}) {
  if (!rawArgs || typeof rawArgs !== "object" || Array.isArray(rawArgs)) throw new GatewayError("INVALID_ARGUMENTS", "arguments must be an object");
  const allowed = {
    search_evidence: ["q", "source_id", "change_type", "limit", "cursor", "expected_generation"],
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
    const allowedBrief = ["schema_version", "mode", "generator_version", "generated_at", "source_collection_run_id", "source_status_generated_at", "publication_status", "snapshot_complete", "status_message", "overview", "priority_items", "tracking_items", "other_changes", "source_health"];
    const brief = Object.fromEntries(allowedBrief.filter((key) => key in snapshot.brief).map((key) => [key, snapshot.brief[key]]));
    return { ...envelope(snapshot, tool, args, scope, { current_as_of_server_clock: scope.dataStatus === "SNAPSHOT_RECENT", brief }, 1), receipt: { ...envelope(snapshot, tool, args, scope, {}, 1).receipt, arguments_sha256: argumentsHash } };
  }
  if (tool === "get_publication_receipt") {
    const publication = snapshot.generatedFrom;
    const publicationReceipt = { schema_version: 1, receipt_type: "PUBLICATION_PROJECTION", publication_id: publication.collection_run_id, publication_hash: publication.brief_sha256, generation_id: snapshot.generationId, artifact_hashes: { feed: publication.feed_sha256, status: publication.status_sha256, brief: publication.brief_sha256 }, generated_at: publication.brief_generated_at, collection_status: publication.collection_status, publication_status: publication.publication_status, snapshot_complete: publication.snapshot_complete, freshness: freshness(scope.dataStatus), current_as_of_server_clock: scope.dataStatus === "SNAPSHOT_RECENT", source_gaps: scope.gaps, policy_hash: snapshot.policyBinding.policy_hash };
    return { ...envelope(snapshot, tool, args, scope, { publication_receipt: publicationReceipt }, 1, false, "publication_receipt"), receipt: { ...envelope(snapshot, tool, args, scope, {}, 1).receipt, arguments_sha256: argumentsHash } };
  }
  if (tool === "get_source_health") {
    const selected = snapshot.status.sources.filter((row) => !args.source_id || row.source_id === args.source_id).map(publicSource);
    return { ...envelope(snapshot, tool, args, scope, { sources: selected }, selected.length), receipt: { ...envelope(snapshot, tool, args, scope, {}, selected.length).receipt, arguments_sha256: argumentsHash } };
  }
  if (!Array.isArray(args.claims) || args.claims.length < 1 || args.claims.length > 32 || (args.claims.some((claim) => !claim || typeof claim !== "object" || Object.keys(claim).some((key) => !["schema_version", "claim_id", "text", "claim_type", "temporal_scope", "proposition", "cited_evidence_ids"].includes(key))))) throw new GatewayError("INVALID_ARGUMENTS", "validate_answer requires 1 to 32 structured claims");
  if (args.expected_generation && args.expected_generation !== snapshot.generationId) throw new GatewayError("INVALID_ARGUMENTS", "query generation mismatch; retry against the requested snapshot");
  const gate = await validateAnswer(snapshot, args.claims);
  return { ...envelope(snapshot, tool, args, scope, { gate_status: gate.gate_status, answer: gate.answer, final_claims: gate.final_claims, evidence_ids: gate.receipt.evidence_ids || [], answer_evidence_receipt: gate.receipt }, gate.final_claims?.length || 0, false, "answer_evidence"), receipt: { ...envelope(snapshot, tool, args, scope, {}, gate.final_claims?.length || 0).receipt, arguments_sha256: argumentsHash } };
}

class GatewayError extends Error {
  constructor(code, message, status = 400) { super(message); this.code = code; this.status = status; }
}

function mcpTools() {
  const readonly = { readOnlyHint: true, openWorldHint: false, destructiveHint: false };
  return [
    { name: "search_evidence", description: "Search approved publication metadata; this is not full-text or PublicEvent search.", inputSchema: { type: "object", additionalProperties: false, properties: { q: { type: "string", maxLength: 512 }, source_id: { type: "string", maxLength: 64 }, change_type: { type: "string", maxLength: 64 }, limit: { type: "integer", minimum: 1, maximum: 100 }, cursor: { type: "string", maxLength: 1024 }, expected_generation: { type: "string", maxLength: 128 } } }, annotations: readonly },
    { name: "get_current_brief", description: "Read the checked-in canonical brief with its freshness and publication receipt.", inputSchema: { type: "object", additionalProperties: false, properties: {} }, annotations: readonly },
    { name: "get_publication_receipt", description: "Read the current publication and canonical artifact hashes without exposing raw content.", inputSchema: { type: "object", additionalProperties: false, properties: {} }, annotations: readonly },
    { name: "get_source_health", description: "Read approved source health, freshness, completeness, and gaps.", inputSchema: { type: "object", additionalProperties: false, properties: { source_id: { type: "string", maxLength: 64 } } }, annotations: readonly },
    { name: "validate_answer", description: "Validate structured answer claims against the server-controlled canonical evidence catalog.", inputSchema: { type: "object", additionalProperties: false, properties: { claims: { type: "array", maxItems: 32, items: { type: "object" } }, expected_generation: { type: "string", maxLength: 128 } }, required: ["claims"] }, annotations: readonly },
  ];
}

async function dispatchMcp(snapshot, request) {
  if (request?.jsonrpc !== "2.0" || !("id" in (request || {}))) throw new GatewayError("INVALID_JSON_RPC", "request must be JSON-RPC 2.0 with an id");
  if (request.method === "initialize") return { jsonrpc: "2.0", id: request.id, result: { protocolVersion: MCP_PROTOCOL_VERSION, capabilities: { tools: { listChanged: false } }, serverInfo: { name: "govintel-query-gateway", version: SERVER_VERSION } } };
  if (request.method === "tools/list") return { jsonrpc: "2.0", id: request.id, result: { tools: mcpTools() } };
  if (request.method !== "tools/call" || !request.params || typeof request.params.name !== "string") throw new GatewayError("METHOD_NOT_FOUND", `unsupported MCP method: ${request.method}`, 404);
  try {
    const payload = await execute(snapshot, request.params.name, request.params.arguments);
    return { jsonrpc: "2.0", id: request.id, result: { isError: false, content: [{ type: "text", text: JSON.stringify(payload) }], structuredContent: payload } };
  } catch (error) {
    const gatewayError = error instanceof GatewayError ? error : new GatewayError("INVALID_ARGUMENTS", String(error?.message || error));
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
        return responseJson({ schema_version: 1, service: "govintel-query-gateway", server_version: SERVER_VERSION, status: "ok", publication_freshness: freshness(scope.dataStatus), publication_id: snapshot.generatedFrom.collection_run_id, publication_hash: snapshot.generatedFrom.brief_sha256, query_coverage: queryCoverage(snapshot, "publication_metadata"), policy: snapshot.policyBinding, retention: RETENTION_POLICY, source_gaps: scope.gaps, read_only: true }, 200, request, env);
      }
      if (request.method === "GET" && url.pathname === "/capabilities") return responseJson({ schema_version: 1, server_version: SERVER_VERSION, read_only: true, capabilities: ["search_evidence", "get_current_brief", "get_publication_receipt", "get_source_health", "validate_answer"], unavailable_capabilities: DOMAIN_CAPABILITIES, policy: snapshot.policyBinding, retention: RETENTION_POLICY }, 200, request, env);
      if (request.method !== "POST" || !["/query", "/mcp"].includes(url.pathname)) return responseJson(jsonError("NOT_FOUND", "route not found"), 404, request, env);
      const bytes = await request.arrayBuffer();
      if (bytes.byteLength > MAX_REQUEST_BYTES) return responseJson(jsonError("REQUEST_TOO_LARGE", "request exceeds byte budget"), 413, request, env);
      const input = JSON.parse(new TextDecoder().decode(bytes));
      if (url.pathname === "/query") return responseJson(await execute(snapshot, input?.tool, input?.arguments), 200, request, env);
      return responseJson(await dispatchMcp(snapshot, input), 200, request, env);
    } catch (error) {
      const gatewayError = error instanceof GatewayError ? error : new GatewayError("UPSTREAM_UNAVAILABLE", String(error?.message || error), 503);
      return responseJson(jsonError(gatewayError.code, gatewayError.message), gatewayError.status, request, env);
    }
  },
};
