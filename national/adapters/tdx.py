"""TDX six-city live events; retain public metadata only."""
import json
import os

from national.loader import normalize
from .http import HttpAdapter, text


class TdxAdapter(HttpAdapter):
    CITIES = dict(zip(
        ("Taipei", "NewTaipei", "Taoyuan", "Taichung", "Tainan", "Kaohsiung"),
        ("臺北市", "新北市", "桃園市", "臺中市", "臺南市", "高雄市")))
    EVENT_TYPES = dict(enumerate(("事故", "施工", "壅塞", "特殊管制", "天氣", "災害", "活動", "其它"), 1))
    MAX_BYTES = 8 * 1024 * 1024

    def __init__(self, source, *, session=None):
        super().__init__(source, session=session)
        self.begin_run()

    @staticmethod
    def configured():
        return all(os.environ.get(key, "").strip() for key in ("TDX_CLIENT_ID", "TDX_CLIENT_SECRET"))

    def begin_run(self):
        self._token = None

    def _authenticate(self):
        try:
            response = self.response(self.source["endpoints"][0], max_bytes=64 * 1024,
                                     method="POST", form_body={
                                         "grant_type": "client_credentials",
                                         "client_id": os.environ["TDX_CLIENT_ID"],
                                         "client_secret": os.environ["TDX_CLIENT_SECRET"]})
            try:
                token = json.loads(response.content).get("access_token")
                if not isinstance(token, str) or not token.strip() or any(c.isspace() for c in token):
                    raise ValueError("invalid access_token")
                self._token = token
            finally:
                response.close()
        except Exception:
            # Transport exceptions may include reflected credentials or response URLs.
            raise RuntimeError("TDX token request failed or invalid access_token") from None

    def fetch(self, source, cursor):
        if not self.configured():
            raise ValueError("MISSING_SECRET")
        cities = list(self.CITIES)
        if cursor is not None and cursor not in cities:
            raise ValueError("invalid TDX city cursor")
        index = cities.index(cursor) if cursor is not None else 0
        city = cities[index]
        if self._token is None:
            self._authenticate()
        try:
            url = source["endpoints"][1].replace("{City}", city)
            response = self.response(url, max_bytes=self.MAX_BYTES, bearer_token=self._token)
            try:
                payload = json.loads(response.content)
            finally:
                response.close()
            items, count = payload.get("Items"), payload.get("Count")
            if not isinstance(items, list) or type(count) is not int or count != len(items) or count > 1000:
                raise ValueError("invalid TDX Count or Items")
            records = []
            for row in items:
                event_id = text(row, "EventID").strip()
                event_type = row.get("EventType")
                if not event_id or type(event_type) is not int:
                    raise ValueError("invalid TDX EventID or EventType")
                event_date = text(row, "EffectiveTime") or text(row, "PublishTime")
                record = dict(kind="traffic_event", external_id=f"tdx-{city}-{event_id}",
                              title=text(row, "EventTitle").strip()[:300],
                              case_number=self.EVENT_TYPES.get(event_type, f"類型{event_type}"),
                              event_date=event_date[:10] or None,
                              authority=" ".join(filter(None, (text(payload, "AuthorityCode"), text(row, "Source")))),
                              jurisdiction=self.CITIES[city], official_url="https://tdx.transportdata.tw/")
                normalize(source, record)
                records.append(record)
            if len({r["external_id"] for r in records}) != len(records):
                raise ValueError("duplicate TDX EventID")
        except Exception:
            self._token = None
            raise RuntimeError("TDX city request failed or invalid event response (Count/Items/fields)") from None
        next_cursor = cities[index + 1] if index + 1 < len(cities) else None
        if next_cursor is None:
            self._token = None
        return records, next_cursor

    def close(self):
        self._token = None
        super().close()
