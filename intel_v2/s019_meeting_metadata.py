"""Extract bounded, explicit S019 ODT header metadata without raw projection.

This recognizes supplied text-flow metadata, not document rendering, a registry
identity, an official version, completeness, rights, or human verification.
"""
from __future__ import annotations

import hashlib
import io
import re
import struct
import unicodedata
import zipfile
import zlib
from datetime import date
from xml.etree import ElementTree as ET

CONTRACT = "S019_ODT_EXPLICIT_HEADER_V1"
MAX_RAW_BYTES = 2 * 1024 * 1024
MAX_MEMBERS = 128
MAX_UNCOMPRESSED_BYTES = 8 * 1024 * 1024
MAX_XML_BYTES = 4 * 1024 * 1024
MAX_COMPRESSION_RATIO = 100
MAX_XML_NODES = 50000
MAX_XML_DEPTH = 128
MAX_XML_TEXT_CHARS = 2 * 1024 * 1024
MAX_BLOCK_CHARS = 8192
MAX_HEADER_BLOCKS = 32
OFFICE = "urn:oasis:names:tc:opendocument:xmlns:office:1.0"
TEXT = "urn:oasis:names:tc:opendocument:xmlns:text:1.0"
TABLE = "urn:oasis:names:tc:opendocument:xmlns:table:1.0"
MIMETYPE = b"application/vnd.oasis.opendocument.text"
TITLE = re.compile(r"^(?:臺中市政府)?(?:第([0-9]{1,5})次市政會議|市政會議第([0-9]{1,5})次)(?:會議紀錄|紀錄|記錄|議程)?(?:\((?:定稿|草案|修正版)\))?$")
DATE_LABEL = re.compile(r"^(?:[一二三四五六七八九十壹貳參肆伍陸柒捌玖拾0-9]+[、.])?(會議日期|會議時間|開會時間|時間|日期):(.*)$")
DATE_TOKEN = re.compile(r"(?<![0-9])(中華民國|民國)?(1[0-9]{2}|20[0-9]{2})(?:年([0-9]{1,2})月([0-9]{1,2})日|[./-]([0-9]{1,2})[./-]([0-9]{1,2}))(?![0-9])")
DATE_ROLES = {"會議日期": "MEETING_DATE", "會議時間": "MEETING_TIME", "開會時間": "MEETING_TIME", "時間": "TIME", "日期": "DATE"}
CLOCK = r"(?:上午|下午|早上|晚上|中午)?(?:[01]?[0-9]|2[0-3])(?:時(?:[0-5]?[0-9]分)?|:[0-5][0-9])(?:整)?"
CLOCK_VALUE = re.compile(CLOCK + r"(?:[至到~～-]" + CLOCK + r")?$")
DATE_SUFFIX = re.compile(r"(?:\((?:星期|週|周|禮拜)([一二三四五六日天])\))?(?:" + CLOCK + r"(?:[至到~～-]" + CLOCK + r")?)?$")
WEEKDAYS = {"一": 0, "二": 1, "三": 2, "四": 3, "五": 4, "六": 5, "日": 6, "天": 6}
SKIP_TAGS = {f"{{{OFFICE}}}annotation", f"{{{TEXT}}}tracked-changes", f"{{{TEXT}}}deletion", f"{{{TEXT}}}hidden-text", f"{{{TEXT}}}conditional-text", f"{{{TEXT}}}hidden-paragraph"}


class _Rejected(ValueError):
    pass


class _BoundedTreeBuilder(ET.TreeBuilder):
    """Count character callbacks before storing text, including unfinished tails."""
    def __init__(self):
        super().__init__()
        self.nodes = self.depth = self.text_chars = 0

    def start(self, tag, attrs):
        self.nodes += 1
        self.depth += 1
        if self.depth > MAX_XML_DEPTH:
            raise _Rejected("XML_DEPTH_LIMIT")
        if self.nodes > MAX_XML_NODES:
            raise _Rejected("XML_NODE_LIMIT")
        return super().start(tag, attrs)

    def end(self, tag):
        self.depth -= 1
        return super().end(tag)

    def data(self, value):
        self.text_chars += len(value)
        if self.text_chars > MAX_XML_TEXT_CHARS:
            raise _Rejected("XML_TEXT_LIMIT")
        super().data(value)


def _member_bytes(raw, archive, info, limit):
    """Bound actual deflate output, including bytes hidden by forged ZIP sizes."""
    if info.file_size > limit or info.compress_size > MAX_RAW_BYTES:
        raise _Rejected("ZIP_MEMBER_BYTE_LIMIT")
    offset = info.header_offset
    if not 0 <= offset <= len(raw) - 30:
        raise _Rejected("ZIP_LOCAL_HEADER_MISMATCH")
    header = struct.unpack_from("<4s5H3I2H", raw, offset)
    if header[0] != b"PK\x03\x04" or header[2] != info.flag_bits or header[3] != info.compress_type:
        raise _Rejected("ZIP_LOCAL_HEADER_MISMATCH")
    if not header[2] & 8 and (header[6], header[7], header[8]) != (info.CRC, info.compress_size, info.file_size):
        raise _Rejected("ZIP_LOCAL_HEADER_MISMATCH")
    start = offset + 30 + header[9] + header[10]
    stop = start + info.compress_size
    boundary = getattr(info, "_end_offset", None) or archive.start_dir
    if not start <= stop <= min(len(raw), archive.start_dir, boundary):
        raise _Rejected("ZIP_MEMBER_BOUNDARY_MISMATCH")
    name = raw[offset + 30:offset + 30 + header[9]].decode("utf-8" if info.flag_bits & 0x800 else "cp437")
    if name != info.orig_filename:
        raise _Rejected("ZIP_LOCAL_HEADER_MISMATCH")
    compressed = raw[start:stop]
    if info.compress_type == zipfile.ZIP_STORED:
        value = compressed
    else:
        decoder = zlib.decompressobj(-zlib.MAX_WBITS)
        value = decoder.decompress(compressed, limit + 1)
        if len(value) > limit or decoder.unconsumed_tail:
            raise _Rejected("ZIP_MEMBER_BYTE_LIMIT")
        if not decoder.eof or decoder.unused_data:
            raise _Rejected("ZIP_DEFLATE_STREAM_MISMATCH")
    if len(value) > limit:
        raise _Rejected("ZIP_MEMBER_BYTE_LIMIT")
    if len(value) != info.file_size or zlib.crc32(value) & 0xffffffff != info.CRC:
        raise _Rejected("ZIP_MEMBER_SIZE_OR_CRC_MISMATCH")
    if len(value) > max(1, info.compress_size) * MAX_COMPRESSION_RATIO:
        raise _Rejected("ZIP_COMPRESSION_BOUND")
    return value


def _xml_tree(raw):
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        members = archive.infolist()
        if len(members) > MAX_MEMBERS:
            raise _Rejected("ZIP_MEMBER_COUNT_LIMIT")
        names = [item.filename.casefold() for item in members]
        if any(item.filename != item.orig_filename or "\x00" in item.orig_filename for item in members):
            raise _Rejected("ZIP_MEMBER_NAME_UNRECOGNIZED")
        if len(set(names)) != len(names):
            raise _Rejected("ZIP_DUPLICATE_MEMBERS")
        if any(item.flag_bits & 1 for item in members):
            raise _Rejected("ZIP_ENCRYPTED_MEMBERS")
        if sum(item.file_size for item in members) > MAX_UNCOMPRESSED_BYTES:
            raise _Rejected("ZIP_UNCOMPRESSED_BYTE_LIMIT")
        if any(item.compress_type not in {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}
               or item.file_size > MAX_UNCOMPRESSED_BYTES
               or item.file_size > max(1, item.compress_size) * MAX_COMPRESSION_RATIO for item in members):
            raise _Rejected("ZIP_COMPRESSION_BOUND")
        by_name = {item.filename: item for item in members}
        if "mimetype" not in by_name or "content.xml" not in by_name:
            raise _Rejected("ODT_REQUIRED_MEMBERS_MISSING")
        if _member_bytes(raw, archive, by_name["mimetype"], 128) != MIMETYPE:
            raise _Rejected("ODT_MIMETYPE_UNRECOGNIZED")
        xml = _member_bytes(raw, archive, by_name["content.xml"], MAX_XML_BYTES)
    try:
        xml_text = xml.decode("utf-8-sig")
    except UnicodeError as error:
        raise _Rejected("XML_ENCODING_UNSUPPORTED") from error
    encoding = re.search(r"<\?xml\b[^?]*\bencoding\s*=\s*['\"]([^'\"]+)['\"]", xml_text, re.I)
    if "\x00" in xml_text or encoding and encoding.group(1).lower() != "utf-8":
        raise _Rejected("XML_ENCODING_UNSUPPORTED")
    if re.search(r"<!\s*(?:DOCTYPE|ENTITY)\b", xml_text, re.I):
        raise _Rejected("XML_DTD_OR_ENTITY_DECLARATION")
    parser = ET.XMLParser(target=_BoundedTreeBuilder())
    for offset in range(0, len(xml), 16384):
        parser.feed(xml[offset:offset + 16384])
    root = parser.close()
    if root.tag != f"{{{OFFICE}}}document-content":
        raise _Rejected("ODT_DOCUMENT_ROOT_UNRECOGNIZED")
    bodies = root.findall(f"{{{OFFICE}}}body/{{{OFFICE}}}text")
    if len(bodies) != 1:
        raise _Rejected("ODT_TEXT_BODY_UNRECOGNIZED")
    return bodies[0]


def _has_hidden_paragraph_marker(node):
    for child in node:
        if child.tag == f"{{{TEXT}}}hidden-paragraph":
            condition = re.sub(r"\s+", "", child.get(f"{{{TEXT}}}condition", "")).casefold()
            # Do not evaluate arbitrary formulas. Explicit false is visible;
            # true or unresolved conditional visibility cannot supply metadata.
            false_values = {"false", "false()", "ooow:false()", "of:false()", "0"}
            hidden_flag = child.get(f"{{{TEXT}}}is-hidden")
            if (hidden_flag is not None and hidden_flag.strip() not in {"false", "0"}
                    or condition and condition not in false_values):
                return True
        elif child.tag not in SKIP_TAGS and _has_hidden_paragraph_marker(child):
            return True
    return False


def _hidden(node):
    hidden_flag = node.get(f"{{{TEXT}}}is-hidden")
    return (node.tag in SKIP_TAGS or node.get(f"{{{TEXT}}}display") in {"none", "condition"}
            or hidden_flag is not None and hidden_flag.strip() not in {"false", "0"}
            or node.tag == f"{{{TABLE}}}table-row" and node.get(f"{{{TABLE}}}visibility") in {"collapse", "filter"}
            or node.tag in {f"{{{TEXT}}}p", f"{{{TEXT}}}h"} and _has_hidden_paragraph_marker(node))


def _flatten(node):
    parts = []
    size = 0

    def add(value):
        nonlocal size
        size += len(value)
        if size > MAX_BLOCK_CHARS:
            raise _Rejected("ODT_TEXT_BLOCK_LIMIT")
        parts.append(value)

    def visit(element):
        if _hidden(element):
            return
        if element.tag == f"{{{TEXT}}}s":
            count = element.get(f"{{{TEXT}}}c", "1")
            if not re.fullmatch(r"[0-9]{1,3}", count) or not 1 <= int(count) <= 128:
                raise _Rejected("ODT_SPACE_EXPANSION_LIMIT")
            add(" " * int(count))
        elif element.tag in {f"{{{TEXT}}}tab", f"{{{TEXT}}}line-break"}:
            add(" ")
        elif element.text:
            add(element.text)
        for child in element:
            visit(child)
            if child.tail:
                add(child.tail)
        if element.tag in {f"{{{TEXT}}}p", f"{{{TEXT}}}h"}:
            add(" ")

    visit(node)
    return "".join(parts)


def _blocks(node):
    if _hidden(node):
        return
    if node.tag in {f"{{{TEXT}}}p", f"{{{TEXT}}}h", f"{{{TABLE}}}table-row"}:
        value = _flatten(node)
        if value.strip():
            yield value
        return
    for child in node:
        yield from _blocks(child)


def parse_odt_meeting_metadata(raw):
    """Return normalized header fields; ambiguous or missing fields stay unknown."""
    if type(raw) is not bytes:
        raise ValueError("bounded ODT bytes required")
    result = {"source_id": "S-019", "parser_contract": CONTRACT, "input_format": "ODT",
              "validation_scope": "LOCAL_SUPPLIED_ODT_HEADER_METADATA_ONLY",
              "source_body_sha256": hashlib.sha256(raw).hexdigest() if len(raw) <= MAX_RAW_BYTES else None,
              "source_body_bytes": len(raw), "status": "NOT_VERIFIED", "reason_codes": [],
              "meeting_number": None, "meeting_date": None,
              "meeting_number_role": "UNKNOWN", "meeting_date_role": "UNKNOWN", "explicit_date_label_roles": [],
              "meeting_registry_identity": "NOT_VERIFIED", "official_version_identifier": None,
              "version_semantics": "CONTENT_BYTES_ONLY_NOT_OFFICIAL_VERSION",
              "text_scope": "FIRST_32_TEXT_FLOW_BLOCKS_BEFORE_AGENDA_NOT_RENDERING",
              "business_scope_completeness": "NOT_VERIFIED", "whole_history_completeness": "UNKNOWN",
              "rights_approved": False, "human_review": None, "promotion_eligible": False,
              "model_transmission_allowed": False, "raw_content_projection": "NONE", "network_requests": 0}
    if len(raw) > MAX_RAW_BYTES:
        result["reason_codes"] = ["RAW_BYTE_LIMIT"]
        return result
    try:
        body = _xml_tree(raw)
        numbers, dates, roles = set(), set(), set()
        invalid_date = date_range = unresolved_date = False
        era_year_mismatch = weekday_conflict = False
        for index, block in enumerate(_blocks(body)):
            if index >= MAX_HEADER_BLOCKS:
                break
            compact = re.sub(r"\s+", "", unicodedata.normalize("NFKC", block))
            if re.match(r"^(?:[一二三四五六七八九十壹貳參肆伍陸柒捌玖拾0-9]+[、.])?(?:報告事項|討論事項|臨時動議|散會)", compact):
                break
            title = TITLE.fullmatch(compact)
            if title:
                number = int(title.group(1) or title.group(2))
                if number:
                    numbers.add(number)
            labeled = DATE_LABEL.fullmatch(compact)
            if not labeled:
                continue
            roles.add(DATE_ROLES[labeled.group(1)])
            value = labeled.group(2)
            if DATE_ROLES[labeled.group(1)] in {"TIME", "MEETING_TIME"} and CLOCK_VALUE.fullmatch(value):
                # Separate clock-only time fields add no calendar evidence.
                # Empty, unresolved, referential or date-bearing values still
                # go through the strict date checks below and can veto a date.
                continue
            date_range |= bool(re.search(r"日(?:至|到|及|、|與|[-~～])(?:[0-9]{1,4}年)?(?:[0-9]{1,2}月)?[0-9]{1,2}日", value))
            matches = list(DATE_TOKEN.finditer(value))
            date_range |= len(matches) > 1
            if not matches or matches[0].start() != 0:
                unresolved_date = True
                continue
            match = matches[0]
            suffix = DATE_SUFFIX.fullmatch(value[match.end():])
            if not suffix:
                unresolved_date = True
                date_range |= bool(re.match(r"[至到~～-]", value[match.end():]))
            for match_index, match in enumerate(matches):
                era, year, month, day, slash_month, slash_day = match.groups()
                # An explicit ROC era cannot be silently reinterpreted as a
                # Gregorian four-digit year. Unprefixed supported 1xx years
                # follow the source's ROC convention; 20xx is Gregorian.
                if era is not None and len(year) != 3:
                    era_year_mismatch = True
                    continue
                try:
                    calendar_date = date(int(year) + (1911 if len(year) == 3 else 0), int(month or slash_month), int(day or slash_day))
                    dates.add(calendar_date.isoformat())
                    if match_index == 0 and suffix and suffix.group(1) is not None:
                        weekday_conflict |= calendar_date.weekday() != WEEKDAYS[suffix.group(1)]
                except ValueError:
                    invalid_date = True
        result["explicit_date_label_roles"] = sorted(roles)
        if len(numbers) == 1:
            result.update(meeting_number=next(iter(numbers)), meeting_number_role="EXPLICIT_BODY_MEETING_SERIAL_NOT_REGISTRY_ID")
        else:
            result["reason_codes"].append("AMBIGUOUS_MEETING_NUMBER" if numbers else "EXPLICIT_MEETING_TITLE_MISSING")
        if len(numbers) == 1 and len(dates) == 1 and not invalid_date and not date_range and not unresolved_date and not era_year_mismatch and not weekday_conflict:
            result.update(meeting_date=next(iter(dates)), meeting_date_role="EXPLICIT_LABELED_BODY_MEETING_DATE")
        else:
            result["reason_codes"].append("ERA_YEAR_MISMATCH" if era_year_mismatch else "MEETING_WEEKDAY_CONFLICT" if weekday_conflict else "INVALID_MEETING_DATE" if invalid_date else "AMBIGUOUS_MEETING_DATE" if len(dates) > 1 or date_range else "EXPLICIT_MEETING_DATE_NOT_VERIFIED")
        result["status"] = "STRUCTURED_MEETING_METADATA_OBSERVED" if not result["reason_codes"] else "PARTIAL_METADATA"
    except _Rejected as error:
        result["reason_codes"] = [str(error)]
    except (zipfile.BadZipFile, zlib.error, struct.error, RuntimeError, OSError, ET.ParseError, UnicodeError, ValueError):
        result["reason_codes"] = ["ODT_STRUCTURE_UNRECOGNIZED"]
    return result
