"""Taichung proposals: validate the whole snapshot before exposing write pages."""
from html.parser import HTMLParser
import re
from urllib.parse import urlencode
from uuid import UUID

from national.loader import normalize
from .http import HttpAdapter, text


class _PlainText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []

    def handle_data(self, data):
        self.parts.append(data)

    def handle_starttag(self, tag, attrs):
        if tag in {"p", "div", "br", "li"}:
            self.parts.append(" ")

    def handle_endtag(self, tag):
        if tag in {"p", "div", "li"}:
            self.parts.append(" ")


def plain_text(value):
    parser = _PlainText()
    parser.feed(value)
    parser.close()
    return " ".join("".join(parser.parts).split())


class TxgAdapter(HttpAdapter):
    PAGE_SIZE = 200
    MAX_PAGES = 100
    MAX_BYTES = 2 * 1024 * 1024

    def __init__(self, source, *, session=None):
        super().__init__(source, session=session)
        self._pages = None

    def _snapshot(self, source):
        pages, ids = [], set()
        page, expected = 1, None
        while True:
            payload = self.request(source["endpoints"][0] + "?" + urlencode(
                {"pageNumber": page, "pageSize": self.PAGE_SIZE}), max_bytes=self.MAX_BYTES)
            data = payload.get("data")
            if payload.get("success") is not True or not isinstance(data, dict):
                raise ValueError("invalid TXG API payload")
            total_pages, total_count = data.get("totalPages"), data.get("totalCount")
            if type(total_pages) is not int or not 0 <= total_pages <= self.MAX_PAGES:
                raise ValueError("TXG page guard exceeded")
            if type(total_count) is not int or not 0 <= total_count <= self.PAGE_SIZE * self.MAX_PAGES:
                raise ValueError("invalid TXG totalCount")
            if expected is None:
                expected = total_pages, total_count
            if (total_pages, total_count) != expected:
                raise ValueError("TXG pagination totals changed during fetch")
            if (type(data.get("pageNumber")) is not int or data["pageNumber"] != page
                    or type(data.get("pageSize")) is not int or data["pageSize"] != self.PAGE_SIZE):
                raise ValueError("TXG page metadata mismatch")
            rows = data.get("data")
            if not isinstance(rows, list) or len(rows) > self.PAGE_SIZE:
                raise ValueError("invalid TXG rows")
            records = []
            for row in rows:
                if not isinstance(row, dict):
                    raise ValueError("invalid TXG row")
                external_id = text(row, "billId")
                UUID(external_id)  # reject missing/unsafe identifiers before composing a URL
                if external_id in ids:
                    raise ValueError("duplicate TXG billId across pages")
                ids.add(external_id)
                title = plain_text(text(row, "proposalRationaleSort"))
                if not title:
                    title = plain_text(re.split(r"\s*關鍵字包含\s*[:：]", text(row, "proposalRationale"), maxsplit=1)[0])
                title = title or (text(row, "billType") + text(row, "billNumber")).strip()
                outcome = "；".join(part for part in (text(row, key).strip() for key in ("closureType", "resolution"))
                                   if part and part != "-") or None
                record = dict(kind="bill", external_id=external_id, title=title[:1000],
                              case_number=f"{text(row, 'session')} {text(row, 'billNumber')}",
                              proposer=text(row, "sponsor").strip() or None,
                              cosigners=text(row, "jointSignatory").strip() or None,
                              committee=text(row, "committee").strip() or None, outcome=outcome,
                              official_url="https://yishi.tccc.gov.tw/proposals/" + external_id,
                              event_date=None)
                normalize(source, record)
                records.append(record)
            pages.append(records)
            if page >= total_pages:
                break
            page += 1
        if len(ids) != expected[1] or (expected[0] == 0 and ids):
            raise ValueError(f"TXG totalCount mismatch: {len(ids)} != {expected[1]}")
        return pages

    def fetch(self, source, cursor):
        page = 1 if cursor is None else cursor
        if type(page) is not int or not 1 <= page <= self.MAX_PAGES:
            raise ValueError("invalid TXG page cursor")
        if self._pages is None or cursor is None:
            # Keep only bounded public metadata; never cache the HTML/full response.
            self._pages = self._snapshot(source)
        if page > len(self._pages):
            raise ValueError("TXG resume page no longer exists")
        records = self._pages[page - 1]
        next_cursor = page + 1 if page < len(self._pages) else None
        if next_cursor is None:
            self._pages = None
        return records, next_cursor
