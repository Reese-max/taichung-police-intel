export const HANDOFF_STORAGE_KEY = "govintel.v2.handoff.v1";

const ACTIVE_STATUSES = new Set(["WATCHING", "NEEDS_REVIEW"]);
const WATCH_STATUSES = new Set(["WATCHING", "NEEDS_REVIEW", "RESOLVED", "DISMISSED"]);

function copy(value) {
  return JSON.parse(JSON.stringify(value));
}

function stamp(value) {
  const date = value instanceof Date ? value : new Date(value ?? Date.now());
  if (Number.isNaN(date.getTime())) throw new Error("時間格式無法驗證");
  return date.toISOString();
}

function identityFor(item) {
  if (typeof item?.identity === "string" && item.identity.includes(":")) return item.identity;
  if (item?.source_id && item?.stable_key) return `${item.source_id}:${item.stable_key}`;
  return "";
}

function versionFor(item) {
  const value = item?.source_version ?? item?.version_no ?? item?.after_version;
  return Number.isInteger(value) && value >= 1 ? value : null;
}

function hashFor(item) {
  return item?.source_sha256 || item?.normalized_sha256 || item?.source_hash || null;
}

function itemForWatch(item) {
  const identity = identityFor(item);
  const version = versionFor(item);
  const officialUrl = typeof item?.official_url === "string" ? item.official_url : "";
  if (!identity || version === null || !officialUrl.startsWith("https://")) return null;
  return {
    identity,
    source_id: item.source_id || identity.split(":", 1)[0],
    stable_key: item.stable_key || identity.slice(identity.indexOf(":") + 1),
    title: item.title || item.headline || "未命名官方資料",
    official_url: officialUrl,
    source_version: version,
    source_sha256: hashFor(item),
    source_document_version: item.source_document_version || item.document_version_id || null,
    event_id: item.event_id || null,
  };
}

function requiredItem(item) {
  const normalized = itemForWatch(item);
  if (!normalized) throw new Error("此項缺少可驗證的官方 URL 或來源版本，未加入追蹤");
  return normalized;
}

function watchIdFor(identity) {
  return `WATCH-${encodeURIComponent(identity)}`;
}

function claimIdFor(identity, version) {
  return `CLAIM-${encodeURIComponent(`${identity}#v${version}`)}`;
}

function invalidationIdFor(watchId, version, sourceHash) {
  return `INV-${encodeURIComponent(`${watchId}|${version}|${sourceHash || "UNKNOWN"}`)}`;
}

function itemsByIdentity(items) {
  const values = Array.isArray(items) ? items : Object.values(items || {});
  return new Map(
    values
      .map(itemForWatch)
      .filter(Boolean)
      .map((item) => [item.identity, item]),
  );
}

export function emptyLocalHandoff() {
  return {
    schema_version: 1,
    mode: "V2_HANDOFF",
    watch_items: {},
    handoffs: [],
    last_publication: null,
    last_updated_at: null,
  };
}

export function validateLocalHandoff(value) {
  if (!value || value.schema_version !== 1 || value.mode !== "V2_HANDOFF") {
    throw new Error("本機交班資料格式不相容，已停止載入");
  }
  if (!value.watch_items || Array.isArray(value.watch_items) || !Array.isArray(value.handoffs)) {
    throw new Error("本機交班資料結構不完整，已停止載入");
  }
  for (const [watchId, watch] of Object.entries(value.watch_items)) {
    if (
      !watch
      || watch.watch_id !== watchId
      || !identityFor(watch)
      || !WATCH_STATUSES.has(watch.status)
      || typeof watch.official_url !== "string"
      || !watch.official_url.startsWith("https://")
      || !Array.isArray(watch.invalidations)
    ) {
      throw new Error(`本機追蹤資料無法驗證：${watchId}`);
    }
  }
  return value;
}

export function loadLocalHandoff(storage = null) {
  const target = storage || (typeof window !== "undefined" ? window.localStorage : null);
  if (!target) return emptyLocalHandoff();
  const raw = target.getItem(HANDOFF_STORAGE_KEY);
  if (!raw) return emptyLocalHandoff();
  return validateLocalHandoff(JSON.parse(raw));
}

export function saveLocalHandoff(state, storage = null) {
  const target = storage || (typeof window !== "undefined" ? window.localStorage : null);
  const valid = validateLocalHandoff(state);
  if (!target) throw new Error("瀏覽器不提供本機儲存，無法保存交班草稿");
  target.setItem(HANDOFF_STORAGE_KEY, JSON.stringify(valid));
  return valid;
}

export function addLocalWatch(state, item, createdAt = new Date()) {
  const result = copy(validateLocalHandoff(state));
  const current = requiredItem(item);
  const watchId = watchIdFor(current.identity);
  const existing = result.watch_items[watchId];
  if (existing && !["RESOLVED", "DISMISSED"].includes(existing.status)) return result;
  const at = stamp(createdAt);
  result.watch_items[watchId] = {
    ...(existing || {}),
    watch_id: watchId,
    ...current,
    created_at: at,
    updated_at: at,
    status: "WATCHING",
    reason_code: "MANUAL",
    tracked_version: current.source_version,
    tracked_sha256: current.source_sha256,
    last_reviewed_version: null,
    last_reviewed_sha256: null,
    last_handoff_id: null,
    invalidations: existing?.invalidations || [],
  };
  result.last_updated_at = at;
  return result;
}

export function syncLocalHandoff(state, items, publication = {}, observedAt = new Date()) {
  const result = copy(validateLocalHandoff(state));
  const currentItems = itemsByIdentity(items);
  const at = stamp(observedAt);
  for (const watch of Object.values(result.watch_items)) {
    const current = currentItems.get(watch.identity);
    if (!current) continue;
    watch.title = current.title;
    watch.official_url = current.official_url;
    watch.source_version = current.source_version;
    watch.source_sha256 = current.source_sha256;
    const versionChanged = current.source_version > (watch.tracked_version || 0);
    const hashChanged = Boolean(
      current.source_sha256
      && watch.tracked_sha256
      && current.source_sha256 !== watch.tracked_sha256,
    );
    if (!versionChanged && !hashChanged) continue;
    const invalidationId = invalidationIdFor(
      watch.watch_id,
      current.source_version,
      current.source_sha256,
    );
    if (!watch.invalidations.some((entry) => entry.invalidation_id === invalidationId)) {
      watch.invalidations.push({
        invalidation_id: invalidationId,
        event_id: current.event_id,
        reason: "SOURCE_VERSION_CHANGED",
        detected_at: at,
        before: {
          version: watch.tracked_version,
          normalized_sha256: watch.tracked_sha256,
        },
        after: {
          version: current.source_version,
          normalized_sha256: current.source_sha256,
          official_url: current.official_url,
        },
      });
    }
    watch.status = "NEEDS_REVIEW";
    watch.updated_at = at;
  }
  result.last_publication = {
    collection_run_id: publication.source_collection_run_id || publication.collection_run_id || null,
    observed_at: at,
  };
  result.last_updated_at = at;
  return result;
}

export function projectLocalTracking(state, items) {
  const currentItems = itemsByIdentity(items);
  return Object.values(validateLocalHandoff(state).watch_items)
    .filter((watch) => ACTIVE_STATUSES.has(watch.status))
    .map((watch) => {
      const current = currentItems.get(watch.identity) || watch;
      const invalidation = watch.invalidations.at(-1) || null;
      return {
        ...watch,
        tracking_id: `TRACK-${watch.watch_id}`,
        headline: current.title || watch.title,
        what_changed: watch.status === "NEEDS_REVIEW"
          ? "官方來源版本已變更，需重新核對；既有交班版本仍保留。"
          : "此項由承辦人加入跨日追蹤，尚未標記為已處理。",
        recommended_action: watch.status === "NEEDS_REVIEW"
          ? "核對新舊官方版本，再確認是否建立下一版交班摘要。"
          : "確認目前官方內容與業管狀態，必要時建立交班版本。",
        watch_status: watch.status,
        official_url: current.official_url || watch.official_url,
        source_version: current.source_version || watch.tracked_version,
        source_sha256: current.source_sha256 || watch.tracked_sha256,
        invalidation,
      };
    })
    .sort((left, right) => (
      Number(right.watch_status === "NEEDS_REVIEW") - Number(left.watch_status === "NEEDS_REVIEW")
      || left.watch_id.localeCompare(right.watch_id)
    ));
}

export function confirmLocalHandoff(state, items, publication = {}, confirmedAt = new Date(), watchIds = null) {
  const result = copy(validateLocalHandoff(state));
  const currentItems = itemsByIdentity(items);
  const selected = watchIds?.length
    ? [...new Set(watchIds)]
    : Object.values(result.watch_items)
      .filter((watch) => ACTIVE_STATUSES.has(watch.status))
      .map((watch) => watch.watch_id);
  if (!selected.length) throw new Error("至少加入一項追蹤後才能確認交班版本");
  const entries = selected.map((watchId) => {
    const watch = result.watch_items[watchId];
    const current = currentItems.get(watch?.identity);
    if (!watch || !ACTIVE_STATUSES.has(watch.status) || !current) {
      throw new Error(`目前沒有可驗證的來源版本：${watchId}`);
    }
    return {
      watch_id: watchId,
      identity: current.identity,
      title: current.title,
      source_version: current.source_version,
      source_sha256: current.source_sha256,
      claim_id: claimIdFor(current.identity, current.source_version),
      source_document_version: current.source_document_version || current.source_sha256 || `v${current.source_version}`,
      evidence: {
        official_url: current.official_url,
        locator: `${current.identity}#v${current.source_version}`,
      },
    };
  });
  const generated = stamp(publication.generated_at || confirmedAt);
  const confirmed = stamp(confirmedAt);
  const briefVersion = Math.max(0, ...result.handoffs.map((handoff) => handoff.brief_version || 0)) + 1;
  const material = [briefVersion, generated, ...entries.map((entry) => `${entry.identity}@v${entry.source_version}`)];
  const briefId = `HANDOFF-${briefVersion}-${encodeURIComponent(material.join("|"))}`;
  const handoff = {
    brief_id: briefId,
    brief_version: briefVersion,
    generated_at: generated,
    confirmed_at: confirmed,
    confirmation_state: "CONFIRMED",
    publication: {
      collection_run_id: publication.source_collection_run_id || publication.collection_run_id || null,
      brief_sha256: publication.brief_sha256 || publication.publication_hash || null,
    },
    items: entries,
  };
  result.handoffs.push(handoff);
  for (const entry of entries) {
    const watch = result.watch_items[entry.watch_id];
    watch.status = "WATCHING";
    watch.updated_at = confirmed;
    watch.tracked_version = entry.source_version;
    watch.tracked_sha256 = entry.source_sha256;
    watch.last_reviewed_version = entry.source_version;
    watch.last_reviewed_sha256 = entry.source_sha256;
    watch.last_handoff_id = briefId;
    for (const invalidation of watch.invalidations) invalidation.resolved_in_handoff_id = briefId;
  }
  result.last_updated_at = confirmed;
  return result;
}

export function setLocalWatchStatus(state, watchId, status, updatedAt = new Date()) {
  if (!new Set(["RESOLVED", "DISMISSED"]).has(status)) throw new Error("不支援的追蹤狀態");
  const result = copy(validateLocalHandoff(state));
  const watch = result.watch_items[watchId];
  if (!watch) throw new Error(`找不到追蹤項目：${watchId}`);
  watch.status = status;
  watch.updated_at = stamp(updatedAt);
  result.last_updated_at = watch.updated_at;
  return result;
}

export function latestHandoff(state) {
  const valid = validateLocalHandoff(state);
  return valid.handoffs.at(-1) || null;
}

export function handoffMarkdown(handoff) {
  if (!handoff) throw new Error("尚未確認交班版本");
  const lines = [
    `# GovIntel AI 本機交班摘要 v${handoff.brief_version}`,
    "",
    `- brief_id: \`${handoff.brief_id}\``,
    `- generated_at: \`${handoff.generated_at}\``,
    `- confirmed_at: \`${handoff.confirmed_at}\``,
    `- collection_run_id: \`${handoff.publication?.collection_run_id || "UNKNOWN"}\``,
    "",
    "## 追蹤項目",
    "",
  ];
  for (const item of handoff.items) {
    lines.push(
      `### ${item.title}`,
      `- watch_id: \`${item.watch_id}\``,
      `- claim_id: \`${item.claim_id}\``,
      `- source version: \`v${item.source_version}\``,
      `- evidence: [${item.evidence.official_url}](${item.evidence.official_url})`,
      `- locator: \`${item.evidence.locator}\``,
      "",
    );
  }
  return lines.join("\n");
}

export function exportLocalHandoff(state, format = "markdown") {
  const handoff = latestHandoff(state);
  if (!handoff) throw new Error("尚未確認交班版本");
  if (format === "json") {
    return {
      content: JSON.stringify(handoff, null, 2),
      filename: `${handoff.brief_id}.json`,
      mime: "application/json",
    };
  }
  return {
    content: handoffMarkdown(handoff),
    filename: `${handoff.brief_id}.md`,
    mime: "text/markdown;charset=utf-8",
  };
}
