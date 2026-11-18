import json
from pathlib import Path


class FixtureAdapter:
    def __init__(self, path, *, page_size=100):
        self.records = json.loads(Path(path).read_text(encoding="utf-8"))
        if not isinstance(self.records, list) or type(page_size) is not int or page_size < 1:
            raise ValueError("fixture must be a JSON array; page_size must be positive")
        self.page_size = page_size

    def fetch(self, source, cursor):
        offset = 0 if cursor is None else cursor
        if type(offset) is not int or not 0 <= offset <= len(self.records):
            raise ValueError("invalid fixture cursor")
        end = min(offset + self.page_size, len(self.records))
        return self.records[offset:end], end if end < len(self.records) else None
