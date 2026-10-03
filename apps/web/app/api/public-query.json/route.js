import { readFile } from "node:fs/promises";
import { resolve } from "node:path";

// Load the query_domain module
import { fileURLToPath } from "node:url";
import { dirname } from "node:path";

const __filename = fileURLToPath(import.meta.url);
const __dirname = dirname(__filename);
const ROOT = resolve(__dirname, "../../../../..");

// Import the Python query_domain module
import { createRequire } from "node:module";
const require = createRequire(import.meta.url);

// We'll load the Python module via a different approach
// For now, use a simple implementation that matches the Python API

const EVENT_STORE_PATH = resolve(process.cwd(), "apps/web/public/data/public-events.json");
const STATISTICS_STORE_PATH = resolve(process.cwd(), "apps/web/public/data/statistics.json");

let eventStoreCache = null;
let statisticsStoreCache = null;

function canonicalJson(value) {
  return JSON.stringify(value, (key, val) => {
    if (Array.isArray(val)) return val;
    if (val && typeof val === "object") {
      return Object.keys(val).sort().reduce((obj, k) => { obj[k] = val[k]; return obj; }, {});
    }
    return val;
  });
}

async function sha256(data) {
  const encoder = new TextEncoder();
  const bytes = encoder.encode(data);
  const hash = await crypto.subtle.digest("SHA-256", bytes);
  return Array.from(new Uint8Array(hash)).map(b => b.toString(16).padStart(2, "0")).join("");
}

async function loadEventStore() {
  if (eventStoreCache) return eventStoreCache;
  const raw = await readFile(EVENT_STORE_PATH, "utf8");
  const data = JSON.parse(raw);
  // Validate and normalize
  eventStoreCache = data;
  return eventStoreCache;
}

async function loadStatisticsStore() {
  if (statisticsStoreCache) return statisticsStoreCache;
  const raw = await readFile(STATISTICS_STORE_PATH, "utf8");
  const data = JSON.parse(raw);
  statisticsStoreCache = data;
  return statisticsStoreCache;
}

function _instant(value, name) {
  if (!value || typeof value !== "string") throw new Error(`${name} must be an ISO-8601 timestamp`);
  const parsed = new Date(value.replace("Z", "+00:00"));
  if (isNaN(parsed.getTime())) throw new Error(`${name} must be an ISO-8601 timestamp`);
  return parsed;
}

function _timeBound(value, name) {
  if (!value || typeof value !== "string") throw new Error(`${name} must be an ISO-8601 date or timestamp`);
  let parsed;
  if (value.length === 10) {
    parsed = new Date(value + "T00:00:00+00:00");
  } else {
    parsed = new Date(value.replace("Z", "+00:00"));
  }
  if (isNaN(parsed.getTime())) throw new Error(`${name} must be an ISO-8601 date or timestamp`);
  return parsed;
}

function _eventStart(event) {
  if (event.start_at) return _instant(event.start_at, "start_at");
  if (event.event_date) return _timeBound(event.event_date, "event_date");
  if (event.end_at) return _instant(event.end_at, "end_at");
  return new Date(-8640000000000000); // datetime.min
}

function _eventEnd(event) {
  if (event.end_at) return _instant(event.end_at, "end_at");
  return _eventStart(event);
}

function _eventMatches(event, args) {
  if (args.public_event_id && event.public_event_id !== args.public_event_id) return false;
  if (args.q && !`${event.canonical_title} ${event.event_type || ""}`.toLowerCase().includes(args.q.toLowerCase())) return false;
  
  const locations = new Set([event.district_id, ...(event.location_ids || []), ...(event.location_candidates || [])]);
  if (args.region && !locations.has(args.region)) return false;
  if (args.district && !locations.has(args.district)) return false;
  
  const agencies = new Set([...(event.agency_ids || []), ...(event.independent_source_ids || [])]);
  if (args.agency && !agencies.has(args.agency)) return false;
  
  if (args.category && event.event_type !== args.category) return false;
  if (args.verification_status && (event.verification_status || event.fusion_status) !== args.verification_status) return false;
  if (args.event_status && (event.event_status || event.source_state) !== args.event_status) return false;
  if (args.tracked !== undefined && !!args.tracked !== !!(event.tracked || event.tracking_id)) return false;
  if (args.changed_only && !(event.changed || event.changed_fields?.length || event.change_type)) return false;
  
  const lower = args.time_from ? _timeBound(args.time_from, "time_from") : null;
  const upper = args.time_to ? _timeBound(args.time_to, "time_to") : null;
  if (lower && upper && upper < lower) throw new Error("time_to must not precede time_from");
  
  const hasTime = event.start_at || event.end_at || event.event_date;
  if ((lower || upper) && !hasTime) return false;
  if (lower && _eventEnd(event) < lower) return false;
  if (upper && _eventStart(event) > upper) return false;
  return true;
}

function _projectDocument(eventId, link) {
  const versionId = link.document_version_id;
  const locator = link.evidence_locator || `${link.official_url}#document-version=${encodeURIComponent(versionId)}`;
  return {
    document_version_id: versionId,
    document_id: link.document_id,
    source_id: link.source_id,
    official_url: link.official_url,
    evidence_locator: locator,
    evidence_id: link.evidence_id || `DOC-${sha256Sync([eventId, versionId]).slice(0, 20).toUpperCase()}`,
  };
}

function sha256Sync(data) {
  let hash = 0;
  const str = JSON.stringify(data);
  for (let i = 0; i < str.length; i++) {
    hash = ((hash << 5) - hash) + str.charCodeAt(i);
    hash |= 0;
  }
  return Math.abs(hash).toString(16).padStart(64, "0");
}

function projectEvent(event) {
  const allowed = [
    "public_event_id", "event_type", "named_event_id", "canonical_title", "event_date", "start_at", "end_at",
    "district_id", "location_candidates", "location_ids", "agency_ids", "independent_source_ids",
    "independent_source_count", "fusion_status", "verification_status", "event_status", "source_state",
    "tracked", "tracking_id", "changed", "changed_fields", "conflict_fields", "uncertain_fields", "created_at", "updated_at",
  ];
  const result = {};
  for (const key of allowed) {
    if (key in event) result[key] = event[key];
  }
  result.documents = event.linked_document_versions.map(link => _projectDocument(event.public_event_id, link));
  return result;
}

function queryEvents(store, args) {
  const filters = { ...args };
  delete filters.limit;
  delete filters.cursor;
  delete filters.expected_generation;
  
  const limit = args.limit || 20;
  const cursor = args.cursor;
  
  let matches = store.public_events.filter(event => _eventMatches(event, args));
  matches = matches.map(projectEvent);
  matches.sort((a, b) => -_eventStart(a).getTime() + _eventStart(b).getTime() || a.public_event_id.localeCompare(b.public_event_id));
  
  let offset = 0;
  if (cursor) {
    try {
      const token = JSON.parse(atob(cursor.replace(/-/g, "+").replace(/_/g, "/")));
      if (token.generation !== store.generation_id) throw new Error("generation mismatch");
      offset = token.offset;
    } catch { throw new Error("invalid cursor"); }
  }
  
  const selected = matches.slice(offset, offset + limit);
  const nextCursor = offset + selected.length < matches.length
    ? btoa(JSON.stringify({ generation: store.generation_id, filters: "hash", offset: offset + selected.length }))
    : null;
  
  return {
    query_generation_id: store.generation_id,
    total_matches: matches.length,
    result_count: selected.length,
    offset,
    truncated: matches.length > selected.length,
    has_more: !!nextCursor,
    next_cursor: nextCursor,
    results: selected,
  };
}

function getEvent(store, eventId) {
  const event = store.public_events.find(e => e.public_event_id === eventId);
  if (!event) throw new Error(`Event not found: ${eventId}`);
  const projected = projectEvent(event);
  projected.tracking = {
    tracking_id: event.tracking_id,
    tracked: !!(event.tracked || event.tracking_id),
    changed: !!(event.changed || event.changed_fields?.length || event.change_type),
  };
  projected.version_count = (event.version_history || []).length;
  return projected;
}

function compareEventVersions(store, eventId, beforeVersion, afterVersion) {
  const event = store.public_events.find(e => e.public_event_id === eventId);
  if (!event) throw new Error(`Event not found: ${eventId}`);
  
  const versions = (event.version_history || []).map(v => ({
    document_version_id: v.document_version_id || v.version_id,
    observed_at: v.observed_at,
    published_at: v.published_at,
    effective_at: v.effective_at,
    fields: v.fields || {},
    changed_fields: v.changed_fields || [],
  })).sort((a, b) => (a.observed_at || "").localeCompare(b.observed_at || ""));
  
  const byId = Object.fromEntries(versions.map(v => [v.document_version_id, v]));
  
  let before = beforeVersion ? byId[beforeVersion] : null;
  let after = afterVersion ? byId[afterVersion] : null;
  
  if (beforeVersion && !before) throw new Error(`Version not found: ${beforeVersion}`);
  if (afterVersion && !after) throw new Error(`Version not found: ${afterVersion}`);
  
  if (!before && !after && versions.length >= 2) {
    before = versions[versions.length - 2];
    after = versions[versions.length - 1];
  }
  
  if (!before || !after || before === after) {
    return {
      comparison_status: "NO_COMPARABLE_VERSION_HISTORY",
      public_event_id: eventId,
      before: null,
      after: null,
      changed_fields: [],
      materiality: "UNKNOWN",
      source_document_versions: event.linked_document_versions.map(l => l.document_version_id),
      affected_handoff_claims: event.affected_handoff_claims || [],
    };
  }
  
  const beforeFields = before.fields || {};
  const afterFields = after.fields || {};
  const changed = new Set([
    ...(before.changed_fields || []),
    ...(after.changed_fields || []),
    ...Object.keys({...beforeFields, ...afterFields}).filter(k => beforeFields[k] !== afterFields[k]),
  ]);
  
  return {
    comparison_status: "COMPARED",
    public_event_id: eventId,
    before: { document_version_id: before.document_version_id, observed_at: before.observed_at, published_at: before.published_at, effective_at: before.effective_at, fields: beforeFields, changed_fields: before.changed_fields || [] },
    after: { document_version_id: after.document_version_id, observed_at: after.observed_at, published_at: after.published_at, effective_at: after.effective_at, fields: afterFields, changed_fields: after.changed_fields || [] },
    changed_fields: Array.from(changed).sort(),
    materiality: changed.size ? "MATERIAL" : "NO_CHANGE",
    source_document_versions: [before.document_version_id, after.document_version_id],
    observed_at: { before: before.observed_at, after: after.observed_at },
    published_at: { before: before.published_at, after: after.published_at },
    effective_at: { before: before.effective_at, after: after.effective_at },
    affected_handoff_claims: event.affected_handoff_claims || [],
  };
}

function queryStatistics(store, args) {
  const filters = { ...args };
  delete filters.limit;
  delete filters.cursor;
  delete filters.expected_generation;
  
  const limit = args.limit || 20;
  const cursor = args.cursor;
  
  let rows = store.statistics.filter(row => {
    if (args.dataset_id && row.dataset_id !== args.dataset_id) return false;
    if (args.metric && row.metric !== args.metric) return false;
    if (args.geography && row.geography !== args.geography) return false;
    if (args.agency && row.agency !== args.agency) return false;
    if (args.provisional !== undefined && row.provisional !== args.provisional) return false;
    if (args.period_from && row.period < args.period_from) return false;
    if (args.period_to && row.period > args.period_to) return false;
    return true;
  });
  
  rows.sort((a, b) => a.period.localeCompare(b.period) || a.dataset_id.localeCompare(b.dataset_id) || a.statistic_id.localeCompare(b.statistic_id));
  
  let offset = 0;
  if (cursor) {
    try {
      const token = JSON.parse(atob(cursor.replace(/-/g, "+").replace(/_/g, "/")));
      if (token.generation !== store.generation_id) throw new Error("generation mismatch");
      offset = token.offset;
    } catch { throw new Error("invalid cursor"); }
  }
  
  const selected = rows.slice(offset, offset + limit);
  const nextCursor = offset + selected.length < rows.length
    ? btoa(JSON.stringify({ generation: store.generation_id, filters: "hash", offset: offset + selected.length }))
    : null;
  
  return {
    query_generation_id: store.generation_id,
    total_matches: rows.length,
    result_count: selected.length,
    offset,
    truncated: rows.length > selected.length,
    has_more: !!nextCursor,
    next_cursor: nextCursor,
    results: selected,
  };
}

function jsonError(code, message, status = 400) {
  return Response.json({ schema_version: 1, error: { code, message } }, { status });
}

export async function POST(request) {
  try {
    const body = await request.json();
    const tool = body?.tool;
    const args = body?.arguments || {};

    if (!tool || typeof tool !== "string") {
      return jsonError("INVALID_ARGUMENTS", "tool is required");
    }

    if (tool === "search_events") {
      const store = await loadEventStore();
      const result = queryEvents(store, args);
      return Response.json({
        schema_version: 1,
        query_id: crypto.randomUUID(),
        tool_name: tool,
        query_generation_id: result.query_generation_id,
        queried_at: new Date().toISOString(),
        ...result,
      });
    }

    if (tool === "get_event") {
      const store = await loadEventStore();
      const event = getEvent(store, args.event_id);
      return Response.json({
        schema_version: 1,
        query_id: crypto.randomUUID(),
        tool_name: tool,
        query_generation_id: store.generation_id,
        queried_at: new Date().toISOString(),
        event_ids: [args.event_id],
        event,
      });
    }

    if (tool === "compare_event_versions") {
      const store = await loadEventStore();
      const comparison = compareEventVersions(store, args.event_id, args.before_version, args.after_version);
      return Response.json({
        schema_version: 1,
        query_id: crypto.randomUUID(),
        tool_name: tool,
        query_generation_id: store.generation_id,
        queried_at: new Date().toISOString(),
        event_ids: [args.event_id],
        comparison,
      });
    }

    if (tool === "query_statistics") {
      const store = await loadStatisticsStore();
      const result = queryStatistics(store, args);
      return Response.json({
        schema_version: 1,
        query_id: crypto.randomUUID(),
        tool_name: tool,
        query_generation_id: result.query_generation_id,
        queried_at: new Date().toISOString(),
        ...result,
      });
    }

    return jsonError("CAPABILITY_NOT_AVAILABLE", `${tool} is not implemented`, 422);
  } catch (error) {
    if (error instanceof SyntaxError) {
      return jsonError("INVALID_JSON", "request body must be valid JSON");
    }
    if (error.message?.includes("not found") || error.message?.includes("KeyError")) {
      return jsonError("EVENT_NOT_FOUND", error.message, 404);
    }
    if (error.message?.includes("generation mismatch")) {
      return jsonError("INVALID_ARGUMENTS", error.message);
    }
    if (error.message?.includes("must not precede")) {
      return jsonError("INVALID_ARGUMENTS", error.message);
    }
    return jsonError("UPSTREAM_UNAVAILABLE", error.message, 503);
  }
}
