const ARCHIVE_SEARCH_LIMIT = 20;

export function searchArchive({ homeFeed, fullFeed, query }) {
  const preview = Array.isArray(homeFeed?.archive_preview) ? homeFeed.archive_preview : [];
  const needle = (query || "").trim().toLocaleLowerCase("zh-Hant");
  if (!needle) return { kind: "preview", items: preview };
  if (!fullFeed) return { kind: "needs_full_index", items: preview };
  if (!homeFeed?.collection_run_id || homeFeed.collection_run_id !== fullFeed.collection_run_id) {
    return { kind: "generation_mismatch", items: preview };
  }
  if (!Array.isArray(fullFeed.items)) return { kind: "invalid_full_index", items: preview };

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
