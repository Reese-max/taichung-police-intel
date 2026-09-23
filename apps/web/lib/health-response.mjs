import { assessPublication, PUBLICATION_POLICY_BINDING } from "./publication-freshness.mjs";

export function buildHealthResponse(sourceStatus, publication, nowMs) {
  if (!sourceStatus || sourceStatus.mode !== "COMPETITION_DEMO") {
    throw new Error("invalid demo state");
  }

  const assessment = Number.isFinite(nowMs)
    ? assessPublication(publication, sourceStatus, nowMs)
    : {
        state: "UNKNOWN",
        canReassure: false,
        ageMs: null,
        reason: "靜態輸出無法在請求時讀取目前時鐘，請由前端重新核對快照時效。",
      };
  const sources = Array.isArray(sourceStatus.sources) ? sourceStatus.sources : [];
  const failedSources = sources.filter(source => source?.source_health === "FAILED").length;
  const staleSources = sources.filter(source => ["STALE", "VERY_STALE"].includes(source?.freshness_status)).length;

  return {
    status: assessment.state === "RECENT" ? "ok" : assessment.state.toLowerCase(),
    health: assessment.state,
    can_reassure: assessment.canReassure,
    reason: assessment.reason,
    snapshot_age_ms: assessment.ageMs,
    mode: sourceStatus.mode,
    generated_at: sourceStatus.generated_at,
    sources: sources.length,
    failed_sources: failedSources,
    stale_sources: staleSources,
    policy: PUBLICATION_POLICY_BINDING,
    deployment_verified: false,
    public_http_verified: false,
  };
}
