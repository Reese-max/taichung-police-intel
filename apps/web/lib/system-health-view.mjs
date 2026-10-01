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
