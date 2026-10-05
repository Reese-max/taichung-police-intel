// FICTIONAL OFFLINE ONLY. These grants are trusted test inputs, never live rights.
// No document is fetched; approved origins come from an isolated compiled fixture.
import { documentSha256, canonicalDocumentJson } from '../../../workers/query-gateway/src/document-evidence.js';

export const hashDocumentValue = value => documentSha256(canonicalDocumentJson(value));
export const documentMetadata = ({ text: _text, ...metadata }) => metadata;
export const completion = (payload, finish = 'stop') => Response.json({ choices: [{ finish_reason: finish, message: { content: JSON.stringify(payload) } }] });

export async function rehashDocumentPermission(fixture) {
  const { policy_hash: _old, ...core } = fixture.permissions;
  fixture.permissions.policy_hash = await hashDocumentValue(core);
  fixture.env.RESEARCH_DOCUMENT_PERMISSION_HASH = fixture.permissions.policy_hash;
  return fixture.permissions.policy_hash;
}

export async function refreshDocumentResearchFixture(fixture, { refreshMetadata = true } = {}) {
  if (refreshMetadata) {
    for (const row of fixture.documents) row.content_sha256 = await documentSha256(row.text);
    fixture.permissions.approved_documents = fixture.documents.map(documentMetadata);
    if (fixture.permissions.transmission_policy) {
      fixture.permissions.transmission_policy.approved_document_bindings = await Promise.all(fixture.permissions.approved_documents.map(hashDocumentValue));
    }
  }
  return rehashDocumentPermission(fixture);
}

export async function createDocumentResearchFixture(snapshot, { texts = [
  'FICTIONAL OFFLINE DOCUMENT. 交通管制將於 16:00 開始。原因未說明。',
  'FICTIONAL OFFLINE DOCUMENT. 交通改善預算列示 120 萬元。',
], transmission = true } = {}) {
  const now = Date.now();
  const reviewedAt = new Date(now - 60_000).toISOString();
  const source = snapshot.policy.active_sources[0];
  const origin = source.approved_origins[0];
  const documents = await Promise.all(texts.map(async (text, index) => ({
    schema_version: 1, document_id: `FICTIONAL-GOLD-${index + 1}`, document_version: 'v1', source_id: source.source_id,
    evidence_type: 'WRITTEN_OFFICIAL', requested_url: `${origin}/fictional-offline-gold/document-${index + 1}`,
    official_url: `${origin}/fictional-offline-gold/document-${index + 1}`, origin,
    published_at: new Date(now - 3_600_000).toISOString(), fetched_at: new Date(now - 120_000).toISOString(),
    content_sha256: await documentSha256(text), parser_version: 'fictional-gold-text-v1', text,
  })));
  const permissions = {
    schema_version: transmission ? 2 : 1, policy_id: 'FICTIONAL-OFFLINE-RESEARCH-GOLD', policy_version: 1,
    source_policy_hash: snapshot.policy.policy_hash, governance_hash: snapshot.policy.governance_binding.governance_hash,
    reviewed_at: reviewedAt, expires_at: new Date(now + 86_400_000).toISOString(),
    sources: [{ source_id: source.source_id, status: 'APPROVED', rights_status: 'VERIFIED_DOCUMENT_PERMISSION', review_required: false,
      review_id: 'FICTIONAL-GOLD-RIGHTS', reviewed_at: reviewedAt, full_text_allowed: true, excerpt_allowed: true,
      derived_usage_allowed: true, model_transmission_allowed: transmission }],
    approved_documents: documents.map(documentMetadata),
    ...(transmission ? { transmission_policy: { provider: 'MiniMax', endpoint: 'https://api.minimax.io/v1/chat/completions', model: 'MiniMax-M2.7',
      purpose: 'GOVINTEL_DOCUMENT_SYNTHESIS_AND_CRITIQUE', review_required: false, review_id: 'FICTIONAL-TRANSMISSION', reviewed_at: reviewedAt,
      public_nonpersonal_confirmed: true, approved_document_bindings: await Promise.all(documents.map(row => hashDocumentValue(documentMetadata(row)))) } } : {}),
  };
  const env = {
    RESEARCH_ENABLED: 'true', MINIMAX_API_KEY: 'OFFLINE_FAKE_GOLD_KEY', MINIMAX_BILLING_REVIEWED: 'true',
    RESEARCH_ADMISSION: { fetch: async () => Response.json({ allowed: true }) },
    RESEARCH_DOCUMENT_BUNDLE: { schema_version: 1, permissions, documents },
    RESEARCH_DOCUMENT_SOURCE_POLICY_HASH: snapshot.policy.policy_hash,
  };
  const fixture = { documents, permissions, env };
  await rehashDocumentPermission(fixture);
  return fixture;
}

export function supportedProducer(payload) {
  const passage = payload.passages[0];
  return { claims: [{ claim_id: 'C1', text: passage.text, temporal_scope: 'HISTORICAL_OR_UNDATED',
    citations: [{ passage_id: passage.passage_id, start_utf16: 0, end_utf16: passage.text.length, quote: passage.text }] }] };
}

export function supportedCritic(payload) {
  return { reviews: payload.claims.map(({ claim_id }) => ({ claim_id, verdict: 'SUPPORTED' })) };
}
