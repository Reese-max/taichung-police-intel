"""Legislative Yuan official open data; ppg URLs are links only."""
from collections import Counter
from hashlib import sha256
import re
from urllib.parse import urlencode, urlsplit, urlunsplit

from national.loader import normalize
from .http import HttpAdapter, text


class LyAdapter(HttpAdapter):
    MAX_TERM = 12
    MAX_PERIOD = 8
    MAX_BYTES = 8 * 1024 * 1024

    def __init__(self, source, *, session=None, full=True):
        super().__init__(source, session=session)
        self._pages = None
        # full=False scans only the latest convened term; older terms are closed and rarely change.
        self.full = full

    def _rows(self, endpoint, term, period):
        key = (term, period)
        if key not in self._cache:
            url = urlunsplit(endpoint._replace(query=urlencode(dict(
                term=term, sessionPeriod=f"{period:02d}", page=1,
                limit=20000, fileType="json"))))
            rows = self.request(url, max_bytes=self.MAX_BYTES).get("dataList")
            if not isinstance(rows, list):
                raise ValueError("invalid LY dataList")
            self._cache[key] = rows
        return self._cache[key]

    def _snapshot(self, source, full):
        self.warnings = []
        self._cache = {}
        pages, winners = {}, {}
        endpoint = urlsplit(source["endpoints"][0])
        start = source["scope"]["from_term"]
        if not full:
            # Same convened test as the forward scan: period 01 or 02 has rows.
            start = next((term for term in range(self.MAX_TERM, start - 1, -1)
                          if self._rows(endpoint, term, 1) or self._rows(endpoint, term, 2)), start)
        for term in range(start, self.MAX_TERM + 1):
            started, term_warnings = set(), []
            for period in range(1, self.MAX_PERIOD + 1):
                cursor = f"{term}-{period:02d}"
                rows = self._rows(endpoint, term, period)
                if rows:
                    started.add(period)
                elif self.DATASET == 6:
                    term_warnings.append(f"EMPTY_PERIOD term={term} sessionPeriod={period:02d}")
                records, counts = [], Counter()
                for row in rows:
                    if not isinstance(row, dict):
                        raise ValueError("invalid LY row")
                    if (int(text(row, "term")) != term
                            or int(text(row, "sessionPeriod")) != period):
                        raise ValueError("LY row period mismatch")
                    meeting = int(text(row, "sessionTimes"))
                    if meeting < 0:
                        raise ValueError("invalid LY sessionTimes")
                    record = self.record(row, term, period, meeting, counts)
                    if record is None:
                        continue
                    normalize(source, record)
                    if self.DATASET == 20:
                        key = record["external_id"]
                        if key not in winners or meeting >= winners[key][0]:
                            winners[key] = (meeting, cursor, record)
                    else:
                        records.append(record)
                pages[cursor] = records
                # A term exists once period 01 or 02 has rows; later empty periods are official gaps, keep scanning.
                if period == 2 and not started:
                    break
            if not started:
                break
            self.warnings.extend(term_warnings)
        # Deduplicate across the full range before exposing any write page.
        for _, cursor, record in winners.values():
            pages[cursor].append(record)
        return pages

    def fetch(self, source, cursor):
        start = source["scope"]["from_term"]
        if type(start) is not int or not 1 <= start <= self.MAX_TERM:
            raise ValueError("invalid LY from_term")
        if cursor is not None and (not isinstance(cursor, str)
                or not re.fullmatch(r"[0-9]+-0[1-8]", cursor)
                or not start <= int(cursor.split("-")[0]) <= self.MAX_TERM):
            raise ValueError("invalid LY period cursor")
        if self._pages is None or cursor is None:
            # Rebuild on resume to preserve global winners and the empty-period streak.
            self._pages = self._snapshot(source, self.full)
        if cursor is not None and cursor not in self._pages and not self.full:
            # A cursor saved by an interrupted full scan may point at an older term.
            self._pages = self._snapshot(source, True)
        cursor = cursor or next(iter(self._pages))
        if cursor not in self._pages:
            raise ValueError("LY resume period no longer exists")
        keys = list(self._pages)
        index = keys.index(cursor)
        next_cursor = keys[index + 1] if index + 1 < len(keys) else None
        records = self._pages[cursor]
        if next_cursor is None:
            self._pages = None
        return records, next_cursor


class Ly20Adapter(LyAdapter):
    DATASET = 20

    def record(self, row, term, period, meeting, counts):
        official_url = "https://data.ly.gov.tw/getds.action?id=20"
        pdf = text(row, "pdfUrl")
        try:
            parsed = urlsplit(pdf)
            if (parsed.scheme == "https" and parsed.hostname == "ppg.ly.gov.tw"
                    and parsed.port in (None, 443) and parsed.username is None
                    and parsed.password is None and "\\" not in pdf
                    and not any(c.isspace() or ord(c) < 32 for c in pdf)):
                official_url = pdf
        except ValueError:
            pass
        bill = text(row, "billNo").strip()
        return dict(kind="ly_bill", external_id=bill,
                    title=text(row, "billName").strip()[:300],
                    case_number=f"第{term}屆第{period}會期 {bill}",
                    authority=text(row, "billOrg").strip()[:200],
                    official_url=official_url, event_date=None)


class Ly6Adapter(LyAdapter):
    DATASET = 6

    def record(self, row, term, period, meeting, counts):
        item = text(row, "item")
        title = item.strip()
        if len(title) < 20 and title.startswith(("甲、", "乙、")):
            return None
        key = "|".join(text(row, field) for field in
                       ("term", "sessionPeriod", "sessionTimes", "item"))
        external_id = "ly6-" + sha256(key.encode("utf-8")).hexdigest()[:24]
        counts[external_id] += 1
        if counts[external_id] > 1:
            external_id += "-" + str(counts[external_id])
        return dict(kind="ly_interp", external_id=external_id, title=title[:300],
                    case_number=f"第{term}屆第{period}會期第{meeting}次會議",
                    official_url="https://data.ly.gov.tw/getds.action?id=6", event_date=None)
