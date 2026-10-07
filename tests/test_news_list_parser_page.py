"""Offline regressions for S032's observed comma Parser pagination."""
import unittest
import online_collect as oc

ROOT = oc.NEWS_LIST_SOURCES["S-032"]["list_url"]


def page(number):
    return ROOT + ",,,,,,,," + str(number)


def controls(*links):
    return ("<nav class='pagination'>" + "".join(
        f'<a href="{url}">{label}</a>' for label, url in links
    ) + "</nav>").encode()


class ParserPageTests(unittest.TestCase):
    def parse(self, *links, current=82):
        return oc.next_news_list_page(controls(*links), page(current), "S-032")

    def test_proven_last_allows_observed_clamped_next(self):
        self.assertEqual(self.parse(("第一頁", ROOT), ("上一頁", page(81)),
            ("最末頁", page(82)), ("下一頁", page(82))), (None, False))

    def test_terminal_proof_does_not_depend_on_control_order(self):
        self.assertEqual(self.parse(("下一頁", page(82)), ("最末頁", page(82))), (None, False))

    def test_self_next_without_official_last_is_still_partial(self):
        self.assertEqual(self.parse(("下一頁", page(82))), (None, True))

    def test_regular_next_remains_traversable(self):
        self.assertEqual(self.parse(("下一頁", page(82)), ("最末頁", page(82)), current=81), (page(82), True))

    def test_conflicting_next_or_last_blocks_terminal(self):
        for conflict in (("下一頁", page(83)), ("最末頁", page(83)),
                         ("83", page(83)), ("81", page(80)), ("第一頁", page(2))):
            with self.subTest(conflict=conflict):
                self.assertEqual(self.parse(("下一頁", page(82)), ("最末頁", page(82)), conflict), (None, True))

    def test_host_path_query_and_parser_shape_are_bound(self):
        for target in (page(82).replace("www.traffic.taichung.gov.tw", "evil.example.test"),
                       page(82).replace("index.asp", "index-1.asp"), page(82)+"&category=other",
                       page(82)+"&parser=9,4,20,,,,,,,,82", page(82).replace("9,4,20", "9,4,21"),
                       page(82).replace(",,,,,,,,82", ",,,,,,,82"), page(82)+"#frag",
                       page(82).replace("https:", "http:"), page(82).replace("82", "082")):
            with self.subTest(target=target):
                self.assertEqual(self.parse(("下一頁", page(82)), ("最末頁", target)), (None, True))

    def test_nonterminal_or_ambiguous_identity_has_no_terminal_proof(self):
        for current in (ROOT, page(82)+"&Parser=9,4,20", page(82).replace("82", "unknown")):
            with self.subTest(current=current):
                self.assertNotEqual(oc.next_news_list_page(controls(("最末頁", page(82)), ("下一頁", page(82))), current, "S-032"), (None, False))

    def test_unmarked_next_is_only_accepted_for_s032_parser_shape(self):
        base="https://www.rdec.taichung.gov.tw/12047/12142/12186?Page=82"
        self.assertNotEqual(oc.next_news_list_page(controls(("最末頁", base), ("下一頁", base)), base, "S-019"), (None, False))

    def test_entry_and_explicit_comma_first_page_need_terminal_evidence(self):
        for current in (ROOT, page(1)):
            with self.subTest(current=current):
                self.assertEqual(oc.next_news_list_page(b"<html></html>", current, "S-032"), (None, True))

    def test_unlabelled_rel_first_is_checked_and_case_insensitive(self):
        body=controls(("最末頁", page(82)), ("下一頁", page(82)))
        body+=f'<link rel="FIRST" href="{page(2)}">'.encode()
        self.assertEqual(oc.next_news_list_page(body,page(82),"S-032"),(None,True))

    def test_previous_must_be_adjacent_and_distinct_conflicts_stay_partial(self):
        for links in ((("上一頁",page(1)),), (("上一頁",page(1)),("上一頁",page(2)))):
            with self.subTest(links=links):
                self.assertEqual(self.parse(*links,("下一頁",page(3)),("最末頁",page(3)),current=3),(None,True))

    def test_first_page_clamped_previous_is_legitimate(self):
        self.assertEqual(self.parse(("上一頁",ROOT),("下一頁",page(2)),("最末頁",page(2)),current=1),(page(2),True))
        self.assertEqual(self.parse(("上一頁",ROOT),("下一頁",ROOT),("最末頁",ROOT),current=1),(None,False))

    def test_identical_previous_controls_are_allowed_at_terminal(self):
        self.assertEqual(self.parse(("上一頁",page(81)),("上一頁",page(81)),("下一頁",page(82)),("最末頁",page(82))),(None,False))


if __name__ == "__main__":
    unittest.main()
