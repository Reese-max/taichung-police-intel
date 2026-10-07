"""Local continuation safety: no fabricated chain can authorize global coverage."""
from copy import deepcopy
from datetime import date
import json
import importlib.util
import io
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
from contextlib import redirect_stdout
from unittest import mock
import unittest

from intel_v2 import news_list_checkpoint as cp
from tests.test_news_list_collector import FakeSession
from tests.test_news_list_parser_page import ROOT, page

START, END = date(2026, 9, 1), date(2026, 10, 7)
CLI_PATH = Path(__file__).resolve().parents[1] / "scripts/news-list-resume.py"
cli_spec = importlib.util.spec_from_file_location("news_list_resume_cli", CLI_PATH)
cli = importlib.util.module_from_spec(cli_spec)
cli_spec.loader.exec_module(cli)


def fixtures(length=6):
    urls = [ROOT] + [page(number) for number in range(2, length + 1)]
    bodies = {}
    for number, url in enumerate(urls, 1):
        following = urls[number] if number < length else url
        day = f"2026-09-{20-number:02d}"
        bodies[url] = (f'<meta charset="utf-8"><li><a href="index-1.asp?Parser=9,4,20,,,,{1000-number}">Fixture {day}</a></li>'
                       f'<a href="{ROOT}">第一頁</a><a href="{following}">下一頁</a>'
                       f'<a href="{urls[-1]}">最末頁</a>').encode()
    return urls, bodies


def rebuild(value):
    previous = None
    for batch in value["batches"]:
        batch["previous_batch_sha256"] = previous
        batch["batch_sha256"] = cp.digest({key: item for key, item in batch.items() if key != "batch_sha256"})
        previous = batch["batch_sha256"]
    return cp.seal(value)


class CheckpointTests(unittest.TestCase):
    def setUp(self):
        self.urls, self.pages = fixtures()
        self.session = FakeSession(self.pages)

    def batch(self, **kwargs):
        return cp.collect_batch(self.session, START, END, minimum_interval=0.1, **kwargs)

    def test_default_four_pages_and_explicit_resume_bind_full_prefix(self):
        first, state = self.batch()
        self.assertEqual(self.session.fetched, self.urls[:4])
        self.assertEqual(state["next_resume_url"], self.urls[4])
        self.assertFalse(state["prefix_to_declared_terminal_consistent"])
        second, state = self.batch(checkpoint=first, expected_sha256=first["checkpoint_sha256"])
        self.assertEqual(self.session.fetched, self.urls)
        self.assertEqual(state["pages"], 6)
        self.assertTrue(state["prefix_to_declared_terminal_consistent"])
        self.assertIsNone(state["next_resume_url"])
        self.assertEqual(state["validation_scope"], "LOCAL_TRAVERSAL_RECEIPT_ONLY")
        self.assertEqual(state["window_completeness"], "PARTIAL")
        self.assertEqual(state["whole_history_completeness"], "UNKNOWN")
        self.assertFalse(state["coverage_independently_verified"])
        self.assertFalse(state["promotion_eligible"])
        self.assertFalse(state["rights_approved"])
        with self.assertRaises(ValueError):
            self.batch(checkpoint=second)

    def test_page_bound_rejected_before_network(self):
        for budget in (0, 41, True, "4", 4.2):
            with self.subTest(budget=budget), self.assertRaises(ValueError):
                self.batch(max_list_pages=budget)
        self.assertEqual(self.session.fetched, [])

    def test_window_or_source_binding_mismatch_rejected_before_network(self):
        value = cp.new_checkpoint(START, END)
        for field, content in (("source_id", "S-019"), ("requested_start", "2026-08-01"), ("parser_contract", "other"),
                               ("schema_version", True), ("parser_code_sha256", "a"*64)):
            bad = deepcopy(value)
            bad["binding"][field] = content
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.batch(checkpoint=cp.seal(bad))
        self.assertEqual(self.session.fetched, [])

    def test_middle_batch_even_with_recomputed_hashes_cannot_resume(self):
        first, _ = self.batch(max_list_pages=6)
        bad = deepcopy(first)
        bad["batches"][0]["pages"] = bad["batches"][0]["pages"][3:]
        with self.assertRaisesRegex(ValueError, "cursor gap"):
            cp.validate_checkpoint(rebuild(bad), START, END)

    def test_gap_and_duplicate_page_rejected_even_with_recomputed_hashes(self):
        first, _ = self.batch(max_list_pages=6)
        for mode in ("gap", "duplicate"):
            bad = deepcopy(first)
            pages = bad["batches"][0]["pages"]
            if mode == "gap":
                del pages[2]
            else:
                pages.insert(2, deepcopy(pages[1]))
            with self.subTest(mode=mode), self.assertRaisesRegex(ValueError, "cursor gap"):
                cp.validate_checkpoint(rebuild(bad), START, END)

    def test_changed_expected_hash_rejected_before_network(self):
        value = cp.new_checkpoint(START, END)
        with self.assertRaisesRegex(ValueError, "hash mismatch"):
            self.batch(checkpoint=value, expected_sha256="a"*64)
        self.assertEqual(self.session.fetched, [])

    def test_changed_official_last_across_batches_blocks_receipt(self):
        first, _ = self.batch()
        self.pages[self.urls[4]] = self.pages[self.urls[4]].replace(
            f'<a href="{self.urls[-1]}">最末頁</a>'.encode(), f'<a href="{page(7)}">最末頁</a>'.encode())
        with self.assertRaisesRegex(ValueError, "Last changed"):
            self.batch(checkpoint=first)

    def test_case_insensitive_rel_last_cannot_hide_changed_declarations(self):
        for index,url in enumerate(self.urls):
            old=f'<a href="{self.urls[-1]}">最末頁</a>'.encode()
            target=page(7) if index==0 else self.urls[-1]
            rel="LAST" if index==0 else "last"
            self.pages[url]=self.pages[url].replace(old,f'<a href="{target}" rel="{rel}"></a>'.encode())
        with self.assertRaisesRegex(ValueError, "Last changed"):
            self.batch(max_list_pages=6)

    def test_unknown_dates_and_nonmonotonic_order_never_become_global_complete(self):
        for replacement in (b"Unknown date", b"Fixture 2026-10-07"):
            _, self.pages = fixtures()
            self.pages[self.urls[2]] = self.pages[self.urls[2]].replace(b"Fixture 2026-09-17", replacement)
            self.session = FakeSession(self.pages)
            _, state = self.batch(max_list_pages=6)
            self.assertEqual(state["window_completeness"], "PARTIAL")
            self.assertFalse(state["coverage_independently_verified"])
            self.assertFalse(state["all_observed_dates_known"] if replacement == b"Unknown date" else state["reverse_chronological_observed"])

    def test_full_recomputed_fictional_ledger_still_cannot_claim_global_coverage(self):
        first, _ = self.batch(max_list_pages=6)
        for record in first["batches"][0]["pages"]:
            record["body_sha256"] = "a"*64
        state = cp.validate_checkpoint(rebuild(first), START, END)
        self.assertTrue(state["prefix_to_declared_terminal_consistent"])
        self.assertEqual(state["window_completeness"], "PARTIAL")
        self.assertFalse(state["coverage_independently_verified"])

    def test_changed_navigation_cannot_be_hidden_by_recomputed_hashes(self):
        first, _ = self.batch(max_list_pages=6)
        bad = deepcopy(first)
        bad["batches"][0]["pages"][-1]["has_next"] = True
        with self.assertRaisesRegex(ValueError, "navigation claim"):
            cp.validate_checkpoint(rebuild(bad), START, END)

    def test_stable_id_change_during_traversal_is_rejected(self):
        first, _ = self.batch(max_list_pages=6)
        bad = deepcopy(first)
        bad["batches"][0]["pages"][1]["rows"][0]["stable_key"] = bad["batches"][0]["pages"][0]["rows"][0]["stable_key"]
        with self.assertRaisesRegex(ValueError, "row changed"):
            cp.validate_checkpoint(rebuild(bad), START, END)

    def test_82_page_control_ledger_round_trips_within_storage_bound(self):
        first, _ = self.batch(max_list_pages=1)
        original = first["batches"][0]["pages"][0]
        pages = []
        for number in range(1, 83):
            record = deepcopy(original)
            record["requested_url"] = record["response_url"] = ROOT if number == 1 else page(number)
            record["rows"][0]["stable_key"] = str(10000-number)
            controls = cp.pager_controls((
                f'<a href="{page(1)}">第一頁</a><a href="{page(max(1,number-1))}">上一頁</a>'
                f'<a href="{page(min(82,number+1))}">下一頁</a><a href="{page(82)}">最末頁</a>'
                + ''.join(f'<a href="{page(target)}" title="第{target}頁" class="link_brown">第 {target} 頁</a>' for target in range(1,83))
            ).encode(),record["requested_url"])
            record["controls"] = controls
            record["next_url"] = page(number+1) if number < 82 else None
            record["has_next"] = number < 82
            pages.append(record)
        value = cp.new_checkpoint(START, END)
        value["batches"] = [{"batch": index+1, "previous_batch_sha256": None, "pages": pages[offset:offset+32], "batch_sha256": ""}
            for index, offset in enumerate(range(0,82,32))]
        value = rebuild(value)
        # This is explicitly fictional metadata. It proves the reader can read
        # the exact representation the writer emits at the real pager width.
        encoded = cp.encode_checkpoint(value)
        self.assertLessEqual(len(encoded), cp.MAX_CHECKPOINT_BYTES)
        state = cp.validate_checkpoint(json.loads(encoded), START, END)
        self.assertTrue(state["prefix_to_declared_terminal_consistent"])
        self.assertEqual(state["pages"],82)
        self.assertEqual(state["window_completeness"],"PARTIAL")


class CheckpointIOTests(unittest.TestCase):
    def command(self, source, output):
        return [sys.executable, str(CLI_PATH), "--start", START.isoformat(), "--end", END.isoformat(),
                "--resume-checkpoint", str(source), "--expected-checkpoint-sha256", "a"*64,
                "--output-checkpoint", str(output)]

    @unittest.skipUnless(hasattr(os, "mkfifo"), "POSIX FIFO regression")
    def test_cli_fifo_input_rejected_without_blocking_open(self):
        with tempfile.TemporaryDirectory() as directory:
            fifo, output = Path(directory)/"input-fifo", Path(directory)/"output.json"
            os.mkfifo(fifo)
            result = subprocess.run(self.command(fifo,output), capture_output=True, timeout=2)
            self.assertNotEqual(result.returncode,0)
            self.assertIn(b"regular file",result.stderr)
            self.assertFalse(output.exists())

    def test_cli_same_resolved_path_rejected_before_loading_or_network(self):
        with tempfile.TemporaryDirectory() as directory:
            source, alias = Path(directory)/"input.json", Path(directory)/"alias.json"
            original = b"FICTIONAL_NON_JSON_ORIGINAL"
            source.write_bytes(original)
            alias.symlink_to(source)
            for target in (source,alias):
                with self.subTest(target=target):
                    result = subprocess.run(self.command(source,target), capture_output=True, timeout=2)
                    self.assertEqual(result.returncode,2)
                    self.assertIn(b"must differ",result.stderr)
                    self.assertEqual(source.read_bytes(),original)

    def test_reader_checks_regular_file_and_byte_bound(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory)/"too-large.json"
            source.write_bytes(b"x"*(cp.MAX_CHECKPOINT_BYTES+1))
            with self.assertRaisesRegex(ValueError,"2 MiB"):
                cli.read_regular_bounded(source)
            with self.assertRaisesRegex(ValueError,"regular file"):
                cli.read_regular_bounded(Path(directory))

    def test_atomic_private_writer_does_not_touch_colliding_temp_hardlink(self):
        with tempfile.TemporaryDirectory() as directory:
            source, output = Path(directory)/"original.json", Path(directory)/"output.json"
            original = b"FICTIONAL_ORIGINAL_METADATA"
            source.write_bytes(original)
            # Both the previous fixed temporary name and the destination share
            # the original inode. Writing either in place would corrupt input.
            collision = output.with_suffix(output.suffix+".tmp")
            os.link(source,collision)
            os.link(source,output)
            _, bodies = fixtures()
            checkpoint, prior_state = cp.collect_batch(FakeSession(bodies), START, END,
                max_list_pages=1, minimum_interval=0.1)
            cli.write_checkpoint_atomic(output,checkpoint)
            self.assertEqual(source.read_bytes(),original)
            self.assertEqual(collision.read_bytes(),original)
            self.assertEqual(output.stat().st_mode & 0o777,0o600)
            self.assertNotEqual(output.stat().st_ino,source.stat().st_ino)
            loaded = json.loads(cli.read_regular_bounded(output))
            self.assertEqual(loaded,checkpoint)
            self.assertEqual(loaded["batches"][0]["batch"],1)
            self.assertEqual(loaded["batches"][0]["pages"][0]["rows"][0]["stable_key"],"999")
            self.assertEqual(cp.validate_checkpoint(loaded,START,END),prior_state)
            self.assertEqual(sorted(path.name for path in Path(directory).iterdir()), ["original.json","output.json","output.json.tmp"])

    def test_failed_atomic_replace_keeps_destination_and_removes_private_temp(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)/"output.json"
            output.write_bytes(b"FICTIONAL_PRIOR_CHECKPOINT")
            with mock.patch.object(cli.os,"replace",side_effect=OSError("fictional rename failure")):
                with self.assertRaisesRegex(OSError,"rename failure"):
                    cli.write_checkpoint_atomic(output,cp.new_checkpoint(START,END))
            self.assertEqual(output.read_bytes(),b"FICTIONAL_PRIOR_CHECKPOINT")
            self.assertEqual([path.name for path in Path(directory).iterdir()],["output.json"])


class CheckpointV2Tests(unittest.TestCase):
    def test_changed_row_date_parser_rejects_resume_before_another_fetch(self):
        _, bodies = fixtures()
        session = FakeSession(bodies)
        checkpoint, _ = cp.collect_batch(session,START,END,max_list_pages=1)
        session.fetched.clear()
        with mock.patch.object(cp.oc,"roc_date",return_value=date(2000,1,1)):
            with self.assertRaisesRegex(ValueError,"binding|code"):
                cp.collect_batch(session,START,END,checkpoint=checkpoint,max_list_pages=1)
        self.assertEqual(session.fetched,[])

    def test_legacy_v1_checkpoint_requires_restart_even_after_resealing(self):
        checkpoint = cp.new_checkpoint(START,END)
        checkpoint["binding"]["schema_version"] = 1
        checkpoint["binding"]["parser_contract"] = "S032_COMMA_PARSER_V1"
        with self.assertRaisesRegex(ValueError,"restart"):
            cp.validate_checkpoint(cp.seal(checkpoint),START,END)

    def test_unresolved_pagination_is_rejected_before_sealing(self):
        body=(f'<meta charset="utf-8"><li><a href="index-1.asp?Parser=9,4,20,,,,999">Fixture 2026-09-19</a></li>'
              f'<a href="{ROOT}">下一頁</a>').encode()
        with self.assertRaisesRegex(ValueError,"unresolved"):
            cp.collect_batch(FakeSession({ROOT:body}),START,END,max_list_pages=1)

    def test_resealed_unresolved_checkpoint_is_rejected_by_validator(self):
        _, bodies = fixtures()
        checkpoint, _ = cp.collect_batch(FakeSession(bodies),START,END,max_list_pages=1)
        record=checkpoint["batches"][0]["pages"][0]
        record["controls"]=[]
        record["next_url"]=None
        record["has_next"]=True
        with self.assertRaisesRegex(ValueError,"unresolved"):
            cp.validate_checkpoint(rebuild(checkpoint),START,END)

    def test_article_next_story_does_not_retain_raw_markers_or_poison_pager(self):
        _, bodies=fixtures()
        bodies[ROOT]+=b'<a class="next-story FICTIONAL_PRIVATE_CLASS" href="index-1.asp?Parser=9,4,20,,,,765&note=FICTIONAL_PRIVATE_QUERY">FICTIONAL_PRIVATE_TITLE Next story</a>'
        checkpoint,state=cp.collect_batch(FakeSession(bodies),START,END,max_list_pages=1)
        self.assertEqual(state["next_resume_url"],page(2))
        self.assertNotIn("FICTIONAL_PRIVATE",json.dumps(checkpoint))
        self.assertTrue(all(set(control)=={"role","target_page"} for control in checkpoint["batches"][0]["pages"][0]["controls"]))

    def test_decorated_article_next_in_actual_pager_cannot_manufacture_terminal(self):
        for container in ("page", "pagination", "pager", "paginator"):
            body = (f'<meta charset="utf-8"><li><a href="index-1.asp?Parser=9,4,20,,,,999">Fixture 2026-09-19</a></li>'
                    f'<nav class="{container}"><a href="{ROOT}">最末頁</a>'
                    '<a href="index-1.asp?Parser=9,4,20,,,,888">下一頁 — FICTIONAL_PAGER_LABEL</a></nav>').encode()
            original_navigation = cp.oc._traffic_news_list_next(cp.BeautifulSoup(body.decode(), "html.parser"), ROOT)
            self.assertEqual(original_navigation, (None, True))
            session = FakeSession({ROOT: body})
            with self.subTest(container=container), self.assertRaisesRegex(ValueError, "pagination"):
                cp.collect_batch(session, START, END, max_list_pages=1)
            self.assertEqual(session.fetched, [ROOT])

    def test_exact_navigation_with_private_attributes_projects_only_role_and_target(self):
        _, bodies=fixtures()
        bodies[ROOT]=bodies[ROOT].replace(b'>\xe4\xb8\x8b\xe4\xb8\x80\xe9\xa0\x81</a>',b' title="FICTIONAL_PRIVATE_TITLE" class="FICTIONAL_PRIVATE_CLASS">\xe4\xb8\x8b\xe4\xb8\x80\xe9\xa0\x81</a>')
        checkpoint,_=cp.collect_batch(FakeSession(bodies),START,END,max_list_pages=1)
        self.assertNotIn("FICTIONAL_PRIVATE",json.dumps(checkpoint))

    def test_invalid_next_cannot_be_dropped_to_turn_matching_last_into_terminal(self):
        for target in ("https://evil.example.test/?Parser=9,4,20",ROOT+"&private=FICTIONAL_PRIVATE_QUERY"):
            body=(f'<meta charset="utf-8"><li><a href="index-1.asp?Parser=9,4,20,,,,999">Fixture 2026-09-19</a></li>'
                  f'<a href="{ROOT}">最末頁</a><a href="{ROOT}">下一頁</a><a href="{target}">下一頁</a>').encode()
            with self.subTest(target=target),self.assertRaisesRegex(ValueError,"navigation|pagination"):
                cp.collect_batch(FakeSession({ROOT:body}),START,END,max_list_pages=1)

    def test_conflicting_exact_roles_in_one_control_are_blocked(self):
        _,bodies=fixtures()
        bodies[ROOT]=bodies[ROOT].replace(b'>\xe4\xb8\x8b\xe4\xb8\x80\xe9\xa0\x81</a>',b' rel="last">\xe4\xb8\x8b\xe4\xb8\x80\xe9\xa0\x81</a>')
        with self.assertRaisesRegex(ValueError,"role"):
            cp.collect_batch(FakeSession(bodies),START,END,max_list_pages=1)

    def test_module_binding_covers_row_parser_file_changes_without_navigation_changes(self):
        _,bodies=fixtures()
        session=FakeSession(bodies)
        checkpoint,_=cp.collect_batch(session,START,END,max_list_pages=1)
        session.fetched.clear()
        original_read=Path.read_bytes
        def changed_module_bytes(path):
            body=original_read(path)
            if path.name=="online_collect.py":
                self.assertIn(b'"published": roc_date(row_text)',body)
                return body.replace(b'"published": roc_date(row_text)',b'"published": None')
            return body
        with mock.patch.object(Path,"read_bytes",autospec=True,side_effect=changed_module_bytes):
            with self.assertRaisesRegex(ValueError,"binding changed.*restart"):
                cp.collect_batch(session,START,END,checkpoint=checkpoint,max_list_pages=1)
        self.assertEqual(session.fetched,[])

    def test_runtime_row_metadata_projection_change_also_invalidates_checkpoint(self):
        _,bodies=fixtures()
        session=FakeSession(bodies)
        checkpoint,_=cp.collect_batch(session,START,END,max_list_pages=1)
        session.fetched.clear()
        def different_projection(row):
            return {"stable_key":row["stable_key"],"published":None,"list_sha256":"a"*64}
        with mock.patch.object(cp,"row_metadata",different_projection):
            with self.assertRaisesRegex(ValueError,"binding changed.*restart"):
                cp.collect_batch(session,START,END,checkpoint=checkpoint,max_list_pages=1)
        self.assertEqual(session.fetched,[])

    def test_runtime_stable_key_pattern_change_rejects_resume_before_network(self):
        _,bodies=fixtures()
        session=FakeSession(bodies)
        checkpoint,_=cp.collect_batch(session,START,END,max_list_pages=1)
        session.fetched.clear()
        with mock.patch.dict(cp.oc.NEWS_LIST_SOURCES["S-032"],{"id_pattern":r"Parser=9,4,20,,,,(\d{2})"}):
            with self.assertRaisesRegex(ValueError,"binding changed.*restart"):
                cp.collect_batch(session,START,END,checkpoint=checkpoint,max_list_pages=1)
        self.assertEqual(session.fetched,[])

    def test_rebinding_old_raw_controls_cannot_turn_legacy_receipt_into_v2(self):
        _,bodies=fixtures()
        checkpoint,_=cp.collect_batch(FakeSession(bodies),START,END,max_list_pages=1)
        record=checkpoint["batches"][0]["pages"][0]
        record["controls"]=[{"tag":"a","label":"Next","href":page(2),"title":"FICTIONAL_PRIVATE_TITLE",
                             "aria-label":"","rel":["next"],"class":[]}]
        checkpoint["binding"]=cp.binding(START,END)
        with self.assertRaisesRegex(ValueError,"normalized pager"):
            cp.validate_checkpoint(rebuild(checkpoint),START,END)

    def test_cli_unresolved_batch_has_no_success_output_or_checkpoint_write(self):
        _,bodies=fixtures()
        bodies[page(2)]=(f'<meta charset="utf-8"><li><a href="index-1.asp?Parser=9,4,20,,,,998">Fixture 2026-09-18</a></li>'
                        f'<a href="{page(2)}">下一頁</a>').encode()
        session=FakeSession(bodies)
        session.close=mock.Mock()
        loader=mock.Mock()
        spec=SimpleNamespace(loader=loader)
        transport=SimpleNamespace(BoundedSession=lambda *args,**kwargs:session)
        with tempfile.TemporaryDirectory() as directory:
            output=Path(directory)/"output.json"
            output.write_bytes(b"FICTIONAL_PRIOR_RECEIPT")
            command=[str(CLI_PATH),"--start",START.isoformat(),"--end",END.isoformat(),"--output-checkpoint",str(output)]
            stdout=io.StringIO()
            with mock.patch.object(sys,"argv",command),mock.patch.object(cli.importlib.util,"spec_from_file_location",return_value=spec),\
                    mock.patch.object(cli.importlib.util,"module_from_spec",return_value=transport),\
                    mock.patch.object(cli,"write_checkpoint_atomic",wraps=cli.write_checkpoint_atomic) as writer,redirect_stdout(stdout):
                with self.assertRaisesRegex(ValueError,"unresolved"):
                    cli.main()
                writer.assert_not_called()
            self.assertEqual(stdout.getvalue(),"")
            self.assertEqual(output.read_bytes(),b"FICTIONAL_PRIOR_RECEIPT")
            self.assertEqual(session.fetched,[ROOT,page(2)])
            session.close.assert_called_once()


if __name__ == "__main__":
    unittest.main()
