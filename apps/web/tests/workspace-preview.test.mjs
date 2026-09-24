import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import * as m from '../public/workspace-preview/model.mjs';
const clone = structuredClone;
const fresh = '2026-09-17T08:00:00+08:00';
function fixture() {
  const sources=m.EXPECTED_SOURCES.map(id=>({source_id:id,source_name:id,source_health:'PASS',window_completeness:'COMPLETE_WITH_ITEMS',last_checked_at:fresh}));
  return [{schema_version:1,collection_run_id:'test',generated_at:fresh,items:[{stable_id:'one',title:'公告',source_id:'S-004',official_url:'https://www.tccc.gov.tw/'}]},
    {schema_version:1,generated_at:fresh,sources,latest_collection_run:{collection_run_id:'test',status:'SUCCEEDED'}},
    {schema_version:1,source_collection_run_id:'test',generated_at:fresh,source_status_generated_at:fresh,snapshot_complete:true,publication_status:'READY'}];
}
const now=Date.parse(fresh)+1000;
test('six route names are bounded and malformed routes fall back',()=>{assert.equal(m.VIEWS.length,6);assert.equal(m.readRoute('#view=javascript:alert(1)&mode=evil&event=unknown').view,'overview');assert.equal(m.readRoute('#view=ask&mode=snapshot').mode,'snapshot');});
test('route roundtrip preserves allowed event and mode',()=>{const r={view:'event',mode:'demo',event:'demo-fraud'};assert.deepEqual(m.readRoute(m.routeHash(r)),r);});
test('text is escaped, not interpreted as markup',()=>assert.equal(m.escapeHTML('<img x="a">&\''),'&lt;img x=&quot;a&quot;&gt;&amp;&#39;'));
test('official links reject script, credentials and disguised hosts',()=>{for(const url of ['javascript:alert(1)','http://www.tccc.gov.tw','https://gov.tw.evil.test','https://www.tccc.gov.tw@evil.test','https://user@www.tccc.gov.tw','https://www.tccc.gov.tw:444'])assert.equal(m.safeOfficialURL(url),null);assert.equal(m.safeOfficialURL('https://www.tccc.gov.tw/'),'https://www.tccc.gov.tw/');});
test('demo data is three separate events, never official claims',()=>{assert.equal(m.DEMO_EVENTS.length,3);assert.equal(new Set(m.EVENT_IDS).size,3);assert.equal(m.initialDemo().synthetic,true);});
test('demo search filters category and literal terms',()=>{assert.equal(m.searchDemo('西屯')[0].id,'demo-activity');assert.equal(m.searchDemo('','反詐')[0].id,'demo-fraud');assert.equal(m.searchDemo('西屯','反詐').length,0);});
test('search is bounded and does not parse injected HTML',()=>{assert.throws(()=>m.searchDemo('a'.repeat(201)));assert.equal(m.searchDemo('<script>').length,0);});
test('storage failure does not claim save success',()=>{const result=m.persistDemo({setItem(){throw Error('quota');}},m.initialDemo());assert.equal(result.ok,false);});
test('corrupt stored state is not silently overwritten',()=>{let wrote=false;const result=m.readDemo({getItem(){return 'broken';},setItem(){wrote=true;}});assert.ok(result.error);assert.equal(wrote,false);});
test('stored private extras are not retained',()=>{const s=m.validateDemo({...m.initialDemo(),private_note:'x'});assert.equal('private_note' in s,false);});
test('duplicate watch IDs are rejected',()=>assert.throws(()=>m.validateDemo({...m.initialDemo(),watched:['demo-fraud','demo-fraud']})));
test('forged or malformed version receipt is rejected',()=>{assert.throws(()=>m.validateDemo({...m.initialDemo(),saved:[{version:2,generation:'fake',confirmed_at:fresh}]}));});
test('conflict, stale and missing review cannot confirm',()=>{const s=m.initialDemo();for(const scenario of ['conflict','stale','aligned'])assert.throws(()=>m.confirmDemo({...s,scenario},null));});
test('wrong generation cannot confirm aligned state',()=>assert.throws(()=>m.confirmDemo({...m.initialDemo(),scenario:'aligned'},'DEMO-conflict-v2')));
test('confirmed v2 is idempotent and v1 stays immutable',()=>{const initial={...m.initialDemo(),scenario:'aligned'};const v1=m.demoExport(initial,1);const saved=m.confirmDemo(initial,m.demoGeneration(initial),new Date(fresh));assert.equal(saved.saved.length,1);assert.deepEqual(m.demoExport(saved,1),v1);assert.equal(m.demoExport(saved,2).traffic_control_start,'16:00');assert.deepEqual(m.confirmDemo(saved,m.demoGeneration(saved)),saved);});
test('unconfirmed v2 cannot be exported',()=>assert.throws(()=>m.demoExport(m.initialDemo(),2)));
test('every export states synthetic and non-operational use',()=>{const v=m.demoExport(m.initialDemo(),1);assert.equal(v.synthetic,true);assert.match(v.purpose,/非正式/);assert.ok(v.limitations.length);});
test('real checked-in snapshot is readable without being deployment-verified',async()=>{const b=await Promise.all(['intelligence-feed','source-status','v2-daily-brief'].map(async n=>JSON.parse(await readFile(new URL(`../public/data/${n}.json`,import.meta.url),'utf8'))));const s=m.inspectSnapshot(...b);assert.equal(s.publication_verified,false);assert.ok(s.items.length>0);});
test('recent snapshot is not automatically verified publication',()=>{const s=m.inspectSnapshot(...fixture(),now);assert.equal(s.status,'SNAPSHOT_RECENT');assert.equal(s.freshness,'RECENT');assert.equal(s.completeness,'COMPLETE');assert.equal(s.publication_verified,false);});
test('old snapshot reports stale',()=>assert.equal(m.inspectSnapshot(...fixture(),now+86400000).status,'STALE'));
test('partial data is not zero or complete',()=>{const b=fixture();b[1].sources[0].window_completeness='PARTIAL';assert.equal(m.inspectSnapshot(...b,now).status,'PARTIAL');});
test('partial remains primary while stale is independently visible',()=>{const b=fixture();b[1].sources[0].window_completeness='PARTIAL';const s=m.inspectSnapshot(...b,now+86400000);assert.equal(s.status,'PARTIAL');assert.equal(s.completeness,'PARTIAL');assert.equal(s.freshness,'STALE');assert.equal(s.partial,true);assert.equal(s.stale,true);});
test('failed source preserves partial state',()=>{const b=fixture();b[1].sources[0].source_health='FAILED';assert.equal(m.inspectSnapshot(...b,now).partial,true);});
test('future clock is unknown',()=>assert.equal(m.inspectSnapshot(...fixture(),now-86400000).status,'UNKNOWN'));
test('unknown last-check time remains unknown',()=>{const b=fixture();b[1].sources[0].last_checked_at=null;assert.equal(m.inspectSnapshot(...b,now).status,'UNKNOWN');});
test('mixed generations are rejected',()=>{const b=fixture();b[2].source_collection_run_id='other';assert.throws(()=>m.inspectSnapshot(...b,now),/GENERATION/);});
test('source replacement or omission fails closed',()=>{const b=fixture();b[1].sources[0].source_id='S-999';assert.throws(()=>m.inspectSnapshot(...b,now),/COVERAGE/);});
test('non-object and duplicate rows are rejected',()=>{const b=fixture();b[0].items.push(null);assert.throws(()=>m.inspectSnapshot(...b,now));const c=fixture();c[0].items.push(clone(c[0].items[0]));assert.throws(()=>m.inspectSnapshot(...c,now),/ROW/);});
test('bounded no-match is possible without asserting world coverage',()=>{const b=fixture();b[0].items=[];const s=m.inspectSnapshot(...b,now);assert.equal(s.items.length,0);assert.match(s.limitation,/不是全市/);});
test('loader uses only three fixed relative snapshot paths and no redirects',async()=>{const b=fixture();let i=0;const seen=[];await m.loadSnapshot(async(url,options)=>{seen.push([url.pathname,options]);return new Response(JSON.stringify(b[i++]),{headers:{'content-type':'application/json'}});});assert.equal(seen.length,3);assert.ok(seen.every(v=>v[0].includes('/data/')&&v[1].redirect==='error'&&v[1].cache==='no-store'));});
test('HTTP or HTML error cannot become synthetic fallback',async()=>{await assert.rejects(m.loadSnapshot(async()=>new Response('bad',{status:503})),/HTTP_FAILED/);await assert.rejects(m.loadSnapshot(async()=>new Response('bad',{headers:{'content-type':'text/html'}})),/CONTENT_TYPE/);});
test('oversized response rejected before download',async()=>await assert.rejects(m.loadSnapshot(async()=>new Response('{}',{headers:{'content-type':'application/json','content-length':String(5*1024*1024)}})),/TOO_LARGE/));
test('UI route is isolated with same-origin helpers and explicit boundaries',async()=>{const html=await readFile(new URL('../public/workspace-preview/index.html',import.meta.url),'utf8');assert.match(html,/lang="zh-Hant"/);assert.match(html,/connect-src 'self'/);for(const file of ['dialog-trigger-focus','workspace','skip-focus','base-path-links','snapshot-freshness'])assert.match(html,new RegExp(`\\./${file}\\.mjs`));assert.doesNotMatch(html,/https:\/\/(?:fonts|cdn)/);const base=await readFile(new URL('../public/workspace-preview/base-path-links.mjs',import.meta.url),'utf8');assert.match(base,/a\[href="\.\.\/\.\.\/"\]/);assert.match(base,/setAttribute\('href', '\.\.\/'\)/);const freshness=await readFile(new URL('../public/workspace-preview/snapshot-freshness.mjs',import.meta.url),'utf8');assert.match(freshness,/mode.*snapshot/);assert.match(freshness,/data-action="reload"/);});
