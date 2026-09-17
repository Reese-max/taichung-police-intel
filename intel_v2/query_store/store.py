from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from intel_v2.query_store.filters import _matches_filters
from intel_v2.query_store.index import Generation, GenerationStatus, IndexEntry
from intel_v2.query_store.schema import SCHEMA_VERSION, QueryStoreSchema
from intel_v2.query_store.trust import TrustTier

STATIC_PUBLICATION: dict[str, Any] = {
    "schema_version": SCHEMA_VERSION,
    "publication_id": "STATIC-GOVINTEL",
    "items": [],
    "publication_hash": "static-govintel-fallback",
}


@dataclass
class QueryStore:
    _active_generation: Generation | None = None
    _generations: dict[str, Generation] = field(default_factory=dict)
    _write_locked: bool = True

    def _write_entry(self, *, canonical_id: str, data: dict[str, Any]) -> None:
        raise ValueError(
            "Query Store does not accept write truth mutation; only rebuild/swap generation"
        )

    def rebuild(self, canonical_publication: dict[str, Any]) -> Generation:
        if self._active_generation is not None and self._write_locked:
            raise ValueError(
                "Query Store is write-protected: only rebuild/swap generation is allowed"
            )

        publication_hash = canonical_publication.get(
            "publication_hash", self._hash_publication(canonical_publication)
        )
        schema_version = canonical_publication.get("schema_version", SCHEMA_VERSION)
        if schema_version != SCHEMA_VERSION:
            raise ValueError(
                f"unsupported schema version: {schema_version}, expected {SCHEMA_VERSION}"
            )

        items = canonical_publication.get("items", [])
        entries = [self._build_entry(item, publication_hash) for item in items]
        entries.sort(key=lambda e: e.canonical_id)

        generation_id = f"GEN-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:8]}"
        generation = Generation(
            generation_id=generation_id,
            publication_hash=publication_hash,
            entries=entries,
        )
        self._generations[generation_id] = generation
        return generation

    def swap_generation(self, generation: Generation) -> None:
        if self._active_generation is not None:
            self._active_generation.status = GenerationStatus.STALE
        self._active_generation = generation
        generation.status = GenerationStatus.ACTIVE

    def get_active_generation(self) -> Generation | None:
        return self._active_generation

    def query(
        self,
        filters: dict[str, Any] | None = None,
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> dict[str, Any]:
        active = self._active_generation
        if active is None or active.status == GenerationStatus.STALE:
            return self._build_static_results(filters, limit, offset)

        entries_dicts = [e.to_dict() for e in active.entries]
        filtered = [e for e in entries_dicts if _matches_filters(e, filters or {})]
        total = len(filtered)
        page = filtered[offset : offset + limit]
        truncated = offset + limit < total

        return {
            "results": page,
            "total": total,
            "limit": limit,
            "offset": offset,
            "truncated": truncated,
            "truncation_receipt": {
                "total": total,
                "returned": len(page),
                "limit": limit,
                "offset": offset,
                "generation_id": active.generation_id,
                "publication_hash": active.publication_hash,
            },
        }

    def query_or_static(
        self,
        filters: dict[str, Any] | None = None,
        *,
        limit: int = 100,
    ) -> dict[str, Any] | None:
        active = self._active_generation
        if active is None:
            return {
                "results": [],
                "total": 0,
                "limit": limit,
                "offset": 0,
                "truncated": False,
                "fallback": "static_govintel",
                "results_source": "static_fallback",
            }
        return self.query(filters=filters, limit=limit)

    def _build_entry(self, item: dict[str, Any], publication_hash: str) -> IndexEntry:
        trust_raw = item.get("trust_tier", TrustTier.VERIFIED)
        if isinstance(trust_raw, TrustTier):
            trust = trust_raw
        else:
            trust = TrustTier(trust_raw) if trust_raw in (t.value for t in TrustTier) else TrustTier.VERIFIED
        return IndexEntry(
            canonical_id=str(item.get("canonical_id", "")),
            publication_hash=publication_hash,
            source_locator=str(item.get("source_locator", "")),
            source_id=str(item.get("source_id", "")),
            title=str(item.get("title", "")),
            region=str(item.get("region", "")),
            agency=str(item.get("agency", "")),
            category=str(item.get("category", "")),
            status=str(item.get("status", "")),
            trust_tier=trust,
            evidence_ids=list(item.get("evidence_ids", []) or []),
            detected_at=str(item.get("detected_at", "")),
            extra={k: v for k, v in item.items() if k not in _ENTRY_FIELDS},
        )

    def _hash_publication(self, publication: dict[str, Any]) -> str:
        raw = json.dumps(publication, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return hashlib.sha256(raw).hexdigest()

    def _build_static_results(
        self,
        filters: dict[str, Any] | None,
        limit: int,
        offset: int,
    ) -> dict[str, Any]:
        return {
            "results": [],
            "total": 0,
            "limit": limit,
            "offset": offset,
            "truncated": False,
            "fallback": "static_govintel",
            "stale_generation": self._active_generation.generation_id if self._active_generation else None,
        }


_ENTRY_FIELDS = {
    "canonical_id",
    "publication_hash",
    "source_locator",
    "source_id",
    "title",
    "region",
    "agency",
    "category",
    "status",
    "trust_tier",
    "evidence_ids",
    "detected_at",
}
