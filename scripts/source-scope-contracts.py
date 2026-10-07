"""Offline source contracts; public metadata only, never fetch or grant rights."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import tempfile
from datetime import date
from pathlib import Path
from urllib.parse import parse_qsl, unquote, urljoin, urlsplit

from bs4 import BeautifulSoup

MAX_BYTES = 2 * 1024 * 1024
HOSTS = {"S-026": "law.taichung.gov.tw", "S-010": "vod.tccc.gov.tw",
         "S-011": "agenda-vod.tccc.gov.tw"}
DATE_TOKEN = re.compile(r"(?<!\d)(20\d{2}|1\d{2})[./-](\d{1,2})[./-](\d{1,2})(?!\d)")
KNOWN_CURRENT_EMPTY_MARKERS = {"e91ee4ba0dd2e616e280d9ed6e8cce8eca350c12066360c6ce89cb3bacd4578e"}


def _query(url):
    values = {}
    for key, value in parse_qsl(urlsplit(url).query, keep_blank_values=True):
        key = key.lower()
        if key in values:
            raise ValueError("duplicate URL query key")
        values[key] = value
    return values


def _url(source_id, url, path):
    parsed = urlsplit(url)
    if (parsed.scheme != "https" or parsed.hostname != HOSTS[source_id]
            or parsed.username or parsed.password or parsed.port not in (None, 443)
            or parsed.fragment or parsed.path.lower() != path.lower()):
        raise ValueError("URL does not match the source contract")
    return _query(url)


def _base(source_id, kind, raw, official_url, http_status, final_url):
    if source_id not in HOSTS or not isinstance(raw, bytes) or len(raw) > MAX_BYTES:
        raise ValueError("unsupported source or bounded input required")
    # Rejected caller URLs can contain credentials or private search values.
    # Only bounded public source selectors are echoed in the metadata projection.
    safe_url = None
    try:
        parsed = urlsplit(official_url)
        query = _query(official_url)
        allowed_paths = ({"/draftforum.aspx"} if kind == "CURRENT_DRAFT_SNAPSHOT" else
                         {"/draftopinion.aspx"} if source_id == "S-026" else
                         {"/wb_news02.asp"} if source_id == "S-010" else {"/v2_news02.asp"})
        if (parsed.scheme == "https" and parsed.hostname == HOSTS[source_id]
                and not parsed.username and not parsed.password and parsed.port in (None, 443)
                and parsed.path.lower() in allowed_paths and not parsed.fragment and all(
                    (key in {"id", "fileid", "ano", "url", "pageno"} and re.fullmatch(r"[1-9]\d*", value))
                    or (key == "type" and value in {"H", "ANN"}) for key, value in query.items())):
            safe_url = official_url
    except ValueError:
        pass
    result = {"schema_version": 1, "source_id": source_id, "kind": kind,
              "official_url": safe_url, "source_body_sha256": hashlib.sha256(raw).hexdigest(),
              "validation_scope": "LOCAL_SUPPLIED_HTTP_METADATA_CONTRACT_ONLY",
              "http_metadata_asserted_by_caller": True,
              "status": "NOT_VERIFIED", "reason_codes": [],
              "business_scope_completeness": "NOT_VERIFIED", "requested_window_count": None,
              "publication_date": "UNKNOWN", "version_semantics": "NOT_VERIFIED",
              "human_review": None, "rights_review": "PENDING",
              "model_transmission_allowed": False, "promotion_eligible": False,
              "raw_content_projection": "NONE", "network_requests": 0}
    if type(http_status) is not int or http_status != 200:
        result["reason_codes"].append("HTTP_STATUS_NOT_200")
    if final_url is not None and final_url != official_url:
        result["reason_codes"].append("FINAL_URL_DIFFERS_FROM_REQUESTED_URL")
    return result


def _visible(node):
    for element in [node, *node.parents]:
        if getattr(element, "name", None) in {"script", "style", "template", "head", "noscript"}:
            return False
        if not getattr(element, "attrs", None):
            continue
        style = re.sub(r"\s+", "", element.get("style", "").lower())
        if (element.has_attr("hidden") or str(element.get("aria-hidden", "")).lower() == "true"
                or "display:none" in style or "visibility:hidden" in style
                or set(element.get("class", [])) & {"hidden", "d-none", "hide"}
                or ("tab-pane" in element.get("class", []) and "active" not in element.get("class", []))):
            return False
    return True


def _visible_text(node):
    """Outer visibility does not make hidden descendant text renderable."""
    return " ".join(str(value).strip() for value in node.strings
                    if value.parent is not None and _visible(value.parent) and str(value).strip())


def _dates(text):
    values = []
    for year, month, day in DATE_TOKEN.findall(text):
        try:
            y = int(year) + (1911 if len(year) == 3 else 0)
            values.append(date(y, int(month), int(day)).isoformat())
        except ValueError:
            return None
    return values


def read_bounded_regular_file(path):
    """Do not block on FIFOs/devices or read more than the fixed input bound."""
    descriptor = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_BYTES:
            raise ValueError("bounded regular input file required")
        with os.fdopen(descriptor, "rb", closefd=False) as handle:
            raw = handle.read(MAX_BYTES + 1)
        if len(raw) > MAX_BYTES:
            raise ValueError("input byte budget exceeded")
        return raw
    finally:
        os.close(descriptor)


def reject_output_input_alias(input_path, output_path):
    """The metadata destination must not replace any path to the raw evidence."""
    input_path, output_path = Path(input_path), Path(output_path)
    if output_path.resolve() == input_path.resolve():
        raise ValueError("metadata output aliases the original input")
    try:
        if output_path.samefile(input_path):
            raise ValueError("metadata output aliases the original input inode")
    except FileNotFoundError:
        # A fresh destination is allowed; missing input still fails the reader.
        pass


def write_metadata_atomic(output_path, text, input_path):
    """Private fresh inode, atomic replace, and no fixed temporary symlink path."""
    output_path = Path(output_path)
    reject_output_input_alias(input_path, output_path)
    descriptor, temporary = tempfile.mkstemp(prefix="." + output_path.name + "-", dir=output_path.parent)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            descriptor = None
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        # Recheck aliases before commit, without following the destination inode.
        reject_output_input_alias(input_path, output_path)
        os.replace(temporary, output_path)
    finally:
        if descriptor is not None:
            os.close(descriptor)
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def parse_current_drafts(raw, official_url, *, http_status=200, final_url=None):
    """Recognize only the publisher's visible, unfiltered current empty state."""
    result = _base("S-026", "CURRENT_DRAFT_SNAPSHOT", raw, official_url, http_status, final_url)
    result["current_snapshot_count"] = None
    try:
        if _url("S-026", official_url, "/DraftForum.aspx"):
            raise ValueError("current empty contract requires the unfiltered default URL")
    except ValueError:
        result["reason_codes"].append("REQUEST_IS_NOT_UNFILTERED_CURRENT_INDEX")
        return result
    if result["reason_codes"]:
        return result
    soup = BeautifulSoup(raw, "html.parser")
    for node in soup(["script", "style"]):
        node.decompose()
    text = _visible_text(soup).lower()
    if any(word in text for word in ["service unavailable", "server error in", "internal server error", "403 forbidden",
                                    "access denied", "permission denied", "login required", "sign in to continue", "請先登入", "驗證失敗",
                                    "系統維護", "系統錯誤", "暫停服務", "服務暫停", "存取遭拒"]):
        result["reason_codes"].append("SERVER_ERROR_MARKER")
        return result
    if any(_visible(node) for node in soup.select('input[type="password"]')):
        result["reason_codes"].append("VISIBLE_LOGIN_FORM")
        return result
    forms = soup.select("form#aspnetForm")
    tabs = soup.select("#new.tab-pane.active")
    markers = soup.select("#new > #ctl00_cp_content_divNoData")
    if (len(forms) != 1 or len(tabs) != 1 or len(soup.select(".tab-pane.active")) != 1
            or len(markers) != 1 or not _visible(tabs[0]) or not _visible(markers[0])):
        result["reason_codes"].append("VISIBLE_CURRENT_EMPTY_STRUCTURE_NOT_RECOGNIZED")
        return result
    try:
        if not forms[0].get("action"):
            raise ValueError("current form action missing")
        form_url = urljoin(official_url, forms[0]["action"])
        if _url("S-026", form_url, "/DraftForum.aspx") or forms[0] not in tabs[0].parents:
            raise ValueError("current form contract mismatch")
    except ValueError:
        result["reason_codes"].append("CURRENT_FORM_CONTRACT_MISMATCH")
        return result
    for control in forms[0].select("input[name],select[name]"):
        if control.get("name", "").split("$")[-1].lower() == "type":
            result["reason_codes"].append("CURRENT_TYPE_CONTROL_NOT_SUPPORTED")
            return result
    for link in soup.select(".nav .active a[href], .nav-tabs .active a[href], .nav a.active[href], .nav-tabs a.active[href]"):
        if not _visible(link):
            continue
        target = urljoin(official_url, link["href"])
        if urlsplit(target).path.lower() == "/draftforum.aspx":
            try:
                if _url("S-026", target, "/DraftForum.aspx"):
                    raise ValueError("active tab has a filtered or history selector")
            except ValueError:
                result["reason_codes"].append("ACTIVE_NAVIGATION_CONTRADICTS_CURRENT_INDEX")
                return result
    marker = "".join(_visible_text(markers[0]).split())
    result["empty_state_evidence"] = {
        "selector": "#new > #ctl00_cp_content_divNoData",
        "marker_sha256": hashlib.sha256(marker.encode()).hexdigest(),
        "marker_text_length": len(marker), "marker_text_projection": "NONE",
        "visibility_scope": "STATIC_HTML_RULES_ONLY_NOT_BROWSER_RENDERING"}
    marker_recognized = (hashlib.sha256(marker.encode()).hexdigest() in KNOWN_CURRENT_EMPTY_MARKERS
                         or re.fullmatch(r"尚無預告中(?:的|之)?(?:法規)?草案(?:資料)?[！!。.]?", marker))
    if not marker_recognized:
        result["reason_codes"].append("EMPTY_SEMANTICS_NOT_RECOGNIZED")
    elif any(word in marker for word in ["失敗", "錯誤", "維護", "無法", "稍後"]):
        result["reason_codes"].append("ERROR_IN_EMPTY_STATE_MARKER")
    elif soup.select("#tableList") or any(
            _visible(link) and urlsplit(urljoin(official_url, link["href"])).path.lower() == "/draftopinion.aspx"
            for link in soup.select("a[href]")):
        result["reason_codes"].append("EMPTY_MARKER_CONTRADICTS_LIST_CONTENT")
    else:
        result["status"] = "PUBLISHER_DECLARED_EMPTY_CURRENT_SNAPSHOT"
        result["current_snapshot_count"] = 0
    return result


def parse_draft_detail(raw, official_url, *, http_status=200, final_url=None):
    result = _base("S-026", "DRAFT_DETAIL_METADATA", raw, official_url, http_status, final_url)
    try:
        query = _url("S-026", official_url, "/DraftOpinion.aspx")
        draft_id = query.get("id", "")
        if (not re.fullmatch(r"[1-9]\d*", draft_id) or set(query) - {"id", "type"}
                or ("type" in query and query["type"] != "H")):
            raise ValueError("one positive draft ID required")
    except ValueError:
        result["reason_codes"].append("DRAFT_DETAIL_IDENTITY_NOT_RECOGNIZED")
        return result
    result["draft_id"] = draft_id
    if result["reason_codes"]:
        return result
    soup = BeautifulSoup(raw, "html.parser")
    fields = {"公告日期": [], "預告日期": []}
    for row in soup.find_all("tr"):
        cells = row.find_all(["th", "td"], recursive=False)
        if len(cells) < 2 or not _visible(row) or not all(_visible(cell) for cell in cells):
            continue
        label = "".join(_visible_text(cells[0]).split()).rstrip("：:")
        if label in fields:
            fields[label].append(_dates(" ".join(_visible_text(c) for c in cells[1:])))
    announced = fields["公告日期"]
    notice = fields["預告日期"]
    result["announcement_date"] = announced[0][0] if len(announced) == 1 and announced[0] and len(announced[0]) == 1 else None
    result["pre_notice_interval"] = {"start": None, "end": None, "date_role": "OFFICIAL_PRE_NOTICE_DATE_LABEL"}
    if len(notice) == 1 and notice[0] and len(notice[0]) == 2 and notice[0][0] <= notice[0][1]:
        result["pre_notice_interval"].update({"start": notice[0][0], "end": notice[0][1]})
    result["deadline_semantics"] = "NOT_VERIFIED_PRE_NOTICE_INTERVAL_ONLY"
    attachments = []
    malformed = 0
    for link in soup.select('a[href]'):
        if urlsplit(urljoin(official_url, link["href"].strip())).path.lower() != "/download.ashx":
            continue
        if not _visible(link):
            continue
        target = urljoin(official_url, link["href"].strip())
        try:
            q = _url("S-026", target, "/Download.ashx")
            if (not re.fullmatch(r"[1-9]\d*", q.get("fileid", "")) or q.get("id") != draft_id
                    or q.get("type") != "ANN" or set(q) != {"fileid", "id", "type"}):
                raise ValueError("attachment is not bound to this draft")
            row = {"attachment_id": q["fileid"], "draft_id": draft_id, "attachment_type": "ANN", "official_url": target}
            if row not in attachments:
                attachments.append(row)
        except ValueError:
            malformed += 1
    result["attachments"] = attachments
    result["visible_bound_attachment_count"] = len(attachments)
    result["unrecognized_visible_download_links"] = malformed
    result["attachment_byte_acquisition"] = "NOT_ASSERTED_BY_OFFLINE_PARSER"
    if result["announcement_date"] is None:
        result["reason_codes"].append("ANNOUNCEMENT_DATE_NOT_RECOGNIZED")
    if result["pre_notice_interval"]["start"] is None:
        result["reason_codes"].append("PRE_NOTICE_INTERVAL_NOT_RECOGNIZED")
    if malformed:
        result["reason_codes"].append("ATTACHMENT_IDENTITY_NOT_RECOGNIZED")
    result["status"] = "DETAIL_METADATA_PARSED" if not result["reason_codes"] else "PARTIAL_DETAIL_METADATA"
    return result


def parse_video_detail(source_id, raw, official_url, *, http_status=200, final_url=None):
    if source_id not in {"S-010", "S-011"}:
        raise ValueError("video source required")
    result = _base(source_id, "VIDEO_DETAIL_METADATA", raw, official_url, http_status, final_url)
    path = "/wb_news02.asp" if source_id == "S-010" else "/v2_news02.asp"
    try:
        query = _url(source_id, official_url, path)
        if (query.get("url") != "92" or not re.fullmatch(r"[1-9]\d*", query.get("ano", ""))
                or set(query) - {"url", "ano", "pageno"}
                or ("pageno" in query and not re.fullmatch(r"[1-9]\d*", query["pageno"]))):
            raise ValueError("video detail item identity required")
    except ValueError:
        result["reason_codes"].append("VIDEO_DETAIL_IDENTITY_NOT_RECOGNIZED")
        return result
    result["item_id"] = query["ano"]
    result.update({"meeting_date": None, "meeting_date_role": "UNKNOWN", "playback_verification": "NOT_RUN",
                   "timeline_semantics": "NOT_VERIFIED", "subtitle_or_transcript_acquisition": "NOT_RUN"})
    if result["reason_codes"]:
        return result
    soup = BeautifulSoup(raw, "html.parser")
    dates = []
    for row in soup.select("table.pM tr"):
        cells = row.find_all(["th", "td"], recursive=False)
        if (len(cells) >= 2 and _visible(row) and all(_visible(cell) for cell in cells)
                and "".join(_visible_text(cells[0]).split()).rstrip("：:") == "會議日期"):
            dates.append(_dates(" ".join(_visible_text(c) for c in cells[1:])))
    if len(dates) == 1 and dates[0] and len(dates[0]) == 1:
        result["meeting_date"] = dates[0][0]
        result["meeting_date_role"] = "OFFICIAL_MEETING_DATE_LABEL"
    else:
        result["reason_codes"].append("MEETING_DATE_NOT_RECOGNIZED")
    identifiers = []
    for title in soup.select(".Vtitle, b > font"):
        if not _visible(title):
            continue
        text = "".join(_visible_text(title).split())
        row = {"title_sha256": hashlib.sha256(text.encode()).hexdigest()}
        for role, pattern in [("council_term", r"第(\d+)屆"), ("regular_session", r"第(\d+)次定期會"), ("meeting_number", r"第(\d+)次會議")]:
            values = re.findall(pattern, text)
            if len(values) == 1:
                row[role] = int(values[0])
        if len(row) > 1 and row not in identifiers:
            identifiers.append(row)
    result["official_title_numeric_identifiers"] = identifiers
    embeds = []
    for iframe in soup.select(".video-container iframe[src]"):
        if not _visible(iframe):
            continue
        src = iframe["src"]
        parsed = urlsplit(src)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
            result["reason_codes"].append("PLAYER_EMBED_URL_NOT_RECOGNIZED")
            continue
        identifier = unquote(parsed.path.rpartition("/")[2])
        embeds.append({"embed_url_sha256": hashlib.sha256(src.encode()).hexdigest(),
                       "player_identifier_sha256": hashlib.sha256(identifier.encode()).hexdigest(),
                       "player_host": parsed.hostname, "opaque_player_identifier": None,
                       "identifier_projection": "HASH_ONLY",
                       "requested": False, "identifier_date_semantics": "NOT_INFERRED"})
    result["player_embeds"] = embeds
    result["visible_player_embed_count"] = len(embeds)
    if not embeds:
        result["reason_codes"].append("PLAYER_EMBED_NOT_FOUND")
    result["status"] = "VIDEO_DETAIL_METADATA_PARSED" if not result["reason_codes"] else "PARTIAL_VIDEO_METADATA"
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-id", choices=sorted(HOSTS), required=True)
    parser.add_argument("--kind", choices=["current", "detail"], required=True)
    parser.add_argument("--url", required=True)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--http-status", type=int, default=200)
    parser.add_argument("--final-url")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.output:
        reject_output_input_alias(args.input, args.output)
    raw = read_bounded_regular_file(args.input)
    options = {"http_status": args.http_status, "final_url": args.final_url}
    if args.source_id == "S-026":
        fn = parse_current_drafts if args.kind == "current" else parse_draft_detail
        result = fn(raw, args.url, **options)
    elif args.kind == "detail":
        result = parse_video_detail(args.source_id, raw, args.url, **options)
    else:
        parser.error("video current indexes require separate enumeration evidence")
    text = json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    if args.output:
        write_metadata_atomic(args.output, text, args.input)
    else:
        print(text, end="")
    return 0 if not result["reason_codes"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
