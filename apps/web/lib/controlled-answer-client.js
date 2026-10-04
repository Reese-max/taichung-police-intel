import { queryGateway } from "./query-release-client.js";

export function buildControlledAnswerClaims(response) {
  return (Array.isArray(response?.results) ? response.results : []).map(item => ({
    schema_version: 1,
    claim_id: `publication-${item.canonical_id}`,
    text: item.title,
    claim_type: "STATUS",
    temporal_scope: "CURRENT",
    proposition: { subject: `publication:${item.canonical_id}:title`, value: item.title },
    cited_evidence_ids: [`PUB-${item.canonical_id}`],
  }));
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
  if (!["PASS", "QUALIFIED"].includes(result.gate_status) || !result.answer_evidence_receipt || !Array.isArray(result.answer)) {
    throw new Error("回答證據無法核對，請稍後重試。");
  }
  return result;
}
