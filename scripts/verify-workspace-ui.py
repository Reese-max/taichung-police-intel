#!/usr/bin/env python3
"""Real browser acceptance for the isolated Figma UI preview, never production."""
from __future__ import annotations
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import hashlib
import json
import os
import threading
import unittest
from playwright.sync_api import sync_playwright, expect

ROOT=Path(__file__).resolve().parents[1]
OUTPUT=Path(os.environ.get('WORKSPACE_UI_OUTPUT','runtime-evidence/workspace-ui')).resolve()
PUBLIC=ROOT/'apps/web/public'
PREFIX='/taichung-police-intel'
KEY='govintel:synthetic-workspace:v1'
VIEWS=['overview','ask','event','handoff','sources','connections']

class Handler(SimpleHTTPRequestHandler):
    def do_GET(self):
        if not self.path.startswith(PREFIX+'/'):
            self.send_error(404); return
        self.path=self.path[len(PREFIX):]
        return super().do_GET()
    def log_message(self,*_): pass

class WorkspaceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        OUTPUT.mkdir(parents=True,exist_ok=True)
        cls.server=ThreadingHTTPServer(('127.0.0.1',0),partial(Handler,directory=str(PUBLIC)))
        threading.Thread(target=cls.server.serve_forever,daemon=True).start()
        cls.url=f'http://127.0.0.1:{cls.server.server_port}{PREFIX}/workspace-preview/index.html'
        cls.pw=sync_playwright().start()
        options={'headless':True}
        if os.environ.get('BROWSER_EXECUTABLE'): options['executable_path']=os.environ['BROWSER_EXECUTABLE']
        cls.browser=cls.pw.chromium.launch(**options)
    @classmethod
    def tearDownClass(cls):
        cls.browser.close(); cls.pw.stop(); cls.server.shutdown()
    def setUp(self):
        self.context=self.browser.new_context(viewport={'width':1440,'height':1050},locale='zh-TW',reduced_motion='reduce',accept_downloads=True)
        self.context.tracing.start(screenshots=True,snapshots=True,sources=True)
        self.page=self.context.new_page(); self.errors=[]
        self.page.on('pageerror',lambda error:self.errors.append(str(error)))
    def tearDown(self):
        try:
            self.page.screenshot(path=str(OUTPUT/(self._testMethodName+'.png')),full_page=True)
            self.context.tracing.stop(path=str(OUTPUT/(self._testMethodName+'.zip')))
        finally: self.context.close()
        self.assertEqual(self.errors,[],'browser JavaScript errors')
    def go(self,view='overview',mode='demo',event='demo-activity'):
        self.page.goto(self.url+f'#view={view}&mode={mode}&event={event}')
        expect(self.page.locator('#page-title')).to_be_visible()
    def test_01_responsive_six_pages(self):
        for width in (1440,768,390,320):
            self.page.set_viewport_size({'width':width,'height':1050 if width>850 else 900})
            for view in VIEWS:
                self.go(view)
                self.assertLessEqual(self.page.evaluate('document.documentElement.scrollWidth'),width+1,f'overflow {view} {width}')
                if width in (1440,390): self.page.screenshot(path=str(OUTPUT/f'{view}-{width}.png'),full_page=True)
    def test_02_search_and_injection_and_distinct_events(self):
        self.go('ask')
        self.page.locator('#query-input').fill('<img src=x onerror="alert(1)">')
        self.page.locator('#search-form').evaluate('(form)=>form.requestSubmit()')
        expect(self.page.get_by_role('heading',name='示例資料中沒有符合的結果')).to_be_visible()
        self.assertEqual(self.page.locator('img').count(),0)
        self.page.locator('[data-action="clear-query"]').first.click()
        self.page.locator('[data-action="category"][data-value="反詐"]').click()
        self.assertEqual(self.page.locator('.answer-result').count(),1)
        self.assertEqual(self.page.locator('.aside-stack .source-link').count(),0)
        self.page.locator('.answer-result').click()
        expect(self.page.locator('#page-title')).to_have_text('反詐公告新增假客服提醒')
        self.assertNotIn('17:00',self.page.locator('main').inner_text())
    def test_03_tabs_dialog_and_keyboard(self):
        self.go('event')
        self.page.locator('#tab-compare').focus(); self.page.keyboard.press('ArrowRight')
        expect(self.page.locator('#tab-evidence')).to_have_attribute('aria-selected','true')
        self.page.locator('[data-ref="demo-police:v2"]').click()
        expect(self.page.locator('dialog')).to_be_visible()
        expect(self.page.locator('dialog')).to_contain_text('16:00')
        self.page.keyboard.press('Escape'); expect(self.page.locator('dialog')).not_to_be_visible()
        self.page.locator('.skip-link').focus(); self.page.keyboard.press('Enter')
        expect(self.page.locator('#main-content')).to_be_focused()
        self.assertIn('view=event',self.page.url)
    def test_04_review_persistence_and_export(self):
        self.go('handoff'); expect(self.page.locator('[data-action="confirm-open"]')).to_be_disabled()
        self.go('event'); self.page.locator('#scenario').select_option('aligned')
        self.go('handoff'); self.page.locator('[data-action="confirm-open"]').click()
        expect(self.page.locator('#save-confirmation')).to_be_disabled()
        self.page.locator('dialog [data-ref="demo-police:v2"]').click()
        expect(self.page.locator('dialog')).to_contain_text('16:00')
        self.page.locator('dialog [data-action="confirm-open"]').click()
        expect(self.page.locator('#save-confirmation')).to_be_disabled()
        self.page.locator('#review-checkbox').check(); self.page.locator('#save-confirmation').click()
        expect(self.page.locator('.document')).to_contain_text('v2 已保存在此瀏覽器')
        self.page.reload(); expect(self.page.locator('.document')).to_contain_text('v2 已保存在此瀏覽器')
        with self.page.expect_download() as info: self.page.locator('[data-action="export"]').click()
        download=info.value; path=OUTPUT/download.suggested_filename; download.save_as(path)
        data=json.loads(path.read_text()); self.assertTrue(data['synthetic']); self.assertEqual(data['traffic_control_start'],'16:00')
        self.page.locator('[data-action="history"][data-version="1"]').click()
        expect(self.page.locator('dialog')).to_contain_text('17:00')
    def test_05_stale_and_storage_failure(self):
        self.go('event'); self.page.locator('#scenario').select_option('stale')
        self.go('handoff'); expect(self.page.locator('[data-action="confirm-open"]')).to_be_disabled()
        self.go('event'); self.page.locator('#scenario').select_option('aligned')
        self.go('handoff'); self.page.locator('[data-action="confirm-open"]').click()
        self.page.locator('#review-checkbox').check()
        self.page.evaluate("key=>localStorage.removeItem(key)",KEY)
        quota=self.page.evaluate("""() => {
          let counter=0;
          for (const size of [1024*1024,256*1024,64*1024,16*1024,4096,1024,256,64,16,4,1]) {
            for (let i=0;i<128;i+=1) {
              try { localStorage.setItem(`__quota_${size}_${counter++}`,'x'.repeat(size)); }
              catch (_) { break; }
            }
          }
          try { localStorage.setItem('__quota_final_probe','x'); return 'not-exhausted'; }
          catch (error) { return error?.name || 'quota-error'; }
        }""")
        self.assertNotEqual(quota,'not-exhausted','browser localStorage quota was not exhausted')
        self.page.locator('#save-confirmation').click()
        expect(self.page.locator('main')).to_contain_text('此操作尚未保存')
        self.assertNotIn('v2 已保存在此瀏覽器',self.page.locator('.document').inner_text())
    def test_06_cross_tab_invalidates_review(self):
        self.go('event'); self.page.locator('#scenario').select_option('aligned')
        self.go('handoff'); self.page.locator('[data-action="confirm-open"]').click(); self.page.locator('#review-checkbox').check()
        other=self.context.new_page(); other.goto(self.url)
        other.evaluate("key=>{const s=JSON.parse(localStorage.getItem(key));s.scenario='conflict';localStorage.setItem(key,JSON.stringify(s));}",KEY)
        expect(self.page.locator('dialog')).not_to_be_visible()
        expect(self.page.locator('[data-action="confirm-open"]')).to_be_disabled()
        other.close()
    def test_07_snapshot_from_actual_saved_files_and_unsupported(self):
        self.go(mode='snapshot')
        expect(self.page.get_by_role('heading',name='保存文件列表')).to_be_visible()
        self.assertLessEqual(self.page.locator('.snapshot-table tbody tr').count(),20)
        expect(self.page.locator('main')).to_contain_text('不是今日新增')
        expect(self.page.locator('main')).to_contain_text('未執行')
        self.go('ask','snapshot')
        expect(self.page.get_by_role('heading',name='此能力尚未接入保存快照')).to_be_visible()
        self.assertEqual(self.page.locator('.answer-result').count(),0)
    def test_08_snapshot_error_not_fake_empty_or_demo(self):
        self.page.route('**/data/*.json',lambda route:route.fulfill(status=503,body='offline'))
        self.go(mode='snapshot')
        expect(self.page.get_by_role('heading',name='目前無法核對保存快照')).to_be_visible()
        expect(self.page.locator('main')).to_contain_text('沒有以合成資料代替')
        self.assertEqual(self.page.locator('.event-card').count(),0)
    def test_09_snapshot_generation_mismatch(self):
        def respond(route):
            name=route.request.url.rsplit('/',1)[-1]
            data=json.loads((PUBLIC/'data'/name).read_text())
            if name=='v2-daily-brief.json': data['source_collection_run_id']='deliberately-wrong'
            route.fulfill(status=200,content_type='application/json',body=json.dumps(data))
        self.page.route('**/data/*.json',respond); self.go(mode='snapshot')
        expect(self.page.locator('main')).to_contain_text('GENERATION_MISMATCH')
    def test_10_mobile_more_and_no_fake_mcp(self):
        self.page.set_viewport_size({'width':390,'height':900}); self.go()
        self.page.locator('[data-action="more"]').click()
        self.page.locator('dialog a').filter(has_text='AI / MCP').click()
        expect(self.page.locator('#page-title')).to_have_text('讓資料進入你的 AI 工作流程。')
        expect(self.page.get_by_role('button',name='連接端點尚未提供')).to_be_disabled()
        self.assertEqual(self.page.locator('input').count(),0)
    def test_11_dialog_focus_returns_to_actual_trigger(self):
        self.go('sources')
        triggers=self.page.locator('[data-action="source-detail"]')
        self.assertGreaterEqual(triggers.count(),2)
        first=triggers.nth(0); second=triggers.nth(1)
        first.click(); expect(self.page.locator('dialog')).to_be_visible()
        first_id=first.get_attribute('id'); self.assertTrue(first_id and first_id.startswith('workspace-action-'))
        self.page.keyboard.press('Escape'); expect(first).to_be_focused()
        second.click(); expect(self.page.locator('dialog')).to_be_visible()
        second_id=second.get_attribute('id'); self.assertTrue(second_id and second_id.startswith('workspace-action-'))
        self.assertNotEqual(first_id,second_id)
        self.page.keyboard.press('Escape'); expect(second).to_be_focused()
        self.assertEqual(self.page.locator('#last-dialog-trigger').count(),0)
    def test_12_stale_partial_snapshot_keeps_incompleteness_visible(self):
        old='2026-01-01T00:00:00+08:00'
        def respond(route):
            name=route.request.url.rsplit('/',1)[-1]
            data=json.loads((PUBLIC/'data'/name).read_text())
            if name=='intelligence-feed.json': data['generated_at']=old
            elif name=='source-status.json':
                data['generated_at']=old; data['latest_collection_run']['status']='SUCCEEDED'
                for source in data['sources']:
                    source['source_health']='PASS'; source['window_completeness']='COMPLETE_WITH_ITEMS'; source['last_checked_at']=old
                data['sources'][0]['window_completeness']='PARTIAL'
            else:
                data['generated_at']=old; data['source_status_generated_at']=old; data['snapshot_complete']=True; data['publication_status']='READY'
            route.fulfill(status=200,content_type='application/json',body=json.dumps(data))
        self.page.route('**/data/*.json',respond)
        self.go(mode='snapshot')
        expect(self.page.locator('main')).to_contain_text('快照不完整')
        self.assertNotIn('快照核對時間在本地期限內',self.page.locator('main').inner_text())

if __name__=='__main__':
    suite=unittest.defaultTestLoader.loadTestsFromTestCase(WorkspaceTests)
    result=unittest.TextTestRunner(verbosity=2).run(suite)
    receipt={'schema_version':1,'scope':'UI_BROWSER_RUNTIME_NOT_PRODUCTION','code_sha':os.environ.get('GITHUB_SHA'),
      'tests_run':result.testsRun,'failures':len(result.failures),'errors':len(result.errors),'success':result.wasSuccessful(),
      'production_verified':False,'live_official_fetch':False,'viewports':[1440,768,390,320],
      'source_files_sha256':{str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted((PUBLIC/'workspace-preview').glob('*')) if p.is_file()}}
    OUTPUT.mkdir(parents=True,exist_ok=True); (OUTPUT/'receipt.json').write_text(json.dumps(receipt,ensure_ascii=False,indent=2)+'\n')
    raise SystemExit(0 if result.wasSuccessful() else 1)
