"""Offline evidence for opt-in list traversal; no live sources or rights approval."""
import importlib.util
from pathlib import Path
from datetime import date
import unittest

import online_collect as oc

spec = importlib.util.spec_from_file_location("budget_fixtures", Path(__file__).with_name("test_news_list_collector.py"))
f = importlib.util.module_from_spec(spec)
spec.loader.exec_module(f)


class NewsListBudgetTests(unittest.TestCase):
    def pages(self, source_id="S-032", length=6, loop=False, repeated=False):
        base = oc.NEWS_LIST_SOURCES[source_id]["list_url"]
        separator = '&' if '?' in base else '?'
        urls = [base] + [base + separator + f'page={i}' for i in range(2, length + 1)]
        pages = {}
        for n, url in enumerate(urls, 1):
            day = date(2026, 9, 12 - n).isoformat()
            key = str(22000 - n)
            href = f'index-1.asp?Parser=9,4,20,,,,{key}' if source_id == "S-032" else f'/12047/12142/12186/{key}/post'
            body = f'<li><a href="{href}">News {day}</a></li>'
            if n < length:
                body += f'<a rel="next" href="{urls[n]}">Next</a>'
            elif loop:
                body += f'<a rel="next" href="{urls[0]}">Next</a>'
            else:
                body += f'<a href="{url}">Last page</a>'
            pages[url] = f._page(body)
        if repeated:
            pages[urls[-1]] = pages[urls[-2]].replace(urls[-1].encode(), b'javascript:loadMore()')
        return urls, pages

    def collect(self, source_id="S-032", **kwargs):
        urls, pages = self.pages(source_id)
        session = f.FakeSession(pages)
        result = oc.collect_source(session, source_id, date(2026, 9, 8), date(2026, 9, 11), max_details=0, **kwargs)
        return result, session, urls

    def test_navigation_header_footer_and_aside_are_not_list_rows(self):
        html = f._page('<header><nav><li><a href="/447345/post">Undated menu</a></li></nav></header>'
            '<main id="center" class="center"><section class="list"><ul>'
            '<li><a href="/3377338/post">Meeting 2026-09-10</a></li>'
            '<li><a href="/3377300/post">Meeting 2026-09-01</a></li>'
            '</ul></section></main>'
            '<aside><a href="/999999/post">Related 2026-10-07</a></aside>'
            '<footer><a href="/443956/post">Undated contact</a></footer>')
        entries=oc.parse_news_list(html,oc.NEWS_LIST_SOURCES['S-019']['list_url'],oc.NEWS_LIST_SOURCES['S-019']['id_pattern'])
        self.assertEqual([r['stable_key'] for r in entries], ['3377338','3377300'])

    def test_nav_same_id_cannot_shadow_dated_content_row(self):
        html=f._page('<header><nav class="menu"><li><a href="/3377338/post">Menu</a></li></nav></header>'
            '<main id="center"><section class="list"><ul><li><a href="/3377338/post">Meeting 2026-09-10</a></li></ul></section></main>')
        entries=oc.parse_news_list(html,oc.NEWS_LIST_SOURCES['S-019']['list_url'],oc.NEWS_LIST_SOURCES['S-019']['id_pattern'])
        self.assertEqual(len(entries),1)
        self.assertEqual(entries[0]['published'], date(2026,9,10))
        self.assertEqual(entries[0]['title'],'Meeting 2026-09-10')

    def test_true_content_missing_date_still_retained_and_partial(self):
        html=f._page('<main id="center"><section class="list"><ul>'
            '<li><a href="/3377338/post">Meeting 2026-09-10</a></li>'
            '<li><a href="/3377300/post">Unknown date</a></li>'
            '<li><a href="/3377000/post">Meeting 2026-09-01</a></li>'
            '</ul></section></main>')
        result,_=f._collect('S-019',html,max_details=0)
        self.assertEqual(result['snapshot_item_count'],3)
        self.assertEqual(result['window_completeness'],'PARTIAL')

    def test_explicit_six_page_budget_reaches_terminal_for_both_sources(self):
        for sid in ("S-019", "S-032"):
            with self.subTest(source_id=sid):
                result, session, urls = self.collect(sid, max_list_pages=6)
                self.assertEqual(result['window_completeness'], 'COMPLETE_WITH_ITEMS')
                self.assertEqual(result['window_item_count'], 4)
                self.assertEqual(session.fetched, urls)
                self.assertEqual(result['list_traversal']['stop_reason'], 'TERMINAL_PAGE')
                self.assertEqual(result['list_traversal']['whole_history_completeness'], 'UNKNOWN')
                self.assertFalse(result['list_traversal']['detail_pages_complete'])
                self.assertFalse(result['list_traversal']['attachments_downloaded'])

    def test_default_four_pages_remains_partial_even_with_old_prefix(self):
        result, session, urls = self.collect()
        self.assertEqual(result['window_completeness'], 'PARTIAL')
        self.assertEqual(session.fetched, urls[:4])
        self.assertEqual(result['list_traversal']['stop_reason'], 'PAGE_LIMIT')

    def test_invalid_budget_rejected_before_network(self):
        for limit in (0, -1, True, 1.2, '6', 41):
            session = f.FakeSession({})
            with self.subTest(limit=limit), self.assertRaises(ValueError):
                oc.collect_news_list(session, 'S-032', date(2026,9,8), date(2026,9,11), max_list_pages=limit)
            self.assertEqual(session.fetched, [])

    def test_loop_remains_partial_under_larger_budget(self):
        urls, pages = self.pages(loop=True)
        session=f.FakeSession(pages)
        result=oc.collect_news_list(session,'S-032',date(2026,9,8),date(2026,9,11),max_details=0,max_list_pages=10)
        self.assertEqual(result['window_completeness'],'PARTIAL')
        self.assertEqual(session.fetched,urls)
        self.assertEqual(result['list_traversal']['stop_reason'],'PAGE_LOOP')

    def test_old_page_does_not_stop_before_later_newer_page(self):
        urls, pages = self.pages()
        pages[urls[2]]=f._page(f'<li><a href="index-1.asp?Parser=9,4,20,,,,21997">Old 2026-07-01</a></li><a rel="next" href="{urls[3]}">Next</a>')
        session=f.FakeSession(pages)
        result=oc.collect_news_list(session,'S-032',date(2026,9,8),date(2026,9,11),max_details=0,max_list_pages=6)
        self.assertEqual(session.fetched,urls)
        self.assertEqual(result['window_completeness'],'PARTIAL')
        self.assertFalse(result['list_traversal']['reverse_chronological_observed'])


if __name__ == '__main__':
    unittest.main()
