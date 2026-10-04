import { validateReplay } from "./public-query.js";

const DATASETS = ["v2-daily-brief", "intelligence-feed", "source-status", "public-query-replay"];

// Each independent request includes body consumption in its deadline. A stalled
// source must not keep the tracking panel waiting for every dataset forever.
export async function loadConditionDatasets(basePath = "", { fetchImpl = fetch, timeoutMs = 12000 } = {}) {
  const results = await Promise.allSettled(DATASETS.map(async name => {
    const response = await fetchImpl(`${basePath}/data/${name}.json`, {
      cache: "no-store", signal: AbortSignal.timeout(timeoutMs),
    });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const data = await response.json();
    return name === "public-query-replay" ? validateReplay(data) : data;
  }));
  return {
    datasets: Object.fromEntries(results.map((result, index) => [DATASETS[index], result.status === "fulfilled" ? result.value : null])),
    replayError: results[3].status === "rejected" ? results[3].reason.message : null,
  };
}
