"""Local continuation safety: no fabricated chain can authorize global coverage."""
from copy import deepcopy
from datetime import date
import json
import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import tempfile
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
        bodies[url] = (f'<li><a href="index-1.asp?Parser=9,4,20,,,,{1000-number}">Fixture {day}</a></li>'
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
        self.pages[self.urls[4]] = self.pages[self.urls[4]].replace(self.urls[-1].encode(), page(7).encode())
        with self.assertRaisesRegex(ValueError, "Last changed"):
            self.batch(checkpoint=first)

    def test_case_insensitive_rel_last_cannot_hide_changed_declarations(self):
        first, _ = self.batch(max_list_pages=6)
        pages = first["batches"][0]["pages"]
        for index, record in enumerate(pages):
            last = record["controls"][-1]
            last["label"] = ""
            last["rel"] = ["LAST" if index == 0 else "last"]
            if index == 0:
                last["href"] = page(7)
        with self.assertRaisesRegex(ValueError, "Last changed"):
            cp.validate_checkpoint(rebuild(first), START, END)

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
            ).encode())
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


if __name__ == "__main__":
    unittest.main()
