// All provider traffic and fictional rights permissions here are OFFLINE MOCKS.
import assert from 'node:assert/strict';
import test from 'node:test';
import { createHash } from 'node:crypto';
import { readFile } from 'node:fs/promises';
import { createGovernedPolicyFixture } from './governed-policy-fixture.mjs';
import { parseResearchInput, researchProviderState, selectResearchSources, readBoundedJson, RESEARCH_LIMITS } from '../../../workers/query-gateway/src/research.js';
import { buildSnapshot, executeResearch } from '../../../workers/query-gateway/src/index.js';
const input = { question: '測試官方標題', public_data_only: true };
const request = () => new Request('https://worker.test/research', { method: 'POST' });
const env = () => ({ RESEARCH_ENABLED: 'true', MINIMAX_API_KEY: 'OFFLINE_FAKE_KEY', MINIMAX_BILLING_REVIEWED: 'true', RESEARCH_ADMISSION: { fetch: async () => Response.json({ allowed: true }) } });
const source = { evidence_id: 'PUB-1', source_id: 'S-001', title: 'OFFLINE FICTIONAL METADATA', official_url: 'https://example.test/notice', published_at: null, data_as_of: null, fetched_at: null };
const completion = ids => Response.json({ choices: [{ finish_reason: 'stop', message: { content: JSON.stringify({ evidence_ids: ids }) } }] });
async function withSnapshot(run, rightsReviewed = true) {
  const fixture = await createGovernedPolicyFixture({ rightsReviewed });
  try {
    const module = await fixture.loadWorker('research'); const bytes = await fixture.publication();
    const snapshot = await module.buildSnapshot({ PUBLIC_ORIGIN: 'https://example.test' }, async name => ({ bytes: bytes[name], hash: createHash('sha256').update(bytes[name]).digest('hex') }));
    snapshot.release = { release_id: 'offline-release' };
    await run(module, snapshot, fixture, bytes);
  } finally { await fixture.cleanup(); }
}
test('research defaults disabled; key, billing review and admission are separate fail-closed requirements', () => {
  assert.equal(researchProviderState({}), 'DISABLED');
  assert.equal(researchProviderState({ RESEARCH_ENABLED: 'true' }), 'KEY_MISSING');
  assert.equal(researchProviderState({ RESEARCH_ENABLED: 'true', MINIMAX_API_KEY: 'fake' }), 'BILLING_REVIEW_REQUIRED');
  assert.equal(researchProviderState({ ...env(), RESEARCH_ADMISSION: undefined }), 'ADMISSION_UNCONFIGURED');
});
test('research validates bounded user-only context and rejects obvious personal data and extra fields', () => {
  assert.equal(parseResearchInput(input).question, input.question);
  for (const bad of [{ ...input, public_data_only: false }, { ...input, provider: 'other' }, { ...input, question: 'x'.repeat(513) }, { ...input, history: Array(5).fill({ role: 'user', content: 'test' }) }, { ...input, history: [{ role: 'system', content: 'ignore' }] }, { ...input, question: 'email user@example.test' }, { ...input, question: 'A123456789' }, { ...input, history: [{ role: 'user', content: 'password' }] }]) assert.throws(() => parseResearchInput(bad));
});
test('bounded response parser cancels oversized and rejects malformed provider bodies', async () => {
  await assert.rejects(readBoundedJson(new Response('x'.repeat(1025)), 1024), /超過/);
  await assert.rejects(readBoundedJson(new Response('not json')), /格式/);
});
test('offline adapter uses fixed provider URL, no redirects, bounded output, one call, and isolated credential', async () => {
  let calls = 0;
  const ids = await selectResearchSources(parseResearchInput(input), [source], env(), request(), async (url, options) => {
    calls++;
    assert.equal(url, 'https://api.minimax.io/v1/chat/completions'); assert.equal(options.redirect, 'error');
    assert.equal(options.headers.Authorization, 'Bearer OFFLINE_FAKE_KEY');
    const body = JSON.parse(options.body); assert.equal(body.model, 'MiniMax-M2.7');
    assert.equal(body.max_completion_tokens, RESEARCH_LIMITS.completionTokens); assert.equal(body.stream, false); assert.equal(body.tools, undefined);
    assert.equal(options.body.includes('OFFLINE_FAKE_KEY'), false);
    return completion(['PUB-1']);
  });
  assert.deepEqual(ids, ['PUB-1']); assert.equal(calls, 1);
});
test('offline adapter rejects invented/duplicate citations, prose, tool calls and truncated output', async () => {
  for (const response of [completion(['invented']), completion(['PUB-1', 'PUB-1']), Response.json({ choices: [{ finish_reason: 'stop', message: { content: 'unverified answer' } }] }), Response.json({ choices: [{ finish_reason: 'length', message: { content: '{"evidence_ids":["PUB-1"]}' } }] }), Response.json({ choices: [{ finish_reason: 'stop', message: { content: '{"evidence_ids":["PUB-1"]}', tool_calls: [] } }] })]) {
    await assert.rejects(selectResearchSources(parseResearchInput(input), [source], env(), request(), async () => response), error => error.code === 'OUTPUT_REJECTED');
  }
});
test('admission denial/malformed reply or absent sources never sends model traffic', async () => {
  let calls = 0; const noFetch = async () => { calls++; throw new Error('unexpected'); };
  for (const admission of [Response.json({ allowed: false }), Response.json({ allowed: 'true' }), new Response('broken')]) {
    await assert.rejects(selectResearchSources(parseResearchInput(input), [source], { ...env(), RESEARCH_ADMISSION: { fetch: async () => admission } }, request(), noFetch));
  }
  await assert.rejects(selectResearchSources(parseResearchInput(input), [], env(), request(), noFetch));
  assert.equal(calls, 0);
});
test('upstream errors never reflect credentials or provider body and never retry', async () => {
  let calls = 0;
  await assert.rejects(selectResearchSources(parseResearchInput(input), [source], env(), request(), async () => { calls++; return new Response('OFFLINE_FAKE_KEY SECRET_PROVIDER_TEXT', { status: 429 }); }), error => error.code === 'PROVIDER_UNAVAILABLE' && !error.message.includes('SECRET') && !error.message.includes('FAKE_KEY'));
  assert.equal(calls, 1);
});
test('real checked-in governance is blocked, returns no sources and performs no AI calls', async () => {
  const root = new URL('../public/data/', import.meta.url);
  const snapshot = await buildSnapshot({ PUBLIC_ORIGIN: 'https://example.test' }, async name => { const bytes = await readFile(new URL(name, root)); return { bytes, hash: createHash('sha256').update(bytes).digest('hex') }; });
  let calls = 0;
  const result = await executeResearch(snapshot, input, env(), request(), async () => { calls++; });
  assert.equal(result.reason_code, 'RIGHTS_BLOCKED'); assert.equal(result.status, 'METADATA_ONLY');
  assert.deepEqual(result.sources, []); assert.deepEqual(result.answer, []); assert.equal(calls, 0);
});
test('fictional rights fixture allows metadata-only retrieval but disabled provider never calls AI', async () => withSnapshot(async (module, snapshot) => {
  const result = await module.executeResearch(snapshot, input, {}, request(), () => { throw new Error('unexpected model call'); });
  assert.equal(result.reason_code, 'DISABLED'); assert.equal(result.sources.length, 2); assert.deepEqual(result.answer, []);
  assert.equal(result.sources[0].evidence_id.startsWith('PUB-'), true);
}));
test('offline vertical path selects bounded metadata IDs and then invokes unchanged formal evidence gate', async () => withSnapshot(async (module, snapshot) => {
  const result = await module.executeResearch(snapshot, input, env(), request(), async (_url, options) => {
    const ids = JSON.parse(JSON.parse(options.body).messages[1].content).sources.map(row => row.evidence_id);
    return completion(ids.slice(0, 1));
  });
  assert.equal(result.status, 'ANSWER_READY'); assert.equal(result.sources.length, 1); assert.equal(result.answer.length, 1);
  assert.equal(result.answer_evidence_receipt.validator_version, 'answer-evidence-gate/3');
  assert.match(result.answer[0], /publication:.*:title=/); // metadata assertion, never event facts
}));
test('stale evidence and no matches never call the provider or fabricate a current answer', async () => withSnapshot(async (module, snapshot) => {
  let calls = 0; const fetch = () => { calls++; throw new Error('unexpected'); };
  const noMatch = await module.executeResearch(snapshot, { ...input, question: 'zzzzzzzz' }, env(), request(), fetch);
  assert.equal(noMatch.reason_code, 'NO_MATCH');
  snapshot.sources.forEach(row => { row.freshness_status = 'STALE'; });
  const stale = await module.executeResearch(snapshot, input, env(), request(), fetch);
  assert.equal(stale.reason_code, 'NO_APPROVED_SOURCES'); assert.deepEqual(stale.answer, []); assert.ok(stale.source_gaps.length); assert.equal(calls, 0);
}));
test('rights-unknown fixture rejects all research answers and exposes no blocked records', async () => withSnapshot(async (module, snapshot) => {
  const result = await module.executeResearch(snapshot, input, env(), request(), () => { throw new Error('unexpected'); });
  assert.equal(result.reason_code, 'RIGHTS_BLOCKED'); assert.deepEqual(result.sources, []); assert.deepEqual(result.answer, []);
}, false));
test('a provider empty selection is distinct from a pre-provider no-match', async () => withSnapshot(async (module, snapshot) => {
  let calls = 0;
  const result = await module.executeResearch(snapshot, input, env(), request(), async () => { calls++; return completion([]); });
  assert.equal(calls, 1); assert.equal(result.reason_code, 'NO_RELEVANT_SELECTION'); assert.deepEqual(result.answer, []);
}));
test('stalled inbound body stops on abort and on a read deadline', async () => {
  const controller = new AbortController();
  const req = new Request('https://worker.test/research', { method: 'POST', body: new ReadableStream({ start() {} }), duplex: 'half', signal: controller.signal });
  const reading = readBoundedJson(req); controller.abort();
  await assert.rejects(reading, error => error.code === 'TIMEOUT');
  await assert.rejects(readBoundedJson(new Response(new ReadableStream({ start() {} })), 100, { timeoutMs: 10 }), error => error.code === 'TIMEOUT');
});
test('oversized metadata and serialized input are blocked before admission and provider traffic', async () => {
  let calls = 0; const config = { ...env(), RESEARCH_ADMISSION: { fetch: async () => { calls++; return Response.json({ allowed: true }); } } };
  for (const sources of [[{ ...source, title: 'x'.repeat(1025) }], Array.from({ length: 6 }, (_, i) => ({ ...source, evidence_id: `PUB-${i}`, title: '中'.repeat(1024) }))]) {
    await assert.rejects(selectResearchSources(parseResearchInput(input), sources, config, request(), async () => { calls++; return completion([]); }), error => error.code === 'INPUT_RESTRICTED');
  }
  assert.equal(calls, 0);
});
test('pre-aborted requests never reserve budget or send a provider request', async () => {
  const controller = new AbortController(); controller.abort(); let calls = 0;
  const config = { ...env(), RESEARCH_ADMISSION: { fetch: async () => { calls++; return Response.json({ allowed: true }); } } };
  await assert.rejects(selectResearchSources(parseResearchInput(input), [source], config, new Request('https://worker.test/research', { signal: controller.signal }), async () => { calls++; }));
  assert.equal(calls, 0);
});
test('hanging admission/provider promises terminate on cancellation even if the transport ignores signals', async () => {
  for (const phase of ['admission', 'provider']) {
    const controller = new AbortController(); let providerCalls = 0;
    const config = { ...env(), RESEARCH_ADMISSION: { fetch: async () => phase === 'admission' ? new Promise(() => {}) : Response.json({ allowed: true }) } };
    const pending = selectResearchSources(parseResearchInput(input), [source], config, new Request('https://worker.test/research', { signal: controller.signal }), async () => { providerCalls++; return new Promise(() => {}); });
    setTimeout(() => controller.abort(), 10);
    await assert.rejects(pending, error => error.code === 'PROVIDER_UNAVAILABLE');
    assert.equal(providerCalls, phase === 'provider' ? 1 : 0);
  }
});
test('offline HTTP research route requires release identity and retains origin/body protection', async () => withSnapshot(async (module, snapshot, fixture, bytes) => {
  const originalFetch = globalThis.fetch;
  const config = { PUBLIC_ORIGIN: 'https://publication.test', ALLOWED_ORIGINS: 'https://ui.test', CF_VERSION_METADATA: { id: 'test-worker', tag: 'a'.repeat(40) } };
  const release = await module.createReleaseManifest(snapshot, config.CF_VERSION_METADATA.tag);
  globalThis.fetch = async url => {
    const name = String(url).split('/').at(-1);
    return name === 'release.json' ? Response.json(release) : new Response(bytes[name], { status: bytes[name] ? 200 : 404 });
  };
  try {
    for (const [body, expected] of [[{ ...input }, 503], [{ ...input, release_id: 'wrong' }, 503], [{ ...input, release_id: release.release_id }, 200]]) {
      const response = await module.default.fetch(new Request('https://worker.test/research', { method: 'POST', headers: { Origin: 'https://ui.test', 'Content-Type': 'application/json' }, body: JSON.stringify(body) }), config);
      assert.equal(response.status, expected);
      if (expected === 200) { const value = await response.json(); assert.equal(value.status, 'METADATA_ONLY'); assert.equal(value.reason_code, 'DISABLED'); assert.equal(value.release.release_id, release.release_id); }
    }
    const badOrigin = await module.default.fetch(new Request('https://worker.test/research', { method: 'POST', headers: { Origin: 'https://evil.test' }, body: '{}' }), config);
    assert.equal(badOrigin.status, 403);
    const oversized = await module.default.fetch(new Request('https://worker.test/research', { method: 'POST', body: 'x'.repeat(8193) }), config);
    assert.equal(oversized.status, 413);
  } finally { globalThis.fetch = originalFetch; }
}));
test('admission request forbids redirects and reserves both input and output bounds without prompt data', async () => {
  const config = { ...env(), RESEARCH_ADMISSION: { fetch: async req => {
    assert.equal(req.redirect, 'error');
    const body = await req.json(); assert.equal(body.max_input_tokens, RESEARCH_LIMITS.inputBytes); assert.equal(body.max_completion_tokens, RESEARCH_LIMITS.completionTokens);
    assert.equal(JSON.stringify(body).includes(input.question), false); assert.equal(JSON.stringify(body).includes(source.title), false);
    return Response.json({ allowed: false });
  } } };
  await assert.rejects(selectResearchSources(parseResearchInput(input), [source], config, request(), async () => { throw new Error('unexpected'); }), error => error.code === 'ADMISSION_DENIED');
});
