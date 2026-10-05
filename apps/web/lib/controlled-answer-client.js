import { queryGateway } from "./query-release-client.js";

const FACTUAL_TYPES = new Set(["TIME", "LOCATION", "STATUS", "CAUSE", "STATISTIC", "AGENCY"]);
const SUPPORT_STATUSES = new Set(["SUPPORTED", "PARTIAL", "UNSUPPORTED", "CONFLICT", "STALE"]);
const STATEMENT_KEYS = new Set(["text", "claim_type", "temporal_scope", "proposition", "cited_evidence_ids"]);

function object(value) {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function boundedText(value, max = 2000) {
  return typeof value === "string" && value.trim() === value && value.length > 0 && value.length <= max;
}

function normalizeTerm(value) {
  return String(value).normalize("NFC").replace(/\s+/g, "").trim();
}

export function draftStatementText(claimType, proposition) {
  if (!object(proposition) || Object.keys(proposition).length !== 2
      || !boundedText(proposition.subject, 512) || !Object.hasOwn(proposition, "value")) throw new Error("草稿 claim 缺少逐句命題。");
  // The trusted Worker catalog binds these subjects to index metadata, not to
  // event facts that might be mentioned inside a publication's title.
  if (/^publication:.*:(?:title|source_id)$/.test(normalizeTerm(proposition.subject)) && claimType !== "STATUS") {
    throw new Error("索引 metadata 命題僅能使用 STATUS，不能當作其他已核對事實。");
  }
  const value = proposition.value;
  if (claimType === "STATISTIC") {
    if (!object(value) || Object.keys(value).length !== 4
        || !["value", "period", "geography", "unit"].every(key => boundedText(value[key], 128))
        || !/^-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?$/.test(value.value)) throw new Error("草稿統計 claim 缺少完整範圍。");
    return `${value.value} ${value.unit}（${value.period}，${value.geography}）`;
  }
  if (!boundedText(value) && !(typeof value === "number" && Number.isFinite(value))) throw new Error("草稿 claim 數值無法核對。");
  return String(value);
}

export function buildPublicationMetadataDraft(response) {
  const results = response?.results;
  if (!Array.isArray(results) || results.length < 1 || results.length > 32
      || results.some(item => !boundedText(item?.canonical_id, 256) || !boundedText(item?.title))) {
    throw new Error("查詢缺少可核對的資料，未建立逐句草稿。");
  }
  return { schema_version: 1, statements: results.map(item => ({
    text: item.title, claim_type: "STATUS", temporal_scope: "CURRENT",
    proposition: { subject: `publication:${item.canonical_id}:title`, value: item.title },
    cited_evidence_ids: [`PUB-${item.canonical_id}`],
  })) };
}

export function buildControlledAnswerClaims(response) {
  const supplied = Object.hasOwn(response ?? {}, "draft");
  if (!supplied && Object.hasOwn(response ?? {}, "located_facts")) throw new Error("草稿事實尚未逐句對應，未產生回答。");
  const draft = supplied ? response.draft : buildPublicationMetadataDraft(response);
  if (!object(draft) || draft.schema_version !== 1 || Object.keys(draft).some(key => !["schema_version", "statements"].includes(key))
      || !Array.isArray(draft.statements) || draft.statements.length < 1 || draft.statements.length > 32) {
    throw new Error("草稿必須逐句提供 typed claims；任意文字不能略過核對。");
  }
  const claims = draft.statements.map((statement, index) => {
    if (!object(statement) || Object.keys(statement).some(key => !STATEMENT_KEYS.has(key))
        || !FACTUAL_TYPES.has(statement.claim_type) || !["CURRENT", "HISTORICAL"].includes(statement.temporal_scope)
        || !boundedText(statement.text) || !Array.isArray(statement.cited_evidence_ids)
        || statement.cited_evidence_ids.length < 1 || statement.cited_evidence_ids.length > 16
        || statement.cited_evidence_ids.some(id => !boundedText(id, 256))
        || new Set(statement.cited_evidence_ids).size !== statement.cited_evidence_ids.length
        || statement.text !== draftStatementText(statement.claim_type, statement.proposition)) {
      throw new Error("草稿每個 claim 必須逐句具備型別、命題與引用；未對應文字不能釋出。");
    }
    return { schema_version: 1,
      claim_id: supplied ? `draft-${index + 1}` : `publication-${response.results[index].canonical_id}`,
      ...statement };
  });
  if (new Set(claims.map(c => c.claim_id)).size !== claims.length) throw new Error("草稿 claim ID 重複。");
  return claims;
}

async function answerHash(answer) {
  if (!globalThis.crypto?.subtle) throw new Error("回答證據收據 hash 無法核對。");
  const bytes = new TextEncoder().encode(JSON.stringify(answer));
  const digest = await globalThis.crypto.subtle.digest("SHA-256", bytes);
  return Array.from(new Uint8Array(digest), byte => byte.toString(16).padStart(2, "0")).join("");
}

function receiptPropositionMatches(entry, claim) {
  if (!Array.isArray(entry.propositions) || entry.propositions.length !== 1) return false;
  const normalize = normalizeTerm;
  const actual = entry.propositions[0];
  const proposed = claim.proposition;
  if (!object(actual) || actual.subject !== normalize(proposed.subject)) return false;
  if (claim.claim_type !== "STATISTIC") return actual.value === normalize(proposed.value) && actual.statistic === undefined;
  const statistic = { value: proposed.value.value, period: normalize(proposed.value.period),
    geography: normalize(proposed.value.geography), unit: normalize(proposed.value.unit) };
  return actual.value === `${statistic.value} ${statistic.unit}（${statistic.period}，${statistic.geography}）`
    && object(actual.statistic) && Object.keys(actual.statistic).length === 4
    && Object.keys(statistic).every(key => actual.statistic[key] === statistic[key]);
}

export async function validateControlledAnswer(endpoint, response, context = null) {
  const claims = buildControlledAnswerClaims(response);
  if (!claims.length || !response.query_generation_id) throw new Error("查詢缺少可核對的資料與版本。");
  const result = await queryGateway(endpoint, "validate_answer", {
    claims, expected_generation: response.query_generation_id,
  }, context);
  if (result.query_generation_id !== response.query_generation_id || result.publication_hash !== response.publication_hash) {
    throw new Error("回答資料版本已變更，請重新查詢。");
  }
  const receipt = result.answer_evidence_receipt;
  if (!["PASS", "QUALIFIED"].includes(result.gate_status) || !receipt || !Array.isArray(result.answer)
      || result.answer.some(text => !boundedText(text, 8000)) || receipt.schema_version !== 1
      || receipt.validator_version !== "answer-evidence-gate/3" || receipt.renderer_version !== "controlled-answer-renderer/1"
      || receipt.gate_status !== result.gate_status
      || receipt.publication_hash !== response.publication_hash || !Array.isArray(receipt.claims)
      || receipt.claims.length !== claims.length || !Array.isArray(receipt.claim_ids)
      || JSON.stringify(receipt.claim_ids) !== JSON.stringify(claims.map(c => c.claim_id))
      || receipt.claims.some((entry, index) => entry?.claim_id !== claims[index].claim_id
        || entry.claim_type !== claims[index].claim_type || entry.original_text !== claims[index].text
        || !receiptPropositionMatches(entry, claims[index])
        || !SUPPORT_STATUSES.has(entry.support_status))
      || receipt.answer_sha256 !== await answerHash(result.answer)) {
    throw new Error("回答證據無法核對，請稍後重試。");
  }
  return result;
}

export async function runControlledAnswerPipeline(endpoint, queryArguments, context = null, draftAdapter = null) {
  const response = await queryGateway(endpoint, "search_evidence", queryArguments, context);
  if (!Array.isArray(response.results)) throw new Error("查詢資料無法驗證，未建立回答。");
  if (!response.results.length) {
    if (Object.hasOwn(response, "draft") || Object.hasOwn(response, "located_facts")) throw new Error("零結果含未逐句核對的草稿，未產生回答。");
    return { response, answer: null, draft_kind: "NO_MATCH" };
  }
  if (!draftAdapter && !Object.hasOwn(response, "draft") && Object.hasOwn(response, "located_facts")) {
    throw new Error("草稿事實尚未逐句對應，未產生回答。");
  }
  // The adapter returns a bounded structured draft, never trusted evidence. All
  // statements go to the server-owned shared gate before an answer is returned.
  const draft = draftAdapter ? await draftAdapter(structuredClone(response))
    : (Object.hasOwn(response, "draft") ? response.draft : buildPublicationMetadataDraft(response));
  const answer = await validateControlledAnswer(endpoint, { ...response, draft }, context);
  return { response, answer, draft_kind: draftAdapter ? "STRUCTURED_ADAPTER" : "PUBLICATION_METADATA" };
}
