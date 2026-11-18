"""Taipei term/session list slices; never fetch per-record detail pages."""
import re

from .html import Document
from .http import SnapshotAdapter


class TpeAdapter(SnapshotAdapter):
    MAX_SESSIONS = 99

    @staticmethod
    def _options(doc, name):
        return [value for value, _ in doc.options(name) if re.fullmatch(r"\d{2}", value)]

    @staticmethod
    def _records(html, term, session, home):
        doc = Document(html)
        if re.search(r"超過\s*500\s*筆", doc.root.text()):
            raise ValueError(f"TPE_ROW_CAP term={term} session={session}")
        table = doc.one("table", id="MainTable")
        headers = [n.attrs.get("data-field", "") for n in table.find("th")]
        if not {"id", "acno", "case", "text"} <= set(headers):
            raise ValueError("invalid TPE list headers")
        records = []
        for row in table.find("tr"):
            cells = row.find("td")
            if not cells:
                continue
            if len(cells) != len(headers):
                raise ValueError("invalid TPE list row")
            values = dict(zip(headers, (n.text() for n in cells)))
            if not re.fullmatch(r"\d+", values["id"]):
                raise ValueError("invalid TPE SYSN")
            records.append(dict(kind={"議員提案": "bill", "市府提案": "bill_gov"}.get(values["case"], "bill_other"),
                                external_id="tpe:" + values["id"], title=values["text"],
                                case_number=f"第{term}屆第{session}次 案號{values['acno']}",
                                event_date=None, official_url=home))
        if len(records) > 500:
            raise ValueError(f"TPE_ROW_CAP term={term} session={session}")
        return records

    def _snapshot(self, source):
        home, search = source["endpoints"][:2]
        doc = Document(self.read(home))
        terms = self._options(doc, "sele_DETR")
        if not terms:
            raise ValueError("missing TPE terms")
        pages = []
        for term in terms:
            form = doc.form()
            if not form.get("__VIEWSTATE") or not form.get("__EVENTVALIDATION"):
                raise ValueError("TPE_POSTBACK_CHAIN_BROKEN")
            form.update(sele_DETR=term, sele_DETM="", sele_DETP="", ChkOM="on", useCOTE="useCOTE",
                        startdate="", enddate="", councilorSelect="", searchWord="", btnSubmit="查詢")
            doc = Document(self.read(home, form))
            expected = int(doc.one("span", id="OMCount").text().replace(",", ""))
            if expected < 0:
                raise ValueError("invalid TPE OMCount")
            sessions = self._options(doc, "sele_DETM")
            fallback = not sessions
            sessions = sessions or [f"{n:02d}" for n in range(1, self.MAX_SESSIONS + 1)]
            got, empty = 0, 0
            for session in sessions:
                form = dict.fromkeys("HDETP HStartDate HEndDate HCouncilor HKeyWord".split(), "")
                form.update(Type="OM", PeriodSele="COTE", HDETR=term, HDETM=session)
                records = self._records(self.read(search, form), term, session, home)
                pages.append((f"{term}-{session}", records))
                got += len(records)
                empty = empty + 1 if not records else 0
                if fallback and empty == 2:
                    break
            else:
                if fallback:
                    raise ValueError(f"TPE session guard exceeded term={term}")
            if abs(got - expected) > expected * 0.01:
                self.warnings.append(f"TPE_COUNT_MISMATCH term={term} got={got} expected={expected}")
        return pages
