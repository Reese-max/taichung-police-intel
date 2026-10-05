// Official document revisions, official record dates and collection clocks have
// different meanings. This closed metadata projection never publishes PDF text.
const REVISION = "OFFICIAL_DOCUMENT_REVISION_DATE";
const BASES = new Set([REVISION, "OFFICIAL_LIST_TITLE_DATE", "OFFICIAL_API_RECORD_DATE"]);
const EVIDENCE_KEYS = new Set(["date_basis", "document_revision_at", "official_url", "content_sha256", "page_number"]);

function instant(value) {
  const match = typeof value === "string" && /^(\d{4}-\d{2}-\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.\d{1,6})?(?:Z|([+-])(\d{2}):(\d{2}))$/.exec(value);
  const calendar = match && Date.parse(`${match[1]}T00:00:00Z`);
  if (!match || Number(match[2]) > 23 || Number(match[3]) > 59 || Number(match[4]) > 59
      || (match[5] && (Number(match[6]) > 23 || Number(match[7]) > 59)) || !Number.isFinite(calendar)
      || new Date(calendar).toISOString().slice(0, 10) !== match[1] || !Number.isFinite(Date.parse(value))) {
    throw new Error("invalid official date timestamp");
  }
  return Date.parse(value);
}

export function projectDateEvidence(evidence, expectedDate, approvedOrigins) {
  if (!evidence || typeof evidence !== "object" || Array.isArray(evidence)
      || Object.keys(evidence).some(key => !EVIDENCE_KEYS.has(key) && key !== "excerpt")
      || [...EVIDENCE_KEYS].some(key => !Object.hasOwn(evidence, key))) throw new Error("invalid official date evidence fields");
  if (evidence.date_basis !== REVISION || instant(evidence.document_revision_at) !== instant(expectedDate)) {
    throw new Error("official date evidence does not match its revision date");
  }
  let url;
  try { url = new URL(evidence.official_url); } catch { throw new Error("invalid official date evidence URL"); }
  if (typeof evidence.official_url !== "string" || url.protocol !== "https:" || url.username || url.password
      || !approvedOrigins?.includes(url.origin) || !/\.pdf$/i.test(url.pathname)) throw new Error("official date evidence origin or document is not approved");
  if (typeof evidence.content_sha256 !== "string" || !/^[a-f0-9]{64}$/.test(evidence.content_sha256)
      || !Number.isInteger(evidence.page_number) || evidence.page_number < 1
      || (Object.hasOwn(evidence, "excerpt") && typeof evidence.excerpt !== "string")) throw new Error("invalid official date evidence hash or page");
  return Object.fromEntries([...EVIDENCE_KEYS].map(key => [key, evidence[key]]));
}

export function projectPublicationDates(item, approvedOrigins) {
  const basis = item.date_basis;
  const revision = item.document_revision_at;
  const evidence = item.date_evidence;
  if (basis == null && revision == null && evidence == null) return {};
  if (!BASES.has(basis)) throw new Error("invalid official date basis");
  if (basis !== REVISION) {
    if (revision != null || evidence != null) throw new Error("official revision metadata requires a revision basis");
    instant(item.published_at);
    return { date_basis: basis };
  }
  instant(revision);
  return { document_revision_at: revision, date_basis: basis,
    date_evidence: projectDateEvidence(evidence, revision, approvedOrigins) };
}

export function projectSourceDates(source, approvedOrigins) {
  const basis = source.data_as_of_basis;
  const evidence = source.data_as_of_evidence;
  const scope = source.data_as_of_scope;
  if (basis == null && evidence == null && scope == null) return {};
  if (!BASES.has(basis)) throw new Error("invalid source official date basis");
  instant(source.data_as_of);
  const result = { data_as_of_basis: basis };
  if (basis === REVISION) result.data_as_of_evidence = projectDateEvidence(evidence, source.data_as_of, approvedOrigins);
  else if (evidence != null) throw new Error("source revision evidence requires a revision basis");
  if (scope != null) {
    const scopes = [REVISION, "OFFICIAL_LIST_TITLE_DATE"].includes(basis) ? ["LATEST_EVIDENCED_DOCUMENT_VERSION"] : basis === "OFFICIAL_API_RECORD_DATE" ? ["COLLECTION_WINDOW", "OBSERVED_API_PAGE"] : [];
    if (!scopes.includes(scope)) throw new Error("invalid source official date scope");
    result.data_as_of_scope = scope;
  }
  return result;
}

export function publicationDate(item, lang = "zh") {
  if (item.date_basis === "OFFICIAL_API_RECORD_DATE" && item.published_at) return { label: lang === "en" ? "Official record date" : "官方記錄日期", value: item.published_at };
  if (item.date_basis === "OFFICIAL_LIST_TITLE_DATE" && item.published_at) return { label: lang === "en" ? "Official list date" : "官方列表日期", value: item.published_at };
  if (item.published_at) return { label: lang === "en" ? "Publication date" : "發布時間", value: item.published_at };
  if (item.date_basis === REVISION && item.document_revision_at) return { label: lang === "en" ? "Official revision date" : "官方修訂日期", value: item.document_revision_at };
  return { label: lang === "en" ? "Publication date" : "發布時間", value: null };
}

export function sourceDateLabel(source, lang = "zh") {
  if (source.data_as_of_basis === REVISION) return lang === "en" ? "Official revision date (checked attachments)" : "官方修訂日期（已核對附件）";
  if (source.data_as_of_basis === "OFFICIAL_API_RECORD_DATE") return lang === "en" ? "Official record date" : "官方記錄日期";
  if (source.data_as_of_basis === "OFFICIAL_LIST_TITLE_DATE") return lang === "en" ? "Official list date" : "官方列表日期";
  return lang === "en" ? "Official data as of" : "官方資料截至";
}
