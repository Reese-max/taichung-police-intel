"""Bounded official-document versioning and located-fact candidates."""

from __future__ import annotations

import hashlib
import json
import re
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit


ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT / "docs/govintel/source-catalog.v2.json"
MAX_DOCUMENT_BYTES = 2 * 1024 * 1024
EXTRACTOR_VERSION = "located-facts-v1"
RIGHTS_STATUSES = {"UNKNOWN", "METADATA_LINK_ONLY", "PUBLIC_DERIVED"}
DATE_RE = re.compile(r"(?<!\d)(?:(\d{4})|([0-9]{2,3}))\s*[-/.年]\s*(\d{1,2})\s*[-/.月]\s*(\d{1,2})\s*日?(?!\d)")


def canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def sha256(value: bytes | Any) -> str:
    return hashlib.sha256(value if isinstance(value, bytes) else canonical(value)).hexdigest()


class _Text(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self.parts.append(data)


def normalized_text(body: bytes, content_type: str) -> str:
    if "json" in content_type.lower():
        return json.dumps(json.loads(body.decode("utf-8")), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    parser = _Text()
    parser.feed(body.decode("utf-8", errors="replace"))
    return " ".join(" ".join(parser.parts).split())


def _approved_source(source_id: str, url: str) -> dict[str, Any]:
    catalog = json.loads(CATALOG.read_text(encoding="utf-8"))
    row = next((item for item in catalog.get("sources", []) if item.get("source_id") == source_id), None)
    if not isinstance(row, dict):
        raise ValueError(f"source is not in the server catalog: {source_id}")
    expected = urlsplit(str(row.get("entrypoint") or ""))
    actual = urlsplit(url)
    if expected.scheme != "https" or actual.scheme != "https" or actual.hostname != expected.hostname:
        raise ValueError("document URL is outside the approved source origin")
    return row


def acquire_document(
    *,
    source_id: str,
    requested_url: str,
    final_url: str,
    body: bytes,
    content_type: str,
    fetched_at: str,
    rights_status: str = "UNKNOWN",
) -> dict[str, Any]:
    source = _approved_source(source_id, requested_url)
    _approved_source(source_id, final_url)
    if len(body) > MAX_DOCUMENT_BYTES:
        raise ValueError("document exceeds bounded byte limit")
    if rights_status not in RIGHTS_STATUSES:
        raise ValueError("unsupported rights status")
    raw_hash = sha256(body)
    text = normalized_text(body, content_type)
    text_hash = sha256(text)
    original_identity = f"{source_id}:{final_url}"
    document_id = f"DOC-{sha256(original_identity)[:20].upper()}"
    version_id = f"DOCV-{raw_hash[:20].upper()}"
    return {
        "schema_version": 1,
        "document_id": document_id,
        "document_version_id": version_id,
        "source_id": source_id,
        "original_source_identity": original_identity,
        "source_authority": source.get("authority"),
        "requested_url": requested_url,
        "final_url": final_url,
        "fetched_at": fetched_at,
        "content_type": content_type,
        "raw_bytes_sha256": raw_hash,
        "extracted_text_sha256": text_hash,
        "extractor_version": EXTRACTOR_VERSION,
        "rights_status": rights_status,
        "snapshot_ref": f"sha256:{raw_hash}",
    }


def _normalise(value: Any, normalizer: str) -> Any:
    if normalizer == "integer":
        if isinstance(value, bool):
            raise ValueError("boolean is not an integer fact")
        return int(str(value).replace(",", "").strip())
    if normalizer == "text":
        return " ".join(str(value).split())
    return value


def _date_value(value: str) -> str | None:
    match = DATE_RE.search(value)
    if not match:
        return None
    gregorian, roc, month, day = match.groups()
    year = int(gregorian) if gregorian else int(roc) + 1911
    if not 1912 <= year <= 2200:
        return None
    return f"{year:04d}-{int(month):02d}-{int(day):02d}"


def _fact(document: dict[str, Any], rule: dict[str, Any], raw_value: Any, locator: dict[str, Any], status: str, reason: str | None, valid_time: str | None) -> dict[str, Any]:
    subject_id = str(rule.get("subject_id") or "")
    predicate = str(rule.get("predicate") or "")
    if not subject_id or not predicate:
        raise ValueError("fact rule requires subject_id and predicate")
    normalized = _normalise(raw_value, str(rule.get("normalizer") or "text"))
    material = {"document_version_id": document["document_version_id"], "subject_id": subject_id, "predicate": predicate, "locator": locator}
    fact_id = f"FACT-{sha256(material)[:20].upper()}"
    return {
        "fact_id": fact_id,
        "document_id": document["document_id"],
        "document_version_id": document["document_version_id"],
        "source_id": document["source_id"],
        "original_source_identity": document["original_source_identity"],
        "raw_bytes_sha256": document["raw_bytes_sha256"],
        "extracted_text_sha256": document["extracted_text_sha256"],
        "extractor_version": document["extractor_version"],
        "subject_id": subject_id,
        "subject_type": str(rule.get("subject_type") or "PUBLIC_EVENT"),
        "predicate": predicate,
        "raw_value": raw_value,
        "normalized_value": normalized,
        "unit": rule.get("unit"),
        "valid_time": valid_time,
        "geography": rule.get("geography"),
        "locator": locator,
        "derivation_status": "FACT_CANDIDATE",
        "verification_status": status,
        "verification_method": "RULE_EXTRACTED",
        "review_reason": reason,
    }


def extract_html_facts(document: dict[str, Any], body: bytes, rules: list[dict[str, Any]]) -> list[dict[str, Any]]:
    text = normalized_text(body, document["content_type"])
    facts = []
    for rule in rules:
        needle = str(rule.get("needle") or "")
        if not needle:
            raise ValueError("HTML fact rule requires needle")
        matches = list(re.finditer(re.escape(needle), text))
        if not matches:
            continue
        match = matches[0]
        valid_time = None
        reason = None
        status = "FACT_CANDIDATE"
        date_needle = str(rule.get("date_needle") or "")
        if date_needle:
            date_matches = list(re.finditer(re.escape(date_needle), text))
            if len(date_matches) == 1:
                valid_time = _date_value(date_needle)
            else:
                status, reason = "NEEDS_REVIEW", "AMBIGUOUS_VALID_TIME"
        if len(matches) != 1:
            status, reason = "NEEDS_REVIEW", "AMBIGUOUS_TEXT_MATCH"
        locator = {
            "type": "HTML_TEXT_RANGE",
            "start": match.start(),
            "end": match.end(),
            "quote": match.group(0),
            "document_sha256": document["raw_bytes_sha256"],
            "text_sha256": document["extracted_text_sha256"],
        }
        facts.append(_fact(document, rule, match.group(0), locator, status, reason, valid_time))
    return facts


def _pointer(payload: Any, pointer: str) -> Any:
    if pointer == "":
        return payload
    if not pointer.startswith("/"):
        raise ValueError("JSON locator must be an RFC 6901 pointer")
    value = payload
    for token in pointer[1:].split("/"):
        token = token.replace("~1", "/").replace("~0", "~")
        if isinstance(value, list):
            value = value[int(token)]
        elif isinstance(value, dict):
            value = value[token]
        else:
            raise KeyError(pointer)
    return value


def extract_json_facts(document: dict[str, Any], body: bytes, rules: list[dict[str, Any]]) -> list[dict[str, Any]]:
    payload = json.loads(body.decode("utf-8"))
    facts = []
    for rule in rules:
        pointer = str(rule.get("pointer") or "")
        try:
            raw_value = _pointer(payload, pointer)
        except (KeyError, IndexError, TypeError, ValueError):
            continue
        locator = {
            "type": "JSON_POINTER",
            "pointer": pointer,
            "raw_value": raw_value,
            "document_sha256": document["raw_bytes_sha256"],
            "text_sha256": document["extracted_text_sha256"],
        }
        valid_time = _date_value(str(raw_value)) if rule.get("value_is_date") else None
        facts.append(_fact(document, rule, raw_value, locator, "FACT_CANDIDATE", None, valid_time))
    return facts


def verify_fact(document: dict[str, Any], body: bytes, fact: dict[str, Any]) -> dict[str, Any]:
    raw_hash = sha256(body)
    if raw_hash != document.get("raw_bytes_sha256") or raw_hash != fact.get("raw_bytes_sha256"):
        return {"status": "REJECTED", "reason": "RAW_HASH_MISMATCH", "fact_id": fact.get("fact_id")}
    text = normalized_text(body, document["content_type"])
    if sha256(text) != fact.get("extracted_text_sha256"):
        return {"status": "REJECTED", "reason": "TEXT_HASH_MISMATCH", "fact_id": fact.get("fact_id")}
    locator = fact.get("locator") or {}
    if locator.get("type") == "HTML_TEXT_RANGE":
        actual = text[int(locator["start"]): int(locator["end"])]
        valid = actual == locator.get("quote") and locator.get("text_sha256") == sha256(text)
    elif locator.get("type") == "JSON_POINTER":
        valid = _pointer(json.loads(body.decode("utf-8")), locator.get("pointer", "")) == locator.get("raw_value")
    else:
        valid = False
    return {"status": "PASS" if valid else "REJECTED", "reason": None if valid else "LOCATOR_MISMATCH", "fact_id": fact.get("fact_id")}


def build_bundle(document: dict[str, Any], body: bytes, rules: list[dict[str, Any]]) -> dict[str, Any]:
    if "json" in document["content_type"].lower():
        facts = extract_json_facts(document, body, rules)
    else:
        facts = extract_html_facts(document, body, rules)
    evidence = [
        {
            "evidence_id": f"EVID-{fact['fact_id'][5:]}",
            "fact_id": fact["fact_id"],
            "source_id": fact["source_id"],
            "document_version_id": fact["document_version_id"],
            "official_url": document["final_url"],
            "locator": fact["locator"],
            "content_sha256": document["raw_bytes_sha256"],
            "verification_status": fact["verification_status"],
        }
        for fact in facts
    ]
    events = [
        {
            "stable_id": fact["fact_id"],
            "source_id": fact["source_id"],
            "source_snapshot_ref": document["snapshot_ref"],
            "content_sha256": document["raw_bytes_sha256"],
            "official_url": document["final_url"],
            "subject_id": fact["subject_id"],
            "predicate": fact["predicate"],
            "normalized_value": fact["normalized_value"],
            "valid_time": fact["valid_time"],
            "verification_status": fact["verification_status"],
        }
        for fact in facts
    ]
    receipt = {
        "schema_version": 1,
        "receipt_type": "LOCATED_FACT_EXTRACTION",
        "source_id": document["source_id"],
        "document_version_id": document["document_version_id"],
        "raw_bytes_sha256": document["raw_bytes_sha256"],
        "extracted_text_sha256": document["extracted_text_sha256"],
        "extractor_version": document["extractor_version"],
        "fact_count": len(facts),
        "needs_review_count": sum(fact["verification_status"] == "NEEDS_REVIEW" for fact in facts),
        "bundle_sha256": sha256({"facts": facts, "evidence_catalog": evidence, "public_event_inputs": events}),
    }
    return {"document_version": document, "facts": facts, "evidence_catalog": evidence, "public_event_inputs": events, "receipt": receipt}
