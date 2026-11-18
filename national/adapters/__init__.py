"""Adapter contract: fetch(source, cursor) returns (records, next_cursor).

Pages are replayable; None starts a scan and a None next_cursor ends it.
No network adapters are registered in W1.
"""
