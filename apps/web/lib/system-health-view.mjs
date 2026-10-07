// Presentation helpers for the end-to-end system-health receipt (#35).
//
// Kept in a plain ESM module (not inside the JSX component) so the unknown-safe
// rendering rules can be exercised directly by the test suite instead of being
// asserted against component source text.

export const SLO_UNIT_SUFFIX = {
  ratio: "",
  count: " 次",
  milliseconds: " ms",
};

// Unmeasured fields render as UNKNOWN with their reason, never as a number:
// a zero would read as "measured, and it was zero".
export function formatSloMetric(metric) {
  if (!metric || metric.measured !== true || typeof metric.value !== "number") {
    return { value: "UNKNOWN", note: metric?.reason || "尚未量測" };
  }
  const suffix = SLO_UNIT_SUFFIX[metric.unit] ?? ` ${metric.unit}`;
  return { value: `${metric.value}${suffix}`, note: "" };
}

// The receipt may come from an older artifact without the #35 sections; every
// accessor below degrades to an empty/UNKNOWN view instead of throwing.
export function stageModelEntries(health) {
  const stageIds = health?.stage_model?.stage_ids;
  return Array.isArray(stageIds) ? stageIds : [];
}

export function sloMetricEntries(health) {
  const metrics = health?.slo?.metrics;
  return metrics && typeof metrics === "object" ? Object.entries(metrics) : [];
}

export function upstreamOperatingStateLabel(health) {
  const state = health?.upstream?.upstream_operating_state;
  return typeof state === "string" && state ? state : "UNKNOWN";
}

const SOURCE_REASON_LABELS = {
  SOURCE_COVERAGE_OR_COLLECTION_GAP: "來源取得或涵蓋有缺口",
  SOURCE_FRESHNESS_STALE: "官方資料日期陳舊",
  SOURCE_FRESHNESS_UNKNOWN: "官方資料日期未知",
  SOURCE_LAST_SUCCESS_UNKNOWN: "取得成功時間或時區未知",
};

export function sourceActionEntries(health) {
  const rows = health?.operator_summary?.source_actions;
  if (!Array.isArray(rows)) return [];
  return rows.slice(0, 64).flatMap((row) => {
    if (!row || typeof row.source_id !== "string" || !/^S-[0-9]{3}$/.test(row.source_id)
        || !Array.isArray(row.reasons) || !Array.isArray(row.next_actions)
        || row.reasons.length === 0 || row.next_actions.length === 0
        || row.reasons.some((value) => typeof value !== "string" || !value || value.length > 128)
        || row.next_actions.some((value) => typeof value !== "string" || !value.trim() || value.length > 512)) return [];
    return [{
      source_id: row.source_id,
      reasons: row.reasons.slice(0, 8).map((reason) => SOURCE_REASON_LABELS[reason] || "來源狀態待查核"),
      next_actions: row.next_actions.slice(0, 8),
    }];
  });
}
