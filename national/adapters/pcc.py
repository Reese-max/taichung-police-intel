"""PCC list metadata only; never fetch pk detail pages or retain private fields."""
from calendar import monthrange
from datetime import date
from hashlib import sha256
from html import unescape
import json
import re
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, unquote
import xml.etree.ElementTree as ET

import requests

from .html import Document, Node
from .http import SnapshotAdapter, text

ORIGIN = "https://web.pcc.gov.tw"
OPEN_LIST = ORIGIN + "/tps/tp/OpenData/showList"
DOWNLOAD = "/tps/tp/OpenData/downloadFile"
TENDER = "/prkms/tender/common/basic/readTenderBasic"
AWARD = "/prkms/tender/common/bulletion/readBulletion"


def month_start(today, offset=0):
    year, month = divmod(today.year * 12 + today.month - 1 + offset, 12)
    return date(year, month + 1, 1)


def in_scope(source, authority):
    scope = source["scope"]
    return (any(s in authority for s in scope["authority_contains_any"])
            or any(authority.startswith(s) for s in scope["authority_startswith_any"]))


CITIES = ("臺北市 新北市 桃園市 臺中市 臺南市 高雄市 基隆市 新竹市 嘉義市 新竹縣 苗栗縣 彰化縣 "
          "南投縣 雲林縣 嘉義縣 屏東縣 宜蘭縣 花蓮縣 臺東縣 澎湖縣 金門縣 連江縣").split()


def jurisdiction(authority):
    """Local agencies map to their county/city by name prefix; everything else is central (全國)."""
    name = "臺" + authority[1:] if authority.startswith("台") else authority
    return next((city for city in CITIES if name.startswith(city)), "全國")


def iso_date(value, *, roc=False):
    year, month, day = map(int, value.strip().split("/"))
    return date(year + (1911 if roc else 0), month, day).isoformat()


def month_count(source, key):
    count = source["scope"][key]
    if type(count) is not int or not 1 <= count <= 24:
        raise ValueError("PCC months must be between 1 and 24")
    return count


class PccGpaAdapter(SnapshotAdapter):
    MAX_BYTES = 5 * 1024 * 1024
    REQUEST_PATHS = {DOWNLOAD}

    def __init__(self, source, *, session=None, today=None):
        super().__init__(source, session=session)
        self.today = today or date.today()

    def filenames(self, source, kind):
        for half in range(month_count(source, "months") * 2 + 2):
            month = month_start(self.today, -2 - half // 2)
            yield f"{kind}_{month:%Y%m}{2 - half % 2:02d}"

    def parse_xml(self, body, kind):
        if len(body) > self.MAX_BYTES:
            raise ValueError("PCC XML exceeds max_bytes")
        # NUL removal also catches declarations encoded as UTF-16/32 before ET sees them.
        upper = body.replace(b"\x00", b"").upper()
        if b"<!DOCTYPE" in upper or b"<!ENTITY" in upper:
            raise ValueError("PCC XML DOCTYPE/ENTITY forbidden")
        if not body.strip():
            return [], 0
        root = ET.fromstring(body)
        if root.tag != "TENDER_LIST" or any(row.tag != "TENDER" for row in root):
            raise ValueError("invalid PCC XML structure")
        records = {}
        for row in root:
            fields = {child.tag: child.text for child in row}
            authority = text(fields, "TENDER_ORG_NAME").strip()
            if not authority:
                raise ValueError("PCC XML missing authority")
            if not in_scope(self.source, authority):
                continue
            case = text(fields, "TENDER_CASE_NO").strip()
            title = text(fields, "TENDER_NAME").strip()
            event = iso_date(text(fields, "TENDER_SPDT" if kind == "tender" else "AWARD_NOTICE_DATE"))
            if not case or not title:
                raise ValueError("PCC XML missing case/title")
            key = "gpa-" + kind + "-" + sha256(f"{authority}|{case}|{event}".encode()).hexdigest()[:24]
            records[key] = dict(kind=kind, external_id=key, title=title, case_number=case,
                                authority=authority, jurisdiction=jurisdiction(authority),
                                event_date=event, official_url=OPEN_LIST)
        return list(records.values()), len(root)

    def _snapshot(self, source):
        pages, winners = {}, {}
        for kind in ("tender", "award"):
            started, collected, skipped = False, 0, 0
            for name in self.filenames(source, kind):
                try:
                    response = self.response(ORIGIN + DOWNLOAD + "?" + urlencode({"fileName": name + ".xml"}),
                                             max_bytes=self.MAX_BYTES, allow_empty=True)
                except requests.HTTPError as error:
                    if error.response is None or error.response.status_code != 404:
                        raise
                    records, count = [], 0
                else:
                    try:
                        records, count = self.parse_xml(response.content, kind)
                    finally:
                        response.close()
                if not count and not started:
                    if skipped == 2:
                        raise ValueError("PCC latest XML unavailable after two fallbacks")
                    skipped += 1
                    self.warnings.append("PCC_EMPTY_LATEST " + name)
                    continue
                started = True
                if not count:
                    self.warnings.append("PCC_EMPTY_FILE " + name)
                pages[name] = []
                for record in records:
                    winners[record["external_id"]] = name, record
                collected += 1
                if collected == month_count(source, "months") * 2:
                    break
        for name, record in winners.values():
            pages[name].append(record)
        return list(pages.items())


def detail_link(href):
    url = urljoin(ORIGIN, href)
    parsed = urlsplit(url)
    if (parsed.scheme != "https" or parsed.netloc != "web.pcc.gov.tw"
            or parsed.path not in {"/prkms/urlSelector/common/" + p for p in ("tpam", "atm", "nonAtm")}
            or parsed.fragment or "\\" in href
            or not re.fullmatch(r"pk=[A-Za-z0-9%+/=_-]+", parsed.query)):
        raise ValueError("PCC detail link outside allowlist")
    pk = unquote(parsed.query[3:])
    if not re.fullmatch(r"[A-Za-z0-9+/=_-]+", pk):
        raise ValueError("invalid PCC pk")
    return ORIGIN + parsed.path + "?" + urlencode({"pk": pk}), pk


def before_break(node):
    """Case number precedes the first br, including when nested inside an anchor."""
    pieces = []

    def walk(current):
        for child in current.children:
            if isinstance(child, str):
                pieces.append(child)
            elif child.tag == "br":
                return True
            elif child.tag != "script" and walk(child):
                return True
        return False

    walk(node)
    return " ".join(" ".join(pieces).split())


class PccWebAdapter(SnapshotAdapter):
    REQUEST_PATHS = {TENDER, AWARD}
    PAGE_SIZE = 100
    TENDER_TERMS = ("警察", "消防", "交通局", "臺中市政府")
    AWARD_TERMS = ("警察局", "消防局", "交通局", "臺中市政府")

    def __init__(self, source, *, session=None, today=None, backfill=False):
        super().__init__(source, session=session)
        self.today = today or date.today()
        self.months = month_count(source, "months" if backfill else "daily_months")

    def windows(self):
        for offset in range(self.months - 1, -1, -1):
            start = month_start(self.today, -offset)
            yield start, min(self.today, date(start.year, start.month, monthrange(start.year, start.month)[1]))

    def parse_page(self, body, kind):
        doc = Document(body)
        tables = doc.find("table", id="tpam" if kind == "tender" else "bulletion")
        if not tables:
            if "查詢區間較長" in body:
                raise ValueError("PCC query range too long")
            raise ValueError("PCC result table missing")
        banner = doc.one("span", id="pagebanner").text()
        match = re.search(r"共有\s*([\d,]+)\s*筆", banner)
        if not match:
            raise ValueError("PCC total count missing")
        total = int(match[1].replace(",", ""))
        records = []
        for row in tables[0].find("tr"):
            if not {"tb_b2", "tb_b3"} & set(row.attrs.get("class", "").split()):
                continue
            cells = [c for c in row.children if isinstance(c, Node) and c.tag == "td"]
            if len(cells) < 10:
                raise ValueError("PCC row columns missing")
            case_cell = cells[2 if kind == "tender" else 3]
            links = case_cell.find("a")
            if not links:
                raise ValueError("PCC case link missing")
            url, pk = detail_link(links[0].attrs.get("href", ""))
            title = ""
            for link in row.find("a"):
                match = re.match(r"檢視\s+標案(?:名稱|案號)[:：]\s*(.+)", link.attrs.get("title", ""))
                if match:
                    title = unescape(match[1]).strip()
                    break
            if not title:
                for script in case_cell.find("script"):
                    match = re.search(r'pageCode2Img\(\s*("(?:\\.|[^"\\])*")\s*\)', script.text())
                    if match:
                        title = unescape(json.loads(match[1])).strip()
                        break
            if not title:
                raise ValueError("PCC title missing")
            case = before_break(case_cell)
            if "(更正公告)" in case:
                self.warnings.append("PCC_CORRECTION " + pk)
                case = case.replace("(更正公告)", "").strip()
            if not case:
                raise ValueError("PCC case number missing")
            event = cells[6 if kind == "tender" else 5].text()
            if kind == "award":
                match = re.match(r"([0-9]{1,3}/[0-9]{1,2}/[0-9]{1,2})(?=$|\s|\()", event)
                if not match:
                    raise ValueError("PCC award notice date missing or invalid")
                if "無法決標" in event or "無法決標" in cells[1].text():
                    self.warnings.append("PCC_NO_AWARD " + pk)
                event = match[1]
            authority = cells[1 if kind == "tender" else 2].text()
            records.append(dict(kind=kind, external_id="pcc-" + kind + "-" + pk,
                title=title, case_number=case, authority=authority, jurisdiction=jurisdiction(authority),
                event_date=iso_date(event, roc=True), official_url=url))
        return records, total, doc

    def next_award_url(self, doc, url, page):
        current = urlsplit(url)
        for link in doc.one("div", id="displaytagBannerDiv").find("a"):
            target = urlsplit(urljoin(url, link.attrs.get("href", "")))
            pairs = parse_qsl(target.query)
            paging = [(key, value) for key, value in pairs if re.fullmatch(r"d-\d+-p", key)]
            if not paging or paging[0][1] != str(page + 1):
                continue
            if target.scheme != "https" or target.netloc != current.netloc or target.path != AWARD:
                raise ValueError("PCC pagination outside list endpoint")
            # Keep our query/sort/window; only the observed paging parameter comes from HTML.
            query = {k: v for k, v in parse_qsl(current.query) if not re.fullmatch(r"d-\d+-p", k)}
            query[paging[0][0]] = paging[0][1]
            return ORIGIN + AWARD + "?" + urlencode(query)
        raise ValueError("PCC pagination missing before total reached")

    def query(self, kind, params, start, end):
        path = TENDER if kind == "tender" else AWARD
        url = ORIGIN + path + "?" + urlencode(params)
        page, seen, expected, signatures = 1, 0, None, set()
        result = []
        while True:
            records, total, doc = self.parse_page(self.read(url), kind)
            if expected is not None and expected != total:
                raise ValueError("PCC count changed during pagination")
            expected = total
            signature = tuple(r["external_id"] for r in records)
            seen += len(records)
            if (seen > total or not records and seen != total
                    or records and signature in signatures):
                raise ValueError("PCC count mismatch or repeated page")
            signatures.add(signature)
            outside = False
            for record in records:
                event = date.fromisoformat(record["event_date"])
                if kind == "award" and event < start:
                    outside = True
                    break
                if start <= event <= end and in_scope(self.source, record["authority"]):
                    result.append(record)
            if outside or seen == total:
                return result
            if kind == "tender":
                params.update(firstSearch="false", **{"d-49738-p": page + 1})
                url = ORIGIN + path + "?" + urlencode(params)
            else:
                url = self.next_award_url(doc, url, page)
            page += 1

    def _snapshot(self, source):
        pages, winners = {}, {}
        windows = list(self.windows())
        for start, end in windows:
            for term in self.TENDER_TERMS:
                key = f"tender-{start:%Y%m}-{term}"
                params = dict(pageSize=self.PAGE_SIZE, firstSearch="true", searchType="basic",
                    isBinding="N", isLogIn="N", level_1="on", dateType="isDate", orgName=term,
                    tenderStartDate=start.strftime("%Y/%m/%d"), tenderEndDate=end.strftime("%Y/%m/%d"))
                pages[key] = []
                for record in self.query("tender", params, start, end):
                    winners[record["external_id"]] = key, record
        start, end = windows[0][0], self.today
        for year in range(start.year, end.year + 1):
            for term in self.AWARD_TERMS:
                key = f"award-{year}-{term}"
                params = dict(querySentence=term, tenderStatusType="決標", sortCol="AWARD_NOTICE_DATE",
                              timeRange=year - 1911, pageSize=self.PAGE_SIZE)
                pages[key] = []
                for record in self.query("award", params, start, end):
                    winners[record["external_id"]] = key, record
        for key, record in winners.values():
            pages[key].append(record)
        return list(pages.items())
