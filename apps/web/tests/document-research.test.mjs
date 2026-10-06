// Offline route tests: every grant and provider response is fictional.
import assert from 'node:assert/strict';
import test, { before, after } from 'node:test';
import { createHash } from 'node:crypto';
import { readFile } from 'node:fs/promises';
import { createGovernedPolicyFixture } from './governed-policy-fixture.mjs';
import { createDocumentResearchFixture, refreshDocumentResearchFixture, rehashDocumentPermission, completion, supportedProducer, supportedCritic } from './document-research-fixture.mjs';
import { parseResearchInput } from '../../../workers/query-gateway/src/research.js';
import { buildSnapshot, executeResearch } from '../../../workers/query-gateway/src/index.js';
import { validateResearchResponse } from '../lib/research-client.js';

let fixture, worker, snapshot;
const request = signal => new Request('https://worker.test/research', { method: 'POST', ...(signal ? { signal } : {}) });
const input = mode => ({ question: '交通', public_data_only: true, mode });
function controlledClock() {
  const OriginalDate = globalThis.Date; let now = OriginalDate.now();
  globalThis.Date = class extends OriginalDate {
    constructor(...args) { super(...(args.length ? args : [now])); }
    static now() { return now; }
  };
  return { advance: ms => { now += ms; }, restore: () => { globalThis.Date = OriginalDate; } };
}

before(async () => {
  fixture = await createGovernedPolicyFixture(); worker = await fixture.loadWorker('document-route');
  const data = await fixture.publication();
  snapshot = await worker.buildSnapshot({ PUBLIC_ORIGIN: 'https://example.test' }, async name => ({ bytes: data[name], hash: createHash('sha256').update(data[name]).digest('hex') }));
  snapshot.release = { release_id: 'fictional-document-release' };
});
after(async () => { await fixture?.cleanup(); });
function provider(producer = supportedProducer, critic = supportedCritic) {
  let calls = 0;
  return async (_url, options) => { const payload = JSON.parse(JSON.parse(options.body).messages[1].content); return completion(calls++ ? critic(payload) : producer(payload)); };
}

test('mode parsing defaults to metadata, rejects arbitrary mode and enforces explicit UTF8 question bound', () => {
  assert.equal(parseResearchInput({ question: '研究', public_data_only: true }).mode, 'metadata');
  for (const mode of [null, true, 'other', 'DOCUMENTS']) assert.throws(() => parseResearchInput({ ...input(mode) }), error => error.code === 'INVALID_ARGUMENTS');
  assert.throws(() => parseResearchInput({ ...input('documents'), question: '界'.repeat(171) }), error => error.code === 'DOCUMENT_QUERY_TOO_LARGE');
  assert.equal(parseResearchInput({ ...input('metadata'), question: '界'.repeat(171) }).question.length, 171);
});

test('actual checked-in schema1 is zero-admission in all modes, even with a poison bundle getter', async () => {
  const root = new URL('../public/data/', import.meta.url);
  const real = await buildSnapshot({ PUBLIC_ORIGIN: 'https://example.test' }, async name => {
    const bytes = await readFile(new URL(name, root)); return { bytes, hash: createHash('sha256').update(bytes).digest('hex') };
  });
  let calls = 0;
  const env = { get RESEARCH_DOCUMENT_BUNDLE() { throw new Error('Must not read docs before formal admission'); } };
  for (const mode of ['metadata', 'documents', 'synthesis']) {
    const result = await executeResearch(real, input(mode), env, request(), () => { calls++; });
    assert.equal(result.reason_code, 'RIGHTS_BLOCKED'); assert.deepEqual(result.sources, []); assert.deepEqual(result.answer, []);
    assert.equal(result.document_evidence, undefined); assert.equal(result.synthesis, undefined);
  }
  assert.equal(calls, 0);
});

test('real document/synthesis route outputs pass client validation and never use formal answer status', async () => {
  for (const mode of ['documents', 'synthesis']) {
    const docs = await createDocumentResearchFixture(snapshot);
    const result = await worker.executeResearch(snapshot, input(mode), docs.env, request(), provider());
    assert.equal(result.status, mode === 'documents' ? 'EXTRACTS_READY' : 'SYNTHESIS_DRAFT');
    assert.equal(result.mode, mode); assert.deepEqual(result.answer, []); assert.equal(result.answer_evidence_receipt, undefined);
    assert.doesNotThrow(() => validateResearchResponse(result, '交通', mode));
    for (const gap of result.source_gaps) assert.equal(typeof gap.reason, 'string');
  }
});

test('every synthesis call separately reserves budget and critic sees original passages, not only the draft', async () => {
  const docs = await createDocumentResearchFixture(snapshot); let admitted = 0, calls = 0; const passages = [];
  docs.env.RESEARCH_ADMISSION = { fetch: async req => {
    const body = await req.json(); admitted++; assert.equal(body.max_input_tokens, 16384); assert.equal(body.max_completion_tokens, 1024);
    assert.equal(JSON.stringify(body).includes(docs.documents[0].text), false); return Response.json({ allowed: true });
  } };
  const result = await worker.executeResearch(snapshot, input('synthesis'), docs.env, request(), async (_url, options) => {
    calls++; assert.equal(admitted, calls); assert.equal(options.redirect, 'error');
    const payload = JSON.parse(JSON.parse(options.body).messages[1].content); passages.push(payload.passages);
    return completion(calls === 1 ? supportedProducer(payload) : supportedCritic(payload));
  });
  assert.equal(result.status, 'SYNTHESIS_DRAFT'); assert.equal(calls, 2); assert.equal(admitted, 2); assert.deepEqual(passages[0], passages[1]);
  assert.notEqual(result.synthesis.receipt.producer_run_id, result.synthesis.receipt.critic_run_id);
  assert.equal(result.provider_transmission_attempted, true);
});

test('a denied second reservation reports prior transmission truthfully and never leaks generated claims', async () => {
  const docs = await createDocumentResearchFixture(snapshot); let admitted = 0, calls = 0;
  docs.env.RESEARCH_ADMISSION = { fetch: async () => Response.json({ allowed: ++admitted === 1 }) };
  const result = await worker.executeResearch(snapshot, input('synthesis'), docs.env, request(), async (_url, options) => {
    calls++; return completion(supportedProducer(JSON.parse(JSON.parse(options.body).messages[1].content)));
  });
  assert.equal(admitted, 2); assert.equal(calls, 1); assert.equal(result.reason_code, 'REVIEW_ADMISSION_DENIED');
  assert.equal(result.provider_transmission_attempted, true); assert.equal(result.synthesis, undefined); assert.deepEqual(result.answer, []);
  assert.doesNotThrow(() => validateResearchResponse(result, '交通', 'synthesis'));
});

test('same document ID/version from distinct sources keeps distinct immutable source identities', async () => {
  const docs = await createDocumentResearchFixture(snapshot), secondSource = snapshot.policy.active_sources[1];
  docs.documents[1].document_id = docs.documents[0].document_id;
  Object.assign(docs.documents[1], { source_id: secondSource.source_id, origin: secondSource.approved_origins[0], requested_url: `${secondSource.approved_origins[0]}/fictional-document`, official_url: `${secondSource.approved_origins[0]}/fictional-document` });
  docs.permissions.sources.push({ ...docs.permissions.sources[0], source_id: secondSource.source_id });
  await refreshDocumentResearchFixture(docs);
  const result = await worker.executeResearch(snapshot, input('documents'), docs.env, request(), () => { throw new Error('No model'); });
  assert.equal(result.status, 'EXTRACTS_READY'); assert.equal(result.sources.length, 2); assert.equal(new Set(result.sources.map(row => row.evidence_id)).size, 2);
  assert.doesNotThrow(() => validateResearchResponse(result, '交通', 'documents'));
});

test('different quote text at duplicate cross-claim offsets rejects before a critic reservation', async () => {
  const docs = await createDocumentResearchFixture(snapshot); let calls = 0, admitted = 0;
  docs.env.RESEARCH_ADMISSION = { fetch: async () => { admitted++; return Response.json({ allowed: true }); } };
  const result = await worker.executeResearch(snapshot, input('synthesis'), docs.env, request(), async (_url, options) => {
    calls++; const draft = supportedProducer(JSON.parse(JSON.parse(options.body).messages[1].content));
    const duplicate = structuredClone(draft.claims[0]); duplicate.claim_id = 'C2'; duplicate.citations[0].quote = 'Invented quote';
    draft.claims.unshift(duplicate); return completion(draft);
  });
  assert.equal(calls, 1); assert.equal(admitted, 1); assert.equal(result.reason_code, 'QUOTE_NOT_EXACT'); assert.equal(result.provider_transmission_attempted, true);
});

test('expiry during admission is checked at dispatch and releases neither model traffic nor cached excerpts', async () => {
  const clock = controlledClock(); try {
  const docs = await createDocumentResearchFixture(snapshot); docs.permissions.expires_at = new Date(Date.now() + 120).toISOString();
  await rehashDocumentPermission(docs); let calls = 0;
  docs.env.RESEARCH_ADMISSION = { fetch: async () => { clock.advance(150); return Response.json({ allowed: true }); } };
  const result = await worker.executeResearch(snapshot, input('synthesis'), docs.env, request(), async () => { calls++; throw new Error('Must not dispatch expired rights'); });
  assert.equal(calls, 0); assert.equal(result.reason_code, 'PERMISSION_EXPIRED_OR_UNREVIEWED'); assert.equal(result.provider_transmission_attempted, false);
  assert.equal(result.status, 'METADATA_ONLY'); assert.equal(result.document_evidence, undefined); assert.deepEqual(result.sources, []);
  } finally { clock.restore(); }
});

test('expiry during provider work withholds cached excerpts and all generated text while reporting prior transmission', async () => {
  const clock = controlledClock(); try {
  const docs = await createDocumentResearchFixture(snapshot); docs.permissions.expires_at = new Date(Date.now() + 120).toISOString();
  await rehashDocumentPermission(docs); let calls = 0;
  const result = await worker.executeResearch(snapshot, input('synthesis'), docs.env, request(), async (_url, options) => {
    calls++; clock.advance(150); return completion(supportedProducer(JSON.parse(JSON.parse(options.body).messages[1].content)));
  });
  assert.equal(calls, 1); assert.equal(result.reason_code, 'PERMISSION_EXPIRED_OR_UNREVIEWED'); assert.equal(result.provider_transmission_attempted, true);
  assert.equal(result.status, 'METADATA_ONLY'); assert.equal(result.document_evidence, undefined); assert.deepEqual(result.sources, []);
  } finally { clock.restore(); }
});

test('public/nonpersonal confirmation and exact MiniMax destination/purpose cannot be inferred', async () => {
  for (const change of [{ public_nonpersonal_confirmed: false }, { endpoint: 'https://other.invalid/chat' }, { model: 'other' }, { provider: 'Other' }, { purpose: 'OTHER' }, { review_required: true }]) {
    const docs = await createDocumentResearchFixture(snapshot); Object.assign(docs.permissions.transmission_policy, change); await rehashDocumentPermission(docs);
    let calls = 0; const result = await worker.executeResearch(snapshot, input('synthesis'), docs.env, request(), async () => { calls++; });
    assert.equal(result.reason_code, 'MODEL_TRANSMISSION_NOT_APPROVED'); assert.equal(calls, 0); assert.equal(result.document_evidence, undefined);
  }
});

test('currentness, numeric zero, completeness, implementation and safety claims stay held despite agreeing critic', async () => {
  const docs = await createDocumentResearchFixture(snapshot);
  for (const claimText of ['沒有任何交通事件。', 'There are 0 events', 'The road is open at present', 'All sources prove completed implementation', '目前交通安全有保證', '已全面完工', 'There is no risk', 'All cases are done']) {
    const result = await worker.executeResearch(snapshot, input('synthesis'), docs.env, request(), provider(payload => {
      const draft = supportedProducer(payload); draft.claims[0].text = claimText; return draft;
    }));
    assert.equal(result.status, 'EXTRACTS_READY', claimText); assert.deepEqual(result.synthesis.claims, []);
    assert.equal(result.synthesis.held_claims[0].reason_code, 'UNSUPPORTED_TEMPORAL_OR_SCOPE');
    assert.equal(JSON.stringify(result).includes(claimText), false); assert.equal(result.provider_transmission_attempted, true);
    assert.doesNotThrow(() => validateResearchResponse(result, '交通', 'synthesis'));
  }
});
