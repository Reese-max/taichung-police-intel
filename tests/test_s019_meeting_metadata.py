"""Fictional ODT tests; no official documents, source HTTP or provider calls."""
from html import escape
import io
import json
import struct
import unittest
from unittest import mock
import warnings
import zipfile
import zlib

from intel_v2 import s019_meeting_metadata as parser


def xml(body):
    return ('<?xml version="1.0" encoding="UTF-8"?>'
            f'<office:document-content xmlns:office="{parser.OFFICE}" xmlns:text="{parser.TEXT}" xmlns:table="{parser.TABLE}">'
            '<office:body><office:text>' + body + '</office:text></office:body></office:document-content>').encode()


def paragraph(value):
    return '<text:p>' + escape(value) + '</text:p>'


def odt(body=None, *, content=None, mimetype=parser.MIMETYPE, extra=()):
    if body is None:
        body = paragraph('臺中市政府第749次市政會議紀錄') + paragraph('一、時間：中華民國115年9月21日（星期一）上午9時至11時')
    out = io.BytesIO()
    with zipfile.ZipFile(out, 'w') as archive:
        archive.writestr('mimetype', mimetype, compress_type=zipfile.ZIP_STORED)
        archive.writestr('content.xml', content if content is not None else xml(body), compress_type=zipfile.ZIP_DEFLATED)
        for name, value in extra:
            archive.writestr(name, value, compress_type=zipfile.ZIP_DEFLATED)
    return out.getvalue()


def forged_prefix_odt(prefix, hidden_bytes):
    """A ZIP reader would truncate the deflate stream to the forged prefix."""
    raw = bytearray(odt(content=prefix + hidden_bytes))
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        info = archive.getinfo('content.xml')
        central = archive.start_dir
    while raw[central:central + 4] == b'PK\x01\x02':
        name_size, extra_size, comment_size = struct.unpack_from('<3H', raw, central + 28)
        if raw[central + 46:central + 46 + name_size] == b'content.xml':
            break
        central += 46 + name_size + extra_size + comment_size
    crc = zlib.crc32(prefix) & 0xffffffff
    struct.pack_into('<I', raw, info.header_offset + 14, crc)
    struct.pack_into('<I', raw, info.header_offset + 22, len(prefix))
    struct.pack_into('<I', raw, central + 16, crc)
    struct.pack_into('<I', raw, central + 24, len(prefix))
    return bytes(raw)


class S019OdtMetadataTests(unittest.TestCase):
    def parse(self, body=None, **kwargs):
        return parser.parse_odt_meeting_metadata(odt(body, **kwargs))

    def test_explicit_header_roc_date_is_normalized_without_version_claim(self):
        result = self.parse()
        self.assertEqual(result['meeting_number'], 749)
        self.assertEqual(result['meeting_date'], '2026-09-21')
        self.assertEqual(result['explicit_date_label_roles'], ['TIME'])
        self.assertEqual(result['status'], 'STRUCTURED_MEETING_METADATA_OBSERVED')
        self.assertIsNone(result['official_version_identifier'])
        self.assertFalse(result['rights_approved'])
        self.assertFalse(result['promotion_eligible'])
        self.assertFalse(result['model_transmission_allowed'])
        self.assertEqual(result['network_requests'], 0)

    def test_explicit_roc_era_requires_a_roc_year(self):
        for value in ('民國2026年9月21日', '中華民國2026/9/21'):
            with self.subTest(invalid=value):
                result = self.parse(paragraph('臺中市政府第749次市政會議紀錄') + paragraph('時間：' + value))
                self.assertIsNone(result['meeting_date'])
                self.assertIn('ERA_YEAR_MISMATCH', result['reason_codes'])
        for value in ('民國115年9月21日', '中華民國115/9/21', '115年9月21日', '115/9/21', '2026年9月21日', '2026/9/21'):
            with self.subTest(valid=value):
                self.assertEqual(self.parse(paragraph('臺中市政府第749次市政會議紀錄') + paragraph('時間：' + value))['meeting_date'], '2026-09-21')

    def test_explicit_weekday_must_match_the_calendar_date(self):
        for weekday in ('星期二', '週二', '周日', '禮拜天'):
            with self.subTest(invalid=weekday):
                result = self.parse(paragraph('臺中市政府第749次市政會議紀錄') + paragraph('時間：115年9月21日(' + weekday + ')'))
                self.assertIsNone(result['meeting_date'])
                self.assertIn('MEETING_WEEKDAY_CONFLICT', result['reason_codes'])
        for value, expected in (('115年9月21日(星期一)上午9時', '2026-09-21'),
                                ('2026/9/21(週一)', '2026-09-21'),
                                ('2026/9/27(星期日)', '2026-09-27'),
                                ('2026/9/27(禮拜天)', '2026-09-27'),
                                ('115年9月21日', '2026-09-21')):
            with self.subTest(valid=value):
                self.assertEqual(self.parse(paragraph('臺中市政府第749次市政會議紀錄') + paragraph('時間：' + value))['meeting_date'], expected)
        conflicting = paragraph('臺中市政府第749次市政會議紀錄') + paragraph('時間：115年9月21日(星期二)') + paragraph('會議日期：2026-09-21')
        self.assertIsNone(self.parse(conflicting)['meeting_date'])

    def test_date_label_and_value_in_same_table_row_are_associated(self):
        title = '<text:h>臺中市政府第７４９次市政會議紀錄（定稿）</text:h>'
        row = '<table:table><table:table-row><table:table-cell>' + paragraph('會議日期：') + '</table:table-cell><table:table-cell>' + paragraph('2026/9/21') + '</table:table-cell></table:table-row></table:table>'
        result = self.parse(title + row)
        self.assertEqual(result['meeting_number'], 749)
        self.assertEqual(result['meeting_date'], '2026-09-21')
        self.assertEqual(result['explicit_date_label_roles'], ['MEETING_DATE'])
        self.assertIsNone(result['official_version_identifier'])

    def test_unlabeled_body_or_meta_date_is_not_meeting_date(self):
        body = paragraph('臺中市政府第749次市政會議紀錄') + paragraph('2026-09-21')
        result = self.parse(body, extra=(('meta.xml', b'<meta>2026-09-21 FICTIONAL_PRIVATE_META_TITLE</meta>'),))
        self.assertEqual(result['meeting_number'], 749)
        self.assertIsNone(result['meeting_date'])
        self.assertNotIn('FICTIONAL_PRIVATE', json.dumps(result))

    def test_valid_date_without_explicit_meeting_title_stays_unknown(self):
        result = self.parse(paragraph('FICTIONAL_OTHER_DOCUMENT') + paragraph('時間：115年9月21日'))
        self.assertIsNone(result['meeting_number'])
        self.assertIsNone(result['meeting_date'])

    def test_conflicting_meeting_titles_do_not_bind_one_date(self):
        body = paragraph('臺中市政府第749次市政會議紀錄') + paragraph('臺中市政府第750次市政會議紀錄') + paragraph('時間：115年9月21日')
        result = self.parse(body)
        self.assertIsNone(result['meeting_number'])
        self.assertIsNone(result['meeting_date'])
        self.assertIn('AMBIGUOUS_MEETING_NUMBER', result['reason_codes'])

    def test_multiple_dates_and_short_date_ranges_stay_unknown(self):
        for value in ('115年9月21日、115年9月22日', '115年9月21日至22日', '115年9月21日至9月22日'):
            with self.subTest(value=value):
                result = self.parse(paragraph('臺中市政府第749次市政會議紀錄') + paragraph('時間：' + value))
                self.assertIsNone(result['meeting_date'])
                self.assertIn('AMBIGUOUS_MEETING_DATE', result['reason_codes'])

    def test_invalid_calendar_date_stays_unknown_even_with_valid_second_label(self):
        body = paragraph('臺中市政府第749次市政會議紀錄') + paragraph('時間：115年2月30日') + paragraph('會議日期：2026-09-21')
        result = self.parse(body)
        self.assertIsNone(result['meeting_date'])
        self.assertIn('INVALID_MEETING_DATE', result['reason_codes'])

    def test_unknown_revision_or_historical_label_values_are_not_meeting_dates(self):
        for value in ('未定（前次會議日期115年9月21日）', '修訂於115年9月21日', '115年9月21日（修訂）'):
            with self.subTest(value=value):
                result = self.parse(paragraph('臺中市政府第749次市政會議紀錄') + paragraph('會議日期：' + value))
                self.assertIsNone(result['meeting_date'])

    def test_slash_short_ranges_are_not_single_meeting_dates(self):
        for value in ('2026/9/21至22', '115/9/21-22', '2026-09-21～09-22'):
            with self.subTest(value=value):
                result = self.parse(paragraph('臺中市政府第749次市政會議紀錄') + paragraph('會議日期：' + value))
                self.assertIsNone(result['meeting_date'])

    def test_unresolved_date_label_cannot_be_overridden_by_another_time_label(self):
        body = paragraph('臺中市政府第749次市政會議紀錄') + paragraph('會議日期：未定') + paragraph('時間：115年9月21日')
        self.assertIsNone(self.parse(body)['meeting_date'])

    def test_identical_repeated_title_and_date_are_consistent(self):
        body = (paragraph('臺中市政府第749次市政會議紀錄') + paragraph('時間：115年9月21日')) * 2
        self.assertEqual(self.parse(body)['meeting_date'], '2026-09-21')

    def test_agenda_references_and_hidden_changes_are_not_header_identity(self):
        header = paragraph('臺中市政府第749次市政會議紀錄') + paragraph('時間：115年9月21日')
        hidden = '<office:annotation>' + paragraph('臺中市政府第750次市政會議紀錄') + '</office:annotation><text:tracked-changes>' + paragraph('時間：115年10月1日') + '</text:tracked-changes>'
        agenda = paragraph('二、報告事項') + paragraph('臺中市政府第748次市政會議紀錄') + paragraph('時間：115年9月14日')
        result = self.parse(header + hidden + agenda)
        self.assertEqual(result['meeting_number'], 749)
        self.assertEqual(result['meeting_date'], '2026-09-21')

    def test_private_body_text_is_never_projected(self):
        result = self.parse(paragraph('臺中市政府第749次市政會議紀錄') + paragraph('時間：115年9月21日') + paragraph('FICTIONAL_PRIVATE_BODY_MARKER'))
        self.assertNotIn('FICTIONAL_PRIVATE', json.dumps(result))
        self.assertRegex(result['source_body_sha256'], r'^[0-9a-f]{64}$')

    def test_raw_limit_rejected_before_zip_parser(self):
        with mock.patch.object(parser.zipfile, 'ZipFile', side_effect=AssertionError('must not parse oversized bytes')):
            result = parser.parse_odt_meeting_metadata(b'x' * (parser.MAX_RAW_BYTES + 1))
        self.assertEqual(result['reason_codes'], ['RAW_BYTE_LIMIT'])
        self.assertIsNone(result['source_body_sha256'])

    def test_pdf_bytes_are_not_an_odt(self):
        result = parser.parse_odt_meeting_metadata(b'%PDF-1.7 FICTIONAL')
        self.assertIsNone(result['meeting_number'])
        self.assertEqual(result['status'], 'NOT_VERIFIED')

    def test_wrong_mimetype_or_missing_content_rejected(self):
        self.assertEqual(self.parse(mimetype=b'application/vnd.oasis.opendocument.spreadsheet')['reason_codes'], ['ODT_MIMETYPE_UNRECOGNIZED'])
        out = io.BytesIO()
        with zipfile.ZipFile(out, 'w') as archive:
            archive.writestr('mimetype', parser.MIMETYPE)
        self.assertEqual(parser.parse_odt_meeting_metadata(out.getvalue())['reason_codes'], ['ODT_REQUIRED_MEMBERS_MISSING'])

    def test_duplicate_zip_members_fail_closed(self):
        with warnings.catch_warnings():
            warnings.simplefilter('ignore', UserWarning)
            result = self.parse(extra=(('content.xml', xml(paragraph('FICTIONAL_AMBIGUOUS_BODY'))),))
        self.assertEqual(result['reason_codes'], ['ZIP_DUPLICATE_MEMBERS'])

    def test_zip_member_count_bound(self):
        result = self.parse(extra=tuple((f'fictional-{i}', b'x') for i in range(parser.MAX_MEMBERS)))
        self.assertEqual(result['reason_codes'], ['ZIP_MEMBER_COUNT_LIMIT'])

    def test_zip_bomb_rejected_before_member_decompression(self):
        raw = odt(content=b'x' * 200000)
        with mock.patch.object(zipfile.ZipFile, 'open', side_effect=AssertionError('reject ratio before decompress')):
            result = parser.parse_odt_meeting_metadata(raw)
        self.assertEqual(result['reason_codes'], ['ZIP_COMPRESSION_BOUND'])

    def test_fake_zip_size_cannot_hide_decompressed_tail(self):
        prefix = xml(paragraph('臺中市政府第749次市政會議紀錄') + paragraph('時間：115年9月21日'))
        raw = forged_prefix_odt(prefix, b'X' * 1024 * 1024)
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            # Reproduces why trusting ZipExtFile length alone is insufficient.
            self.assertEqual(archive.read('content.xml'), prefix)
        result = parser.parse_odt_meeting_metadata(raw)
        self.assertIsNone(result['meeting_number'])
        self.assertEqual(result['reason_codes'], ['ZIP_MEMBER_SIZE_OR_CRC_MISMATCH'])

    def test_actual_deflate_output_has_fixed_limit_plus_one(self):
        prefix = b'x'
        raw = forged_prefix_odt(prefix, b'X' * 1024 * 1024)
        requests = []
        real_decoder = parser.zlib.decompressobj
        def observed_decoder(*args):
            decoder = real_decoder(*args)
            class Decoder:
                def decompress(self, data, maximum):
                    requests.append(maximum)
                    return decoder.decompress(data, maximum)
                def __getattr__(self, name):
                    return getattr(decoder, name)
            return Decoder()
        with mock.patch.object(parser, 'MAX_XML_BYTES', 128), mock.patch.object(parser.zlib, 'decompressobj', side_effect=observed_decoder):
            result = parser.parse_odt_meeting_metadata(raw)
        self.assertEqual(result['reason_codes'], ['ZIP_MEMBER_BYTE_LIMIT'])
        self.assertEqual(requests, [129])

    def test_dtd_and_utf16_entity_cannot_bypass_xml_guard(self):
        hostile = xml(paragraph('&fictional;')).replace(b'<office:document-content', b'<!DOCTYPE x [<!ENTITY fictional "FICTIONAL_PRIVATE_ENTITY">]><office:document-content', 1)
        self.assertEqual(self.parse(content=hostile)['reason_codes'], ['XML_DTD_OR_ENTITY_DECLARATION'])
        hostile16 = hostile.decode().replace('encoding="UTF-8"', 'encoding="UTF-16"').encode('utf-16')
        self.assertEqual(self.parse(content=hostile16)['reason_codes'], ['XML_ENCODING_UNSUPPORTED'])

    def test_xml_depth_and_node_bounds(self):
        nested = '<text:section>' * 150 + paragraph('FICTIONAL') + '</text:section>' * 150
        self.assertEqual(self.parse(nested)['reason_codes'], ['XML_DEPTH_LIMIT'])
        with mock.patch.object(parser, 'MAX_XML_NODES', 10):
            self.assertEqual(self.parse(paragraph('FICTIONAL') * 20)['reason_codes'], ['XML_NODE_LIMIT'])

    def test_space_expansion_is_bounded(self):
        body = '<text:p>FICTIONAL<text:s text:c="9999999999"/></text:p>'
        self.assertEqual(self.parse(body)['reason_codes'], ['ODT_SPACE_EXPANSION_LIMIT'])


if __name__ == '__main__':
    unittest.main()
