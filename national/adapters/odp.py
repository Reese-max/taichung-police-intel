"""Stream the official ODP export; never follow dataset resource URLs."""
import csv
import io
import re
import tempfile

from .http import HttpAdapter, text


class OdpCatalogAdapter(HttpAdapter):
    MAX_BYTES = 100 * 1024 * 1024
    REQUIRED_FIELDS = {"資料集識別碼", "資料集名稱", "提供機關", "更新頻率", "授權方式",
                       "資料下載網址", "檔案格式", "詮釋資料更新時間"}
    CITIES = ("臺北市", "新北市", "桃園市", "臺中市", "臺南市", "高雄市")

    def fetch(self, source, cursor):
        if cursor is not None:
            raise ValueError("invalid ODP cursor")
        keywords = source["scope"]["keywords"]
        if not isinstance(keywords, list) or not keywords or any(not isinstance(k, str) or not k for k in keywords):
            raise ValueError("invalid ODP keywords")
        records = []
        with tempfile.TemporaryFile(mode="w+b") as raw:
            response = self.response(source["endpoints"][0], max_bytes=self.MAX_BYTES, output_file=raw)
            response.close()
            with io.TextIOWrapper(raw, encoding="utf-8-sig", newline="") as stream:
                reader = csv.DictReader(stream, strict=True)
                if not self.REQUIRED_FIELDS <= set(reader.fieldnames or []):
                    raise ValueError("missing ODP required columns")
                for row in reader:
                    if None in row or any(v is None for v in row.values()):
                        raise ValueError("invalid ODP CSV row")
                    authority = text(row, "提供機關").strip()
                    city = next((c for c in self.CITIES if authority.replace("台", "臺").startswith(c + "政府")), None)
                    title = text(row, "資料集名稱").strip()
                    if city is None or not any(k in title for k in keywords):
                        continue
                    dataset_id = text(row, "資料集識別碼").strip()
                    if not re.fullmatch(r"[0-9]+", dataset_id):
                        raise ValueError("invalid ODP dataset ID")
                    records.append(dict(kind="dataset", external_id="odp-" + dataset_id,
                                        title=title, authority=authority, jurisdiction=city,
                                        event_date=text(row, "詮釋資料更新時間")[:10] or None,
                                        case_number=text(row, "更新頻率")[:100],
                                        license=text(row, "授權方式").strip() or "未標示（不推定開放）",
                                        official_url="https://data.gov.tw/dataset/" + dataset_id))
        return records, None
