import test from "node:test";
import assert from "node:assert/strict";
import {webcrypto} from "node:crypto";
import {createHandler,SOURCE_URLS} from "../../../workers/source-egress-probe/src/index.js";

const NOW = Date.parse("2026-10-06T03:00:00Z");
const TOKEN = "a".repeat(64);
const env = {PROBE_TOKEN:TOKEN,EXPIRES_AT:String(NOW+600000)};
const request = (query="source=S-029",headers={Authorization:`Bearer ${TOKEN}`}) => new Request(`https://probe.example/probe?${query}`,{headers});

test("unauthenticated and expired requests never fetch an origin", async()=>{
  let calls=0;const handler=createHandler({now:()=>NOW,cryptoImpl:webcrypto,fetchImpl:async()=>{calls++;return new Response("unused");}});
  for(const [req,config] of [[request("source=S-029",{}),env],[request(),{...env,EXPIRES_AT:String(NOW-1)}],[request(),{...env,EXPIRES_AT:String(NOW+3600000)}]]) {
    assert.equal((await handler(req,config)).status,403);
  }
  assert.equal(calls,0);
});

test("probe accepts exactly three fixed WWW origins, never an arbitrary URL or apex", async()=>{
  let calls=0;const handler=createHandler({now:()=>NOW,cryptoImpl:webcrypto,fetchImpl:async()=>{calls++;return new Response("unused");}});
  for(const query of ["source=S-029&url=https://evil.example","source=__proto__","source=S-029&source=S-001","source=S-999"])assert.equal((await handler(request(query),env)).status,400);
  assert.equal(calls,0);assert.equal(Object.keys(SOURCE_URLS).length,3);
  assert.ok(Object.values(SOURCE_URLS).every(value=>new URL(value).hostname.startsWith("www.")));
});

test("original bytes and credentials are absent from a transport receipt", async()=>{
  const body="Private captured origin bytes must not be republished";let called;
  const handler=createHandler({now:()=>NOW,cryptoImpl:webcrypto,fetchImpl:async(url,options)=>{called={url,options};return new Response(body,{headers:{"Content-Type":"text/html"}});}});
  const response=await handler(request(),env);const text=await response.text();const receipt=JSON.parse(text);
  assert.equal(called.url,SOURCE_URLS["S-029"]);assert.equal(called.options.redirect,"manual");
  assert.equal(called.options.headers.Authorization,undefined);
  assert.equal(receipt.scope,"TRANSPORT_ONLY_NOT_SOURCE_RECOVERY");assert.equal(receipt.promotion_eligible,false);
  assert.equal(receipt.original_bytes_republished,false);assert.match(receipt.body_sha256,/^[a-f0-9]{64}$/);
  assert.ok(!text.includes(body));assert.ok(!text.includes(TOKEN));
});

test("redirects, oversized responses and timeouts do not become source recovery", async()=>{
  const cases=[
    {fetchImpl:async()=>new Response(null,{status:302,headers:{Location:"https://evil.example"}}),expected:"REDIRECT_NOT_FOLLOWED"},
    {fetchImpl:async()=>new Response(new Uint8Array(2*1024*1024+1)),expected:"BODY_BUDGET_EXCEEDED"},
    {fetchImpl:async()=>new Promise(()=>{}),expected:"TIMEOUT"},
  ];
  for(const item of cases){
    const handler=createHandler({now:()=>NOW,cryptoImpl:webcrypto,fetchImpl:item.fetchImpl,timeoutMs:20});
    const receipt=await(await handler(request(),env)).json();assert.equal(receipt.reason??receipt.status,item.expected);
    assert.equal(receipt.promotion_eligible,false);
  }
});
