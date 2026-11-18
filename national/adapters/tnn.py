"""Tainan list metadata, with an independent one-row count probe per term."""
from datetime import datetime
import json
from urllib.parse import urlencode
from uuid import UUID

from .http import SnapshotAdapter, text


class TnnAdapter(SnapshotAdapter):
    PAGE_SIZE = 1500
    MAX_PAGES = 100

    def _rows(self, url):
        rows = json.loads(self.read(url))
        if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
            raise ValueError("invalid TNN list")
        return rows

    def _snapshot(self, source):
        endpoint, grades_url = source["endpoints"][:2]
        grades = [text(row, "GrdNo") for row in self._rows(grades_url)]
        if not grades or len(grades) > 100 or any(not g.isdecimal() for g in grades):
            raise ValueError("invalid TNN grades")
        pages = []
        for grade in grades:
            def query(page, size):
                params = dict.fromkeys("mgsno timno kidno busno ptypno casenumber orgtitle orgtitle2 casecause keyword".split(), "")
                params.update(topage=page, pagerow=size, grdno=grade)
                return self._rows(endpoint + "?" + urlencode(params))

            probe = query(1, 1)
            if len(probe) > 1:
                raise ValueError("invalid TNN count probe")
            expected = int(text(probe[0], "PagesAll")) if probe else 0
            if not 0 <= expected <= self.PAGE_SIZE * self.MAX_PAGES:
                raise ValueError("TNN page guard exceeded")
            total_pages = (expected + self.PAGE_SIZE - 1) // self.PAGE_SIZE
            count = 0
            for page in range(1, total_pages + 1):
                rows = query(page, self.PAGE_SIZE)
                if len(rows) != min(self.PAGE_SIZE, expected - count):
                    raise ValueError(f"TNN_COUNT_MISMATCH term={grade} page={page}")
                records = []
                for row in rows:
                    if int(text(row, "PagesAll")) != total_pages:
                        raise ValueError("TNN_COUNT_MISMATCH PagesAll")
                    ident = text(row, "ProNo")
                    UUID(ident)
                    kid = text(row, "KidTitle")
                    done = text(row, "DoneDate").strip()
                    records.append(dict(
                        kind={"市府提案": "bill_gov", "議員提案": "bill"}.get(kid.split("-")[0], "bill_other"),
                        external_id="tnn:" + ident, title=text(row, "CaseCause")[:300],
                        case_number=f"{text(row, 'GradeTime')} {kid} 第{text(row, 'CaseNumber')}案",
                        authority=text(row, "OrgTitle")[:200],
                        event_date=datetime.strptime(done, "%Y/%m/%d").date().isoformat() if done else None,
                        official_url="https://www.tncc.gov.tw/motionpage1.asp?" + urlencode({"ProNo": ident})))
                count += len(records)
                pages.append((f"{grade}-{page}", records))
            if count != expected:
                raise ValueError(f"TNN_COUNT_MISMATCH term={grade} got={count} expected={expected}")
        return pages
