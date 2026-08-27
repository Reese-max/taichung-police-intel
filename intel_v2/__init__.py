"""Deterministic V2 intelligence semantics.

The package is intentionally side-effect free. Production collectors may import
it, but the current public demo remains unchanged until shadow-mode acceptance
criteria are met.
"""

from .semantics import (
    ChangeEvent,
    ItemVersion,
    compare_snapshot,
    ensure_slot_is_due,
    feed_item_to_version,
    shadow_brief,
)

__all__ = [
    "ChangeEvent",
    "ItemVersion",
    "compare_snapshot",
    "ensure_slot_is_due",
    "feed_item_to_version",
    "shadow_brief",
]
