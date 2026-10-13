// Presentation only: the Gateway remains responsible for source admission,
// release binding and public-field projection. This never admits extra rows.
export function indexQueryPresentation(response) {
  const rows = Array.isArray(response?.results) ? response.results : [];
  const coverage = response?.query_coverage;
  if (rows.length) return {
    kind: "matches",
    message: coverage?.status === "COVERED_BOUNDED_SCOPE" ? "" : "僅顯示本次回傳的索引；來源涵蓋尚不完整，不能推論其他來源沒有資料。",
  };
  if (response?.answerable_no_match === true
      && coverage?.can_state_bounded_no_match === true
      && coverage.status === "COVERED_BOUNDED_SCOPE") return {
    kind: "empty", message: "本次核准快照範圍內沒有符合條件的索引；不代表現實沒有事件。",
  };
  const blocked = coverage?.rights_blocked_sources?.length || coverage?.formal_admission?.blocked_sources?.length;
  return {
    kind: "unavailable",
    message: blocked
      ? "來源的公開 metadata 用途尚未完成准入，這次索引查詢暫不可用。"
      : "目前沒有可核對的可用索引或完整來源涵蓋，這次索引查詢暫不可用。",
  };
}
