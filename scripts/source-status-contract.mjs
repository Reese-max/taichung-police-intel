const SHA256 = /^[0-9a-f]{64}$/;

export function sourceManifestFailures(source) {
  const label = `demo-status:${source.source_id || "unknown"}`;
  const failures = [];
  const knownGood = source.last_known_good;
  const validKnownGood = Boolean(knownGood?.source_run_id && SHA256.test(knownGood.manifest_sha256 || ""));

  if (source.source_health === "FAILED") {
    // A failed fetch has no current manifest. Its previous verified manifest
    // remains available as last-known-good, never as a new observation.
    if (source.manifest_sha256 !== null) failures.push(`${label}:failed-source-has-current-manifest`);
    if (!validKnownGood) failures.push(`${label}:failed-source-missing-lkg`);
    if (source.result !== "FAILED" || source.window_completeness !== "PARTIAL" ||
        !source.intelligence_gaps?.includes("SOURCE_FAILED") || !source.error_code) {
      failures.push(`${label}:failed-source-not-marked`);
    }
  } else {
    if (!SHA256.test(source.manifest_sha256 || "")) failures.push(`${label}:invalid-manifest`);
    if (source.source_health === "PASS" && !validKnownGood) failures.push(`${label}:missing-lkg`);
    if (!["PASS", "DEGRADED"].includes(source.source_health)) failures.push(`${label}:invalid-health`);
  }
  return failures;
}

export function failedSourceFeedFailures(item, failedSourceIds) {
  const statusFailed = failedSourceIds.has(item.source_id);
  if (!statusFailed && item.source_health !== "FAILED") return [];
  if (statusFailed && item.source_health === "FAILED" &&
      item.change_type === "LKG" && item.eligibility === "INELIGIBLE_SOURCE_FAILED") return [];
  return [`feed:${item.stable_id || "unknown"}:failed-source-not-lkg`];
}
