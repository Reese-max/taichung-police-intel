const CLAIM_LABELS = {
  TIME: "時間",
  LOCATION: "地點",
  STATUS: "狀態",
  AGENCY: "機關",
  TEXT_CHANGE: "文字變更",
};
const SIDES = ["before", "after"];

function isObject(value) {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

export function claimLabel(type) {
  return CLAIM_LABELS[type] || type;
}

// Re-checks in the browser what the Python verifier already enforced: every
// quote must equal the slice of the displayed official text it points to.
export function validateChangeSummaryDemo(data) {
  if (
    data?.schema_version !== 1
    || data?.kind !== "GOVINTEL_CHANGE_SUMMARY_DEMO"
    || data?.status !== "FIXTURE_ONLY"
    || data?.production_verified !== false
    || !isObject(data.summary)
    || typeof data.before_text !== "string"
    || typeof data.after_text !== "string"
  ) return false;
  const { summary } = data;
  if (
    summary.mode !== "CHANGE_SUMMARY"
    || summary.status !== "PENDING_REVIEW"
    || summary.published !== false
    || typeof summary.official_url !== "string"
    || !summary.official_url.startsWith("https://")
    || !isObject(summary.ai_disclosure)
    || !Array.isArray(summary.statements)
    || summary.statements.length === 0
  ) return false;
  const texts = { before: data.before_text, after: data.after_text };
  return summary.statements.every((statement) => (
    isObject(statement)
    && typeof statement.text === "string"
    && Array.isArray(statement.evidence)
    && statement.evidence.length > 0
    && statement.evidence.every((span) => (
      SIDES.includes(span?.side)
      && Number.isInteger(span.start)
      && Number.isInteger(span.end)
      && span.start >= 0
      && span.end > span.start
      && texts[span.side].slice(span.start, span.end) === span.quote
    ))
  ));
}

// Split one side's text into plain and quoted segments for <mark> rendering.
export function highlightSegments(text, spans) {
  const sorted = [...spans].sort((a, b) => a.start - b.start || b.end - a.end);
  const segments = [];
  let cursor = 0;
  for (const span of sorted) {
    if (span.end <= cursor) continue;
    const start = Math.max(span.start, cursor);
    if (start > cursor) segments.push({ text: text.slice(cursor, start), quoted: false });
    segments.push({ text: text.slice(start, span.end), quoted: true });
    cursor = span.end;
  }
  if (cursor < text.length) segments.push({ text: text.slice(cursor), quoted: false });
  return segments;
}

export function spansForSide(statements, side) {
  return statements.flatMap((statement) => statement.evidence.filter((span) => span.side === side));
}
