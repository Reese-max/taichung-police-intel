import importlib.util
import hashlib
import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

spec = importlib.util.spec_from_file_location("source_scope_contracts", Path(__file__).resolve().parents[1] / "scripts/source-scope-contracts.py")
scope = importlib.util.module_from_spec(spec)
spec.loader.exec_module(scope)
CURRENT = "https://law.taichung.gov.tw/DraftForum.aspx"
DETAIL = "https://law.taichung.gov.tw/DraftOpinion.aspx?id=112569&Type=H"
VIDEO = "https://vod.tccc.gov.tw/wb_news02.asp?url=92&ano=14872&pageno=1"
EMPTY = b'<form id="aspnetForm" action="./DraftForum.aspx"><div id="new" class="tab-pane active"><div id="ctl00_cp_content_divNoData">' + '尚無預告中的法規草案'.encode() + b'</div></div></form>'
DRAFT = ('<table><tr><td>公告日期：</td><td>115.08.25</td></tr><tr><td>預告日期：</td><td>115.08.26至115.09.01</td></tr></table>'
         '<a href="Download.ashx?FileID=137020&id=112569&type=ANN">附件甲</a><a href="Download.ashx?FileID=137021&id=112569&type=ANN">附件乙</a><p>PRIVATE_BODY_DO_NOT_PROJECT</p>').encode()
VOD = ('<b><font>第4屆第8次定期會</font></b><table class="pM"><tr><td>會議日期：</td><td>2026-10-01</td></tr></table>'
       '<div class="video-container"><iframe src="https://player.example/vod/opaque_1151001"></iframe></div><p>PRIVATE_BODY_DO_NOT_PROJECT</p>').encode()


class CurrentContractTests(unittest.TestCase):
    def test_visible_current_empty_is_zero_only_for_current_snapshot(self):
        result = scope.parse_current_drafts(EMPTY, CURRENT)
        self.assertEqual(result["status"], "PUBLISHER_DECLARED_EMPTY_CURRENT_SNAPSHOT")
        self.assertEqual(result["current_snapshot_count"], 0)
        self.assertIsNone(result["requested_window_count"])
        self.assertEqual(result["business_scope_completeness"], "NOT_VERIFIED")
        self.assertFalse(result["promotion_eligible"])
        self.assertIsNone(result["human_review"])

    def test_history_or_filtered_urls_cannot_be_zero_current(self):
        for suffix in ["?Type=H", "?keyword=x", "?Type=&type=H", "?page=1"]:
            with self.subTest(suffix=suffix):
                self.assertIsNone(scope.parse_current_drafts(EMPTY, CURRENT + suffix)["current_snapshot_count"])

    def test_missing_table_alone_is_unknown(self):
        self.assertIsNone(scope.parse_current_drafts(b'<html><body>no table</body></html>', CURRENT)["current_snapshot_count"])

    def test_hidden_descendant_empty_marker_text_cannot_prove_zero(self):
        text = '尚無預告中的法規草案'.encode()
        raw = EMPTY.replace(text, b'<span hidden>' + text + b'</span>')
        self.assertIsNone(scope.parse_current_drafts(raw, CURRENT)['current_snapshot_count'])

    def test_hidden_inactive_or_duplicate_empty_markers_reject_zero(self):
        for raw in [EMPTY.replace(b'tab-pane active', b'tab-pane'), EMPTY.replace(b'<div id="ctl00', b'<div hidden id="ctl00'), EMPTY.replace(b'<div id="new"', b'<div style="display: none" id="new"'), EMPTY + EMPTY]:
            with self.subTest(raw=raw):
                self.assertIsNone(scope.parse_current_drafts(raw, CURRENT)["current_snapshot_count"])

    def test_conflicting_list_or_http_error_rejects_zero(self):
        for raw in [EMPTY + b'<table id="tableList"></table>', EMPTY.replace(b'</div></div>', b'<a href="DraftOpinion.aspx?id=1">item</a></div></div>'), EMPTY + b'<h1>Server Error in application</h1>']:
            self.assertIsNone(scope.parse_current_drafts(raw, CURRENT)["current_snapshot_count"])
        self.assertIsNone(scope.parse_current_drafts(EMPTY, CURRENT, http_status=503)["current_snapshot_count"])
        self.assertIsNone(scope.parse_current_drafts(EMPTY, CURRENT, final_url=CURRENT + '?Type=H')["current_snapshot_count"])

    def test_form_or_empty_semantics_must_match(self):
        for raw in [EMPTY.replace(b'./DraftForum.aspx', b'./DraftForum.aspx?Type=H'), EMPTY.replace(b' action="./DraftForum.aspx"', b''), EMPTY.replace('尚無預告中的法規草案'.encode(), '目前服務維護'.encode()), EMPTY.replace('尚無預告中的法規草案'.encode(), '尚無更新但預告中草案存在'.encode())]:
            self.assertIsNone(scope.parse_current_drafts(raw, CURRENT)["current_snapshot_count"])

    def test_rejected_private_query_or_credentials_are_not_echoed(self):
        for url in [CURRENT + '?keyword=PRIVATE_SEARCH_VALUE', CURRENT.replace('https://', 'https://user:PRIVATE_TOKEN@'), CURRENT.replace('/DraftForum.aspx', '/PRIVATE_BODY_PATH')]:
            result = scope.parse_current_drafts(EMPTY, url)
            self.assertIsNone(result["official_url"])
            self.assertNotIn('PRIVATE_', json.dumps(result))

    def test_multiple_active_history_nav_hidden_type_and_visible_outside_item_reject(self):
        for raw in [EMPTY + b'<div id="old" class="tab-pane active">history</div>',
                    EMPTY + b'<ul class="nav"><li class="active"><a href="DraftForum.aspx?Type=H">history</a></li></ul>',
                    EMPTY.replace(b'</form>', b'<input name="Type" value="H" type="hidden"></form>'),
                    EMPTY + b'<a href="DraftOpinion.aspx?id=99">draft</a>',
                    EMPTY + b'<a href="draftopinion.aspx?id=99">draft</a>',
                    EMPTY + b'<h1>Access Denied</h1>',
                    EMPTY + b'<form id="login"><input type="password"></form>',
                    EMPTY + '<h1>系統維護中</h1>'.encode()]:
            with self.subTest(raw=raw):
                self.assertIsNone(scope.parse_current_drafts(raw, CURRENT)["current_snapshot_count"])


class DetailContractTests(unittest.TestCase):
    def test_announcement_notice_and_two_attachments_have_separate_roles(self):
        result = scope.parse_draft_detail(DRAFT, DETAIL)
        self.assertEqual(result["announcement_date"], "2026-08-25")
        self.assertEqual(result["pre_notice_interval"]["end"], "2026-09-01")
        self.assertEqual([a["attachment_id"] for a in result["attachments"]], ["137020", "137021"])
        self.assertEqual(result["deadline_semantics"], "NOT_VERIFIED_PRE_NOTICE_INTERVAL_ONLY")
        self.assertEqual(result["version_semantics"], "NOT_VERIFIED")
        self.assertEqual(result["attachment_byte_acquisition"], "NOT_ASSERTED_BY_OFFLINE_PARSER")

    def test_invalid_or_reversed_notice_dates_are_not_accepted(self):
        for raw in [DRAFT.replace(b'115.08.25', b'115.02.30'), DRAFT.replace(b'115.08.26', b'115.09.02')]:
            self.assertTrue(scope.parse_draft_detail(raw, DETAIL)["reason_codes"])

    def test_other_draft_cross_origin_or_duplicate_query_attachments_reject(self):
        for raw in [DRAFT.replace(b'FileID=137020&id=112569', b'FileID=137020&id=999'), DRAFT.replace(b'Download.ashx?FileID=137020', b'https://other.example/Download.ashx?FileID=137020'), DRAFT.replace(b'FileID=137020', b'FileID=137020&fileid=999'), DRAFT.replace(b'FileID=137020', b'FileID=137020&unknown=999')]:
            self.assertEqual(scope.parse_draft_detail(raw, DETAIL)["unrecognized_visible_download_links"], 1)

    def test_unknown_detail_query_is_not_part_of_contract(self):
        self.assertIn("DRAFT_DETAIL_IDENTITY_NOT_RECOGNIZED", scope.parse_draft_detail(DRAFT, DETAIL + '&unknown=999')["reason_codes"])

    def test_hidden_announcement_date_is_not_an_official_visible_date(self):
        result = scope.parse_draft_detail(DRAFT.replace(b'<td>115.08.25', b'<td hidden>115.08.25'), DETAIL)
        self.assertIsNone(result['announcement_date'])
        self.assertIn('ANNOUNCEMENT_DATE_NOT_RECOGNIZED', result['reason_codes'])

    def test_hidden_descendant_announcement_date_is_not_accepted(self):
        raw = DRAFT.replace(b'115.08.25', b'<span hidden>115.08.25</span>')
        self.assertIsNone(scope.parse_draft_detail(raw, DETAIL)['announcement_date'])

    def test_lowercase_download_path_still_has_draft_identity_checks(self):
        raw = DRAFT.replace(b'Download.ashx?FileID=137020&id=112569', b'download.ashx?FileID=137020&id=999')
        self.assertEqual(scope.parse_draft_detail(raw, DETAIL)['unrecognized_visible_download_links'], 1)

    def test_private_body_and_marker_text_are_never_projected(self):
        self.assertNotIn("PRIVATE_BODY_DO_NOT_PROJECT", json.dumps(scope.parse_draft_detail(DRAFT, DETAIL)))
        self.assertNotIn("尚無預告中的法規草案", json.dumps(scope.parse_current_drafts(EMPTY, CURRENT), ensure_ascii=False))


class VideoContractTests(unittest.TestCase):
    def test_meeting_date_is_explicit_and_player_id_is_opaque(self):
        result = scope.parse_video_detail("S-010", VOD, VIDEO)
        self.assertEqual(result["meeting_date"], "2026-10-01")
        self.assertEqual(result["meeting_date_role"], "OFFICIAL_MEETING_DATE_LABEL")
        self.assertEqual(result["publication_date"], "UNKNOWN")
        self.assertEqual(result["visible_player_embed_count"], 1)
        self.assertEqual(result["timeline_semantics"], "NOT_VERIFIED")
        self.assertEqual(result["official_title_numeric_identifiers"][0]["regular_session"], 8)
        self.assertNotIn("PRIVATE_BODY_DO_NOT_PROJECT", json.dumps(result))

    def test_two_agenda_players_are_not_collapsed_to_one_meeting_video(self):
        raw = VOD.replace(b'</div><p>', b'<iframe src="https://player.example/vod/second"></iframe></div><p>')
        result = scope.parse_video_detail("S-011", raw, "https://agenda-vod.tccc.gov.tw/v2_news02.asp?url=92&ano=594")
        self.assertEqual(result["visible_player_embed_count"], 2)
        self.assertEqual(result["playback_verification"], "NOT_RUN")

    def test_publication_label_or_filename_date_cannot_become_meeting_date(self):
        raw = VOD.replace('會議日期'.encode(), '發布日期'.encode())
        self.assertIsNone(scope.parse_video_detail("S-010", raw, VIDEO)["meeting_date"])

    def test_wrong_source_id_duplicate_ids_and_credential_embeds_reject(self):
        self.assertIn("VIDEO_DETAIL_IDENTITY_NOT_RECOGNIZED", scope.parse_video_detail("S-011", VOD, VIDEO)["reason_codes"])
        self.assertIn("VIDEO_DETAIL_IDENTITY_NOT_RECOGNIZED", scope.parse_video_detail("S-010", VOD, VIDEO + '&ANO=9')["reason_codes"])
        self.assertIn("VIDEO_DETAIL_IDENTITY_NOT_RECOGNIZED", scope.parse_video_detail("S-010", VOD, VIDEO + '&unknown=9')["reason_codes"])
        raw = VOD.replace(b'https://player.example/', b'https://user:secret@player.example/')
        self.assertEqual(scope.parse_video_detail("S-010", raw, VIDEO)["visible_player_embed_count"], 0)

    def test_invalid_calendar_date_does_not_pass(self):
        self.assertIsNone(scope.parse_video_detail("S-010", VOD.replace(b'2026-10-01', b'2026-02-30'), VIDEO)["meeting_date"])

    def test_hidden_meeting_date_cell_is_not_promoted_from_markup(self):
        raw = VOD.replace(b'<td>2026-10-01', b'<td style="display:none">2026-10-01')
        self.assertIsNone(scope.parse_video_detail('S-010', raw, VIDEO)['meeting_date'])

    def test_hidden_descendant_meeting_date_is_not_accepted(self):
        raw = VOD.replace(b'2026-10-01', b'<span hidden>2026-10-01</span>')
        self.assertIsNone(scope.parse_video_detail('S-010', raw, VIDEO)['meeting_date'])

    def test_nonascii_html_or_long_player_ids_are_hash_only(self):
        for value in ['subtitle_PRIVATE_BODY_中文', '%2F..%2FPRIVATE_BODY', 'a' * 161, '%3Cscript%3EPRIVATE_BODY', 'PRIVATE_BODY_DO_NOT_PROJECT']:
            raw = VOD.replace(b'opaque_1151001', value.encode())
            result = scope.parse_video_detail('S-010', raw, VIDEO)
            self.assertEqual(result['visible_player_embed_count'], 1)
            self.assertIsNone(result['player_embeds'][0]['opaque_player_identifier'])
            self.assertNotIn('PRIVATE_BODY', json.dumps(result))


class BoundedFileTests(unittest.TestCase):
    def test_regular_file_reader_is_bounded(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'input'
            path.write_bytes(EMPTY)
            self.assertEqual(scope.read_bounded_regular_file(path), EMPTY)
            path.write_bytes(b'a' * (scope.MAX_BYTES + 1))
            with self.assertRaisesRegex(ValueError, 'bounded regular'):
                scope.read_bounded_regular_file(path)

    @unittest.skipUnless(hasattr(os, 'mkfifo'), 'POSIX FIFO required')
    def test_fifo_and_directory_are_rejected_without_waiting_for_writer(self):
        with tempfile.TemporaryDirectory() as temp:
            fifo = Path(temp) / 'fifo'
            os.mkfifo(fifo)
            with self.assertRaisesRegex(ValueError, 'bounded regular'):
                scope.read_bounded_regular_file(fifo)
            with self.assertRaisesRegex(ValueError, 'bounded regular'):
                scope.read_bounded_regular_file(temp)


class MetadataOutputTests(unittest.TestCase):
    def run_cli(self, source, output):
        return subprocess.run([sys.executable, str(Path(scope.__file__)), '--source-id', 'S-026',
                               '--kind', 'current', '--url', CURRENT, '--input', str(source),
                               '--output', str(output)], capture_output=True, text=True, timeout=8)

    def test_cli_rejects_same_path_and_resolved_alias_without_changing_original(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            source = directory / 'original.html'
            (directory / 'nested').mkdir()
            before = hashlib.sha256(EMPTY).hexdigest()
            for output in [source, directory / 'nested' / '..' / 'original.html']:
                with self.subTest(output=output):
                    source.write_bytes(EMPTY)
                    result = self.run_cli(source, output)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertEqual(hashlib.sha256(source.read_bytes()).hexdigest(), before)
                    self.assertEqual(sorted(p.name for p in directory.iterdir()), ['nested', 'original.html'])

    def test_cli_rejects_symlink_and_hardlink_alias_without_changing_original(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            source = directory / 'original.html'
            source.write_bytes(EMPTY)
            symlink = directory / 'symbolic.json'
            symlink.symlink_to(source)
            hardlink = directory / 'hard.json'
            os.link(source, hardlink)
            before = hashlib.sha256(source.read_bytes()).hexdigest()
            for output in [symlink, hardlink]:
                with self.subTest(output=output):
                    source.write_bytes(EMPTY)
                    result = self.run_cli(source, output)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertEqual(hashlib.sha256(source.read_bytes()).hexdigest(), before)
                    self.assertEqual(hashlib.sha256(output.read_bytes()).hexdigest(), before)
            self.assertTrue(symlink.is_symlink())
            self.assertEqual(source.stat().st_ino, hardlink.stat().st_ino)

    def test_distinct_output_is_private_atomic_replacement(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            source = directory / 'original.html'
            source.write_bytes(EMPTY)
            output = directory / 'metadata.json'
            output.write_text('prior metadata')
            os.chmod(output, 0o666)
            # A retained hardlink proves that the old output inode is not edited.
            previous = directory / 'previous.json'
            os.link(output, previous)
            fixed = directory / 'metadata.json.tmp'
            fixed.symlink_to(source)
            result = self.run_cli(source, output)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(output.read_text())['current_snapshot_count'], 0)
            self.assertEqual(stat.S_IMODE(output.stat().st_mode), 0o600)
            self.assertEqual(previous.read_text(), 'prior metadata')
            self.assertEqual(source.read_bytes(), EMPTY)
            self.assertTrue(fixed.is_symlink())
            self.assertEqual(fixed.read_bytes(), EMPTY)
            self.assertEqual(sorted(p.name for p in directory.iterdir()),
                             ['metadata.json', 'metadata.json.tmp', 'original.html', 'previous.json'])

    def test_symlink_to_unrelated_target_is_replaced_without_following_it(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            source = directory / 'original.html'
            source.write_bytes(EMPTY)
            target = directory / 'unrelated.txt'
            target.write_text('keep unrelated inode')
            output = directory / 'metadata.json'
            output.symlink_to(target)
            result = self.run_cli(source, output)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse(output.is_symlink())
            self.assertEqual(target.read_text(), 'keep unrelated inode')
            self.assertEqual(json.loads(output.read_text())['current_snapshot_count'], 0)
            self.assertEqual(source.read_bytes(), EMPTY)

    def test_output_directory_error_preserves_original_and_cleans_temporary_files(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            source = directory / 'original.html'
            source.write_bytes(EMPTY)
            output = directory / 'metadata'
            output.mkdir()
            (output / 'keep.txt').write_text('keep')
            before_names = sorted(p.name for p in directory.iterdir())
            result = self.run_cli(source, output)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(source.read_bytes(), EMPTY)
            self.assertEqual((output / 'keep.txt').read_text(), 'keep')
            self.assertEqual(sorted(p.name for p in directory.iterdir()), before_names)


if __name__ == "__main__":
    unittest.main()
