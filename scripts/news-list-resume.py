#!/usr/bin/env python3
"""Acquire one bounded S032 metadata-only batch without changing admission."""
from __future__ import annotations

import argparse
from datetime import date
import importlib.util
import json
import os
from pathlib import Path
import stat
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from intel_v2.news_list_checkpoint import MAX_CHECKPOINT_BYTES, collect_batch, encode_checkpoint


def read_regular_bounded(path):
    """Reject FIFOs without waiting for a writer, then bound regular-file reads."""
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NONBLOCK", 0))
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise ValueError("checkpoint input must be a regular file")
        if metadata.st_size > MAX_CHECKPOINT_BYTES:
            raise ValueError("checkpoint exceeds 2 MiB")
        with os.fdopen(descriptor, "rb") as stream:
            descriptor = None
            data = stream.read(MAX_CHECKPOINT_BYTES + 1)
        if len(data) > MAX_CHECKPOINT_BYTES:
            raise ValueError("checkpoint exceeds 2 MiB")
        return data
    finally:
        if descriptor is not None:
            os.close(descriptor)


def write_checkpoint_atomic(path, checkpoint):
    """Publish a private inode; do not write through existing links/temp names."""
    encoded = encode_checkpoint(checkpoint)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix="." + path.name + "-", dir=path.parent)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb") as stream:
            descriptor = None
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if descriptor is not None:
            os.close(descriptor)
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-id", choices=["S-032"], default="S-032")
    parser.add_argument("--start", type=date.fromisoformat, required=True)
    parser.add_argument("--end", type=date.fromisoformat, required=True)
    parser.add_argument("--resume-checkpoint", type=Path)
    parser.add_argument("--expected-checkpoint-sha256")
    parser.add_argument("--output-checkpoint", type=Path, required=True)
    parser.add_argument("--list-page-limit", type=int, choices=range(1, 41), default=4)
    parser.add_argument("--http-call-limit", type=int, choices=range(1, 65), default=6)
    args = parser.parse_args()
    if args.http_call_limit < args.list_page_limit:
        parser.error("the explicit HTTP budget must cover the selected list-page budget")
    if bool(args.resume_checkpoint) != bool(args.expected_checkpoint_sha256):
        parser.error("resume requires both a checkpoint and its separately retained expected SHA256")
    if args.resume_checkpoint and args.resume_checkpoint.resolve() == args.output_checkpoint.resolve():
        parser.error("output checkpoint must differ from the resolved input path")
    checkpoint = None
    if args.resume_checkpoint:
        checkpoint = json.loads(read_regular_bounded(args.resume_checkpoint))
    spec = importlib.util.spec_from_file_location("bounded_candidate_transport", ROOT / "scripts/candidate-runtime-canary.py")
    transport = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(transport)
    import online_collect as oc
    session = transport.BoundedSession(oc.NEWS_LIST_SOURCES["S-032"]["list_url"], max_calls=args.http_call_limit)
    try:
        checkpoint, summary = collect_batch(session, args.start, args.end, checkpoint=checkpoint,
            expected_sha256=args.expected_checkpoint_sha256, max_list_pages=args.list_page_limit)
    finally:
        session.close()
    write_checkpoint_atomic(args.output_checkpoint, checkpoint)
    summary["http_attempts_this_batch"] = session.calls
    summary["list_page_limit_this_batch"] = args.list_page_limit
    summary["http_call_limit_this_batch"] = args.http_call_limit
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
