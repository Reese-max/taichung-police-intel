"""Kaohsiung list metadata; preserve the latest WebForms state on every POST."""
import re
from urllib.parse import parse_qs, urlencode, urljoin, urlsplit

from .html import Document
from .http import SnapshotAdapter

PREFIX = "uscPeriodSessionMeeting$"


class KhhAdapter(SnapshotAdapter):
    PAGE_SIZE = 500
    MAX_PAGES = 100

    @staticmethod
    def _state(doc):
        try:
            form = doc.form()
        except ValueError as error:
            raise ValueError("KHH_POSTBACK_CHAIN_BROKEN") from error
        if not form.get("__VIEWSTATE") or not form.get("__EVENTVALIDATION"):
            raise ValueError("KHH_POSTBACK_CHAIN_BROKEN")
        return form

    def _post(self, url, doc, **updates):
        form = self._state(doc)
        form.update(__EVENTTARGET="", __EVENTARGUMENT="")
        form.update(updates)
        result = Document(self.read(url, form))
        self._state(result)
        return result

    @staticmethod
    def _meetings(doc):
        meetings = {}
        for row in doc.find("tr"):
            cells = row.find("td")
            if not cells or cells[0].attrs.get("data-title") != "會期":
                continue
            counts = [int(c.text().replace(",", "")) for c in cells[1:]]
            if len(counts) < 9:
                raise ValueError("invalid KHH statistics columns")
            meetings[cells[0].text()] = sum(counts)
        if not meetings:
            raise ValueError("missing KHH statistics meetings")
        return meetings

    @staticmethod
    def _counts(doc):
        counts = {}
        for n in range(1, 11):
            fields = doc.find("input", name=f"hidCount0{n}")
            if not fields and n == 10:
                continue
            if len(fields) != 1:
                raise ValueError("missing KHH hidCount")
            value = int(fields[0].attrs.get("value", ""))
            if not 0 <= value <= KhhAdapter.PAGE_SIZE * KhhAdapter.MAX_PAGES:
                raise ValueError("KHH page guard exceeded")
            counts[n] = value
        return counts

    @staticmethod
    def _records(doc, tab, entry):
        name = doc.one("li", id=f"li0{tab}").text().split("(")[0].strip()
        kind = {"市政府提案": "bill_gov", "議員提案": "bill"}.get(name, "bill_other")
        table = doc.one("table", id="gvIndex" + (str(tab) if tab != 1 else ""))
        records, group = [], ""
        for row in table.find("tr"):
            cells = row.find("td")
            if not cells:
                continue
            if "special-title" in row.attrs.get("class", "").split():
                group = row.text()
                continue
            sn = [n.attrs.get("value", "") for n in row.find("input")
                  if n.attrs.get("name", "").endswith("$hidProposalSN")]
            if len(sn) != 1 or not sn[0].isascii() or not sn[0].isdigit() or len(cells) != 7 or not group:
                raise ValueError("invalid KHH proposal row")
            detail = re.search(r"Detail_RWD\.aspx\?s=([0-9A-Fa-f]{16})&ct=([0-9A-Fa-f]+)",
                               cells[4].attrs.get("onclick", ""))
            records.append(dict(kind=kind, external_id="khh:" + sn[0], title=cells[4].text()[:300],
                                case_number=f"{group} 第{cells[1].text()}號", authority=cells[3].text()[:200],
                                event_date=None, official_url=urljoin(entry, detail[0]) if detail else entry))
        return records

    def _snapshot(self, source):
        entry, stats_url = source["endpoints"][:2]
        stats = Document(self.read(stats_url))
        terms = []
        for link in stats.find("a"):
            href = link.attrs.get("href", "")
            if urlsplit(href).path.endswith("/Statistics.aspx"):
                term = parse_qs(urlsplit(href).query).get("session_code", [""])[0]
                if re.fullmatch(r"\d{4}", term) and term not in terms:
                    terms.append(term)
        if not terms or len(terms) > 100:
            raise ValueError("missing KHH terms")
        pages = []
        for term in terms:
            meetings = self._meetings(Document(self.read(stats_url + "?" + urlencode({"session_code": term}))))
            doc = Document(self.read(entry))
            doc = self._post(entry, doc, **{"__EVENTTARGET": PREFIX + "ddlSession",
                                          PREFIX + "ddlPeriod": term[:2], PREFIX + "ddlSession": term})
            options = doc.options(PREFIX + "ddlMeeting")
            if set(name for name, count in meetings.items() if count) - {name for _, name in options}:
                raise ValueError("KHH statistics/meeting options mismatch")
            for meeting, name in options:
                if not meetings.get(name):
                    continue
                if not re.fullmatch(r"\d{8}", meeting) or not meeting.startswith(term):
                    raise ValueError("invalid KHH meeting code")
                doc = self._post(entry, doc, **{"__EVENTTARGET": "LinkButton1", PREFIX + "ddlMeeting": meeting,
                                              "ddlProposalKind": "0"})
                counts = self._counts(doc)
                records = []
                for tab, expected in counts.items():
                    if not expected:
                        continue
                    found = []
                    for page in range(1, (expected + self.PAGE_SIZE - 1) // self.PAGE_SIZE + 1):
                        doc = self._post(entry, doc, **{
                            "btnGo" + (str(tab) if tab != 1 else ""): " Go ", "hidCurrentTab": str(tab),
                            f"DataPager{tab}$ctl02$txtCurrentPage": str(page),
                            f"DataPager{tab}$ctl02$txtPageSize": str(self.PAGE_SIZE)})
                        if self._counts(doc) != counts:
                            raise ValueError("KHH_COUNT_MISMATCH changed hidCount")
                        found.extend(self._records(doc, tab, entry))
                    if len(found) != expected or len({r['external_id'] for r in found}) != expected:
                        raise ValueError(f"KHH_COUNT_MISMATCH meeting={meeting} tab={tab} got={len(found)} expected={expected}")
                    records.extend(found)
                pages.append((meeting, records))
        return pages
