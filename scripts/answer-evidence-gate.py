#!/usr/bin/env python3
"""Typed claim-level evidence gate for GovIntel answers.

Untrusted answer/claim payloads never carry authoritative evidence records. Evidence is
resolved from a separate, server-controlled catalog tied to one canonical publication
hash. This prevents a Web/MCP caller from self-asserting ``trust_tier=OFFICIAL`` and
receiving a verified receipt.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
from typing import Any

VALIDATOR_VERSION = "answer-evidence-gate/v2"
OFFICIAL_TIERS = {"OFFICIAL", "CANONICAL_PUBLICATION", "CONFIRMED_OFFICIAL"}
TRUSTED_VERIFICATION_STATUSES = {"VERIFIED", "CONFIRMED"}
RECENT = "RECENT"
HEX64 = re.compile(r"^[0-9a-f]{64}$")


def canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_value(value: Any) -> str:
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def receipt_hash(value: Any) -> str:
    return sha256_value(value)


def validate_payload(payload: dict[str, Any]) -> None:
    publication_hash = payload.get("publication_hash")
    if not isinstance(publication_hash, str) or not HEX64.fullmatch(publication_hash):
        raise ValueError("publication_hash must be a lowercase SHA-256 hex string")
    if "evidence" in payload:
        raise ValueError("untrusted answer payload must not embed evidence records")
    claims = payload.get("claims")
    if not isinstance(claims, list):
        raise ValueError("claims must be an array")

    claim_ids: set[str] = set()
    for claim in claims:
        if not isinstance(claim, dict):
            raise ValueError("claim row must be an object")
        claim_id = claim.get("claim_id")
        if not isinstance(claim_id, str) or not claim_id:
            raise ValueError("claim_id missing")
        if claim_id in claim_ids:
            raise ValueError(f"duplicate claim_id: {claim_id}")
        claim_ids.add(claim_id)
        if "current_claim" in claim and not isinstance(claim["current_claim"], bool):
            raise ValueError(f"claim {claim_id} current_claim must be a JSON boolean")
        facts = claim.get("required_facts")
        if not isinstance(facts, list) or not facts:
            raise ValueError(f"claim {claim_id} requires at least one required_fact")
        for fact in facts:
            if not isinstance(fact, dict) or not isinstance(fact.get("field"), str) or not fact["field"] or "value" not in fact:
                raise ValueError(f"claim {claim_id} has malformed required_fact")
        refs = claim.get("evidence_ids")
        if not isinstance(refs, list) or any(not isinstance(ref, str) or not ref for ref in refs):
            raise ValueError(f"claim {claim_id} requires evidence_ids array")


def validate_catalog(catalog: dict[str, Any], publication_hash: str) -> str:
    if catalog.get("schema_version") != 1:
        raise ValueError("unsupported evidence catalog schema_version")
    catalog_publication_hash = catalog.get("publication_hash")
    if catalog_publication_hash != publication_hash:
        raise ValueError("evidence catalog publication_hash does not match answer publication_hash")
    evidence = catalog.get("evidence")
    if not isinstance(evidence, list):
        raise ValueError("trusted evidence catalog requires an evidence array")

    evidence_ids: set[str] = set()
    for item in evidence:
        if not isinstance(item, dict):
            raise ValueError("evidence row must be an object")
        evidence_id = item.get("evidence_id")
        if not isinstance(evidence_id, str) or not evidence_id:
            raise ValueError("evidence_id missing")
        if evidence_id in evidence_ids:
            raise ValueError(f"duplicate evidence_id: {evidence_id}")
        evidence_ids.add(evidence_id)

        if item.get("publication_hash") != publication_hash:
            raise ValueError(f"evidence {evidence_id} is not bound to the requested publication")
        source_id = item.get("source_id")
        if not isinstance(source_id, str) or not source_id:
            raise ValueError(f"evidence {evidence_id} requires source_id")
        version = item.get("source_document_version")
        if not isinstance(version, str) or not version:
            raise ValueError(f"evidence {evidence_id} requires source_document_version")
        content_sha256 = item.get("content_sha256")
        if not isinstance(content_sha256, str) or not HEX64.fullmatch(content_sha256):
            raise ValueError(f"evidence {evidence_id} requires content_sha256")
        locator = item.get("locator")
        if not isinstance(locator, str) or not locator.startswith("https://"):
            raise ValueError(f"evidence {evidence_id} requires an HTTPS locator")
        if not isinstance(item.get("facts"), dict):
            raise ValueError(f"evidence {evidence_id} requires facts object")
        trust_tier = item.get("trust_tier")
        if not isinstance(trust_tier, str) or not trust_tier:
            raise ValueError(f"evidence {evidence_id} requires trust_tier")
        verification_status = item.get("verification_status")
        if not isinstance(verification_status, str) or not verification_status:
            raise ValueError(f"evidence {evidence_id} requires verification_status")
        freshness = item.get("freshness")
        if not isinstance(freshness, str) or not freshness:
            raise ValueError(f"evidence {evidence_id} requires freshness")

    # The catalog hash is computed by the trusted runtime from the full catalog and
    # later included in the answer receipt. It is never accepted from the caller.
    return sha256_value(catalog)


def trusted_official(item: dict[str, Any]) -> bool:
    return (
        item.get("trust_tier") in OFFICIAL_TIERS
        and item.get("verification_status") in TRUSTED_VERIFICATION_STATUSES
    )


def evaluate_claim(claim: dict[str, Any], evidence_by_id: dict[str, dict[str, Any]]) -> dict[str, Any]:
    referenced = []
    missing_refs = []
    for evidence_id in claim["evidence_ids"]:
        item = evidence_by_id.get(evidence_id)
        if item is None:
            missing_refs.append(evidence_id)
        else:
            referenced.append(item)

    current_claim = claim.get("current_claim", True)
    base = {
        "claim_id": claim["claim_id"],
        "text": claim.get("text"),
        "current_claim": current_claim,
        "required_fact_count": len(claim["required_facts"]),
        "evidence_ids": list(claim["evidence_ids"]),
        "missing_evidence_ids": missing_refs,
        "source_ids": sorted({item["source_id"] for item in referenced}),
        "source_document_versions": sorted({item["source_document_version"] for item in referenced}),
        "content_sha256": sorted({item["content_sha256"] for item in referenced}),
        "locators": sorted({item["locator"] for item in referenced}),
    }
    if missing_refs:
        return {**base, "support_status": "UNSUPPORTED", "supported_fact_count": 0, "reason": "MISSING_EVIDENCE_REFERENCE"}

    official = [item for item in referenced if trusted_official(item)]
    if not official:
        return {**base, "support_status": "UNSUPPORTED", "supported_fact_count": 0, "reason": "NO_TRUSTED_OFFICIAL_EVIDENCE"}

    usable = official
    if current_claim:
        recent = [item for item in official if item.get("freshness") == RECENT]
        if not recent:
            return {**base, "support_status": "STALE", "supported_fact_count": 0, "reason": "NO_RECENT_OFFICIAL_EVIDENCE"}
        usable = recent

    supported = 0
    unsupported_fields: list[str] = []
    conflict_fields: list[str] = []
    fact_receipts = []
    for required in claim["required_facts"]:
        field = required["field"]
        expected = required["value"]
        observed = [item["facts"][field] for item in usable if field in item["facts"]]
        distinct = {canonical(value) for value in observed}
        if len(distinct) > 1:
            conflict_fields.append(field)
            fact_receipts.append({"field": field, "status": "CONFLICT", "expected": expected, "observed": observed})
            continue
        if observed and canonical(observed[0]) == canonical(expected):
            supported += 1
            fact_receipts.append({"field": field, "status": "SUPPORTED", "expected": expected})
        else:
            unsupported_fields.append(field)
            fact_receipts.append({"field": field, "status": "UNSUPPORTED", "expected": expected, "observed": observed})

    if conflict_fields:
        status = "CONFLICT"
        reason = "OFFICIAL_EVIDENCE_CONFLICT"
    elif supported == len(claim["required_facts"]):
        status = "SUPPORTED"
        reason = "ALL_REQUIRED_FACTS_SUPPORTED"
    elif supported > 0:
        status = "PARTIAL"
        reason = "SOME_REQUIRED_FACTS_UNSUPPORTED"
    else:
        status = "UNSUPPORTED"
        reason = "NO_REQUIRED_FACT_SUPPORTED"

    return {
        **base,
        "support_status": status,
        "supported_fact_count": supported,
        "unsupported_fields": sorted(unsupported_fields),
        "conflict_fields": sorted(conflict_fields),
        "fact_receipts": fact_receipts,
        "reason": reason,
    }


def gate_answer(payload: dict[str, Any], trusted_catalog: dict[str, Any]) -> dict[str, Any]:
    validate_payload(payload)
    catalog_hash = validate_catalog(trusted_catalog, payload["publication_hash"])
    evidence_by_id = {item["evidence_id"]: item for item in trusted_catalog["evidence"]}
    receipts = [evaluate_claim(claim, evidence_by_id) for claim in payload["claims"]]
    statuses = [receipt["support_status"] for receipt in receipts]
    safe_claim_ids = [receipt["claim_id"] for receipt in receipts if receipt["support_status"] == "SUPPORTED"]
    qualified_claim_ids = [receipt["claim_id"] for receipt in receipts if receipt["support_status"] != "SUPPORTED"]

    if receipts and all(status == "SUPPORTED" for status in statuses):
        answer_status = "VERIFIED"
    elif any(status in {"PARTIAL", "STALE", "CONFLICT"} for status in statuses):
        answer_status = "NEEDS_QUALIFICATION"
    else:
        answer_status = "BLOCKED"

    receipt_core = {
        "validator_version": VALIDATOR_VERSION,
        "publication_hash": payload["publication_hash"],
        "evidence_catalog_hash": catalog_hash,
        "answer_status": answer_status,
        "claim_receipts": receipts,
        "safe_claim_ids": safe_claim_ids,
        "qualified_claim_ids": qualified_claim_ids,
    }
    return {"schema_version": 2, **receipt_core, "receipt_hash": receipt_hash(receipt_core)}


def _self_check_catalog(publication_hash: str) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "publication_hash": publication_hash,
        "evidence": [
            {
                "evidence_id": "e1",
                "publication_hash": publication_hash,
                "source_id": "S-POLICE",
                "trust_tier": "OFFICIAL",
                "verification_status": "VERIFIED",
                "freshness": "RECENT",
                "source_document_version": "doc:v3",
                "content_sha256": "b" * 64,
                "locator": "https://example.gov/doc#p4",
                "facts": {"start_time": "16:00"},
            }
        ],
    }


def self_check() -> None:
    publication_hash = "a" * 64
    payload = {
        "publication_hash": publication_hash,
        "claims": [
            {
                "claim_id": "c1",
                "text": "管制提前至16:00，原因是豪雨",
                "current_claim": True,
                "required_facts": [
                    {"field": "start_time", "value": "16:00"},
                    {"field": "cause", "value": "豪雨"},
                ],
                "evidence_ids": ["e1"],
            }
        ],
    }
    result = gate_answer(payload, _self_check_catalog(publication_hash))
    assert result["answer_status"] == "NEEDS_QUALIFICATION"
    assert result["claim_receipts"][0]["support_status"] == "PARTIAL"
    assert result["claim_receipts"][0]["unsupported_fields"] == ["cause"]
    assert HEX64.fullmatch(result["evidence_catalog_hash"])
    print(f"ANSWER_EVIDENCE_GATE_SELF_CHECK_OK receipt={result['receipt_hash'][:12]} status={result['answer_status']}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path)
    parser.add_argument("--evidence-catalog", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--self-check", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.self_check:
        self_check()
        return 0
    if not args.input or not args.evidence_catalog:
        raise SystemExit("--input and --evidence-catalog are required unless --self-check is used")
    payload = json.loads(args.input.read_text(encoding="utf-8"))
    catalog = json.loads(args.evidence_catalog.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or not isinstance(catalog, dict):
        raise SystemExit("input and evidence catalog must be JSON objects")
    result = gate_answer(payload, catalog)
    text = json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding="utf-8")
    else:
        print(text, end="")
    return 0 if result["answer_status"] != "BLOCKED" else 2


if __name__ == "__main__":
    raise SystemExit(main())
