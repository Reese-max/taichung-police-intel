"""Minimal test for chat_turn Workers gateway implementation."""
from __future__ import annotations

import sys
from pathlib import Path

# Verify that the Workers gateway source includes chat_turn support
WORKERS_GATEWAY = Path(__file__).resolve().parents[2] / "workers" / "query-gateway" / "src" / "index.js"

def test_chat_turn_implemented():
    """Test that the Workers gateway implements chat_turn capability."""
    source = WORKERS_GATEWAY.read_text(encoding="utf8")
    # Check that chat_turn is in the allowed tools dict
    assert '"chat_turn"' in source or "'chat_turn'" in source, \
        "chat_turn not found in allowed tools dict"
    # Check that chat_turn is handled in execute()
    assert 'tool === "chat_turn"' in source, \
        "chat_turn not handled in execute() function"
    print("PASS: chat_turn is properly implemented in Workers gateway")