import { assessPublication } from "./publication-freshness.mjs";

export function buildHealthResponse(sourceStatus, publication, nowMs = Date.now()) {
  if (!sourceStatus || sourceStatus.mode !== "COMPETITION_DEMO") {
    throw new Error("invalid demo state");
  }

  const assessment = assessPublication(publication, sourceStatus, nowMs);
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
    deployment_verified: false,
    public_http_verified: false,
  };
}
