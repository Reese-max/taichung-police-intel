"""Taipei advanced-search snapshots; never fetch per-record detail pages."""
import re

from .html import Document
from .http import SnapshotAdapter


class TpeAdapter(SnapshotAdapter):
    @staticmethod
    def _options(doc, name):
        return [value for value, _ in doc.options(name) if re.fullmatch(r"\d{2}", value)]

    @staticmethod
    def _form(doc):
        forms = doc.find("form", id="form1")
        form = {n.attrs["name"]: n.attrs.get("value", "")
                for f in forms for n in f.find("input")
                if n.attrs.get("type", "").lower() == "hidden"
                and n.attrs.get("name") and "disabled" not in n.attrs}
        if len(forms) != 1 or not form.get("__VIEWSTATE") or not form.get("__EVENTVALIDATION"):
            raise ValueError("TPE_POSTBACK_CHAIN_BROKEN")
        return form

    @staticmethod
    def _records(doc, term, home):
        context = "open" if term == "open" else f"term={term}"
        if re.search(r"超過\s*500\s*筆", doc.root.text()):
            raise ValueError(f"TPE_ROW_CAP {context}")
        labels = doc.find(id="OutputWhereStr")
        count = re.search(r"共\s*([0-9]+)\s*筆", labels[0].text()) if len(labels) == 1 else None
        if count is None:
            raise ValueError(f"TPE_COUNT_MISSING {context}")
        expected = int(count[1])
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
            kind = "".join(values["case"].split())
            status = "未結案" if term == "open" else f"第{term}屆結案"
            records.append(dict(kind={"議員提案": "bill", "市府提案": "bill_gov"}.get(kind, "bill_other"),
                                external_id="tpe:" + values["id"], title=values["text"],
                                case_number=f"{status} 案號{values['acno']}",
                                event_date=None, official_url=home))
        if len(records) != expected:
            raise ValueError(f"TPE_COUNT_MISMATCH {context} got={len(records)} expected={expected}")
        return records

    def _snapshot(self, source):
        home, search = source["endpoints"][:2]
        doc = Document(self.read(search))
        terms = self._options(doc, "sele_DETR")
        if not terms:
            raise ValueError("missing TPE terms")
        form = self._form(doc)
        pages, seen = [], set()
        # Open cases also show up under the current term's 結案屆次 list; query them first so they keep 未結案.
        for term in ["open", *terms]:
            if term == "open":
                form.update(useunFi="on", isFini="U", NotZ99All="NotZ99All", btnSubmit="查詢")
            else:
                form.update(useisFi="on", isFini="F", useCOTE="useCOTE", sele_DETR=term, btnSubmit="查詢")
            doc = Document(self.read(search, form))
            form = self._form(doc)
            records = self._records(doc, term, home)
            unique = []
            for record in records:
                sysn = record["external_id"].removeprefix("tpe:")
                if sysn in seen:
                    self.warnings.append(f"TPE_DUPLICATE_SYSN {sysn}")
                else:
                    seen.add(sysn)
                    unique.append(record)
            pages.append((term, unique))
        return pages
