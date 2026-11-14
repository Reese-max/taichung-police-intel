const ARCHIVE_SEARCH_LIMIT = 20;

export function searchArchive({ homeFeed, fullFeed, query }) {
  const preview = Array.isArray(homeFeed?.archive_preview) ? homeFeed.archive_preview : [];
  const needle = (query || "").trim().toLocaleLowerCase("zh-Hant");
  if (!needle) return { kind: "preview", items: preview };
  if (!fullFeed) return { kind: "needs_full_index", items: [] };
  if (!homeFeed?.collection_run_id || homeFeed.collection_run_id !== fullFeed.collection_run_id) {
    return { kind: "generation_mismatch", items: [] };
  }
  if (!Array.isArray(fullFeed.items)) return { kind: "invalid_full_index", items: [] };

  const items = fullFeed.items
    .filter((item) => {
      const haystack = [item.title, item.committee, item.source_id]
        .filter(Boolean)
        .join(" ")
        .toLocaleLowerCase("zh-Hant");
      return haystack.includes(needle);
    })
    .slice(0, ARCHIVE_SEARCH_LIMIT);
  return { kind: "results", items };
}

export function getArchiveSearchView({ homeFeed, fullFeed, query, loadState }) {
  const result = searchArchive({ homeFeed, fullFeed, query });
  if (!(query || "").trim()) {
    return { ...result, state: "preview", statusMessage: null, showNoResults: false };
  }

  if (
    loadState === "failed"
    || result.kind === "generation_mismatch"
    || result.kind === "invalid_full_index"
  ) {
    return {
      ...result,
      state: "failed",
      statusMessage: "完整歷史索引載入失敗，請稍後再試。",
      showNoResults: false,
    };
  }
  if (loadState === "loaded" && result.kind === "results") {
    return {
      ...result,
      state: "loaded",
      statusMessage: null,
      showNoResults: result.items.length === 0,
    };
  }
  return {
    ...result,
    state: "loading",
    statusMessage: "正在載入完整歷史索引…",
    showNoResults: false,
  };
}

export function shouldLoadFullArchive({
  homeFeed,
  fullFeed,
  query,
  loadState,
  queryRevision,
  lastRequestedRevision,
}) {
  return Boolean(
    (query || "").trim()
      && homeFeed
      && !fullFeed
      && ["idle", "failed"].includes(loadState)
      && queryRevision !== lastRequestedRevision,
  );
}
