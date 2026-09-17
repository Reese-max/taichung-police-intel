from __future__ import annotations

from dataclasses import dataclass

SCHEMA_VERSION = 1


@dataclass(frozen=True)
class QueryStoreSchema:
    version: int = SCHEMA_VERSION
