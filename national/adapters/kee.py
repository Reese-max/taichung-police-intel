"""Keelung motion metadata; the API supplies neither IDs nor event dates."""
from collections import Counter
from hashlib import sha256

from .http import HttpAdapter, text


class KeeAdapter(HttpAdapter):
    MAX_BYTES = 8 * 1024 * 1024

    def fetch(self, source, cursor):
        if cursor is not None:
            raise ValueError("KEE single-response cursor must be null")
        self.warnings = []
        payload = self.request(source["endpoints"][0], max_bytes=self.MAX_BYTES,
                               method="POST", json_body={})
        rows = payload.get("data")
        if not isinstance(rows, list):
            raise ValueError("invalid KEE API rows")
        if len(rows) >= 4990:
            self.warnings.append("POSSIBLE_ROW_CAP")
        records, counts = [], Counter()
        for row in rows:
            if not isinstance(row, dict):
                raise ValueError("invalid KEE row")
            key = "|".join(text(row, "sm1_" + field) for field in ("expkd", "seqkd", "seqno", "3c", "1c"))
            external_id = "kmc-" + sha256(key.encode("utf-8")).hexdigest()[:24]
            # No official ID: preserve duplicate rows with occurrence-order suffixes.
            counts[external_id] += 1
            if counts[external_id] > 1:
                external_id += "-" + str(counts[external_id])
            # Live data has rows with null 案由 (e.g. term 19 city-government motions).
            title = text(row, "sm1_3c").strip() or f"{text(row, 'sm1_1c').strip()}提案（官方未提供案由）"
            records.append(dict(kind="bill", external_id=external_id,
                                title=title[:300],
                                case_number=text(row, "sm1_title"), event_date=None,
                                official_url="https://www.kmc.gov.tw/kmcweb/#/motion"))
        return records, None
