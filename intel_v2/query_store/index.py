from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any

from intel_v2.query_store.trust import TrustTier


@dataclass(frozen=True)
class IndexEntry:
    canonical_id: str
    publication_hash: str
    source_locator: str
    source_id: str
    title: str
    region: str
    agency: str
    category: str
    status: str
    trust_tier: TrustTier
    evidence_ids: list[str]
    detected_at: str
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["trust_tier"] = self.trust_tier.value
        d.pop("extra", None)
        return {**d, **self.extra}


class GenerationStatus(str, Enum):
    ACTIVE = "active"
    STALE = "stale"
    DEGRADED = "degraded"


@dataclass
class Generation:
    generation_id: str
    publication_hash: str
    entries: list[IndexEntry]
    status: GenerationStatus = GenerationStatus.ACTIVE
    error_message: str | None = None

    def mark_stale(self, reason: str) -> None:
        self.status = GenerationStatus.STALE
        self.error_message = reason

    def mark_degraded(self, reason: str) -> None:
        self.status = GenerationStatus.DEGRADED
        self.error_message = reason
