"""Offline reproduction of the official S019 last-page controls observed Oct 7."""
import unittest
import online_collect as oc

BASE = 'https://www.rdec.taichung.gov.tw/12047/12142/12186?Page=25&PageSize=30&type='


def html(controls):
    return ('<main id="center"><section class="list"><ul><li><a href="/873338/post">Meeting 2020-09-10</a></li></ul></section><nav class="pagination">'+controls+'</nav></main>').encode()


class TerminalPageProofTests(unittest.TestCase):
    def parse(self, controls, base=BASE):
        return oc.next_news_list_page(html(controls), base, 'S-019')

    def test_matching_explicit_last_page_with_previous_numbers_is_terminal(self):
        controls = '<a href="?Page=1&PageSize=30&type=">第一頁</a><a href="?Page=24&PageSize=30&type=">24</a><a href="?Page=25&PageSize=30&type=">最後一頁</a>'
        self.assertEqual(self.parse(controls),(None,False))

    def test_matching_last_page_alone_is_terminal(self):
        self.assertEqual(self.parse('<a title="最後一頁" href="?Page=25&PageSize=30&type=">End</a>'),(None,False))

    def test_explicit_page_without_any_controls_has_no_terminal_proof(self):
        self.assertEqual(self.parse(''), (None, True))

    def test_numbered_links_without_authoritative_last_stay_partial(self):
        self.assertEqual(self.parse('<a href="?Page=24&PageSize=30&type=">24</a>'),(None,True))

    def test_last_ahead_or_behind_current_is_not_terminal(self):
        for page in (24,26):
            with self.subTest(page=page):
                self.assertEqual(self.parse(f'<a href="?Page={page}&PageSize=30&type=">最後一頁</a>'),(None,True))

    def test_current_page_must_be_explicit_and_unambiguous(self):
        control='<a href="?Page=25&PageSize=30&type=">最後一頁</a>'
        for base in (BASE.split('?')[0],BASE+'&page=25',BASE.replace('Page=25','Page=unknown')):
            with self.subTest(base=base):
                self.assertEqual(self.parse(control,base),(None,True))

    def test_last_must_have_same_origin_path_and_nonpage_query(self):
        for href in ('https://evil.example.test/12047/12142/12186?Page=25&PageSize=30&type=',
                     '/12047/12142/12145?Page=25&PageSize=30&type=',
                     '?Page=25&PageSize=50&type=', '?Page=25&PageSize=30&type=other',
                     'javascript:loadMore()', '?Page=25&Page=25&PageSize=30&type=', '?Page=25&PageSize=30&PageSize=30&type=',
                     '?Page=25&pagesize=30&type=', '?page=25&PageSize=30&type='):
            with self.subTest(href=href):
                self.assertEqual(self.parse(f'<a href="{href}">最後一頁</a>'),(None,True))

    def test_conflicting_numbered_control_or_last_link_prevents_proof(self):
        last='<a href="?Page=25&PageSize=30&type=">最後一頁</a>'
        for conflict in ('<a href="?Page=26&PageSize=30&type=">26</a>',
                         '<a href="?Page=26&PageSize=30&type=">最後一頁</a>',
                         '<a href="https://evil.example.test/?Page=24">24</a>',
                         '<a href="?Page=24&PageSize=30&type=">26</a>'):
            with self.subTest(conflict=conflict):
                self.assertEqual(self.parse(last+conflict),(None,True))

    def test_browser_history_utility_outside_pager_does_not_poison_terminal(self):
        controls='<section class="friendly"><section class="function"><ul><li><a href="javascript:history.back();">回上一頁</a></li></ul></section></section>'
        controls += '<section class="page"><a href="?Page=24&PageSize=30&type=">上一頁</a><a href="?Page=25&PageSize=30&type=">最後一頁</a></section>'
        # The helper wraps controls in a pagination nav. The official utility
        # and pager are siblings, so reproduce that actual ancestry here.
        body=('<main id="center">'+controls+'</main>').encode()
        self.assertEqual(oc.next_news_list_page(body,BASE,'S-019'),(None,False))

    def test_browser_back_in_real_pager_cannot_be_ignored(self):
        for utility in ('<section class="page"><section class="function"><a href="javascript:history.back();">回上一頁</a></section></section>',
                        '<section class="function"><a href="javascript:loadMore()">回上一頁</a></section>',
                        '<section class="function"><a href="javascript:history.back();">上一頁</a></section>'):
            with self.subTest(utility=utility):
                self.assertEqual(self.parse(utility+'<a href="?Page=25&PageSize=30&type=">最後一頁</a>'),(None,True))

    def test_active_next_prevents_terminal_claim(self):
        controls='<a href="?Page=25&PageSize=30&type=">最後一頁</a><a rel="next" href="?Page=26&PageSize=30&type=">下一頁</a>'
        url,has_next=self.parse(controls)
        self.assertTrue(has_next)
        self.assertIsNotNone(url)


if __name__=='__main__':unittest.main()
