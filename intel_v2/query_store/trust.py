from __future__ import annotations

from enum import Enum


class TrustTier(str, Enum):
    VERIFIED = "VERIFIED"
    DISCOVERY_UNVERIFIED = "DISCOVERY_UNVERIFIED"
    CONFLICT = "CONFLICT"
    STALE = "STALE"


def classify_trust_tier(
    *,
    source_id: str,
    event_type: str,
    has_evidence: bool,
    has_canonical_id: bool,
    is_discovery: bool = False,
    conflicting: bool = False,
) -> TrustTier:
    if conflicting:
        return TrustTier.CONFLICT
    if is_discovery:
        return TrustTier.DISCOVERY_UNVERIFIED
    if has_canonical_id and has_evidence:
        return TrustTier.VERIFIED
    if has_canonical_id:
        return TrustTier.STALE
    return TrustTier.DISCOVERY_UNVERIFIED
