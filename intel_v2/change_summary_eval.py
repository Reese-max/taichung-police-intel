"""Diagnostic evaluation of the change summarizer on labelled before/after pairs.

The result is scoped to the case file it ran on. Synthetic seeds report
``SYNTHETIC_DIAGNOSTIC_NOT_HOLDOUT``; a disabled provider reports ``NOT_RUN``
and never fills metrics.
"""

from __future__ import annotations

import collections
from typing import Any, Callable, Iterable

from intel_v2.change_summary import (
    PROMPT_VERSION,
    SummaryInput,
    SummaryProvider,
    canonical_sha256,
    summarize_change,
)

ProviderFor = Callable[[dict[str, Any]], SummaryProvider]


def validate_cases(cases: list[dict[str, Any]]) -> None:
    seen: set[str] = set()
    for case in cases:
        case_id = case.get("case_id")
        if not isinstance(case_id, str) or not case_id.strip() or case_id in seen:
            raise ValueError(f"case_id must be unique and nonempty: {case_id!r}")
        seen.add(case_id)
        if case.get("synthetic") is not True:
            raise ValueError(f"case must be marked synthetic before transport: {case_id}")
        if not isinstance(case.get("before"), str) or not isinstance(case.get("after"), str):
            raise ValueError(f"before/after text required: {case_id}")
        if not isinstance(case.get("should_refuse"), bool):
            raise ValueError(f"should_refuse must be boolean: {case_id}")
        for key in ("must_contain", "forbidden"):
            if not isinstance(case.get(key), list) or not all(isinstance(v, str) and v for v in case[key]):
                raise ValueError(f"{key} must be a list of nonempty strings: {case_id}")
        if case["should_refuse"] and case["must_contain"]:
            raise ValueError(f"a refusal case cannot require content: {case_id}")


def _score(case: dict[str, Any], summary: dict[str, Any]) -> dict[str, Any]:
    refused = summary["status"] == "REFUSED"
    texts = [statement["text"] for statement in summary["statements"]]
    joined = "\n".join(texts)
    missing = [value for value in case["must_contain"] if value not in joined]
    forbidden_hits = [value for value in case["forbidden"] if value in joined]
    if case["should_refuse"]:
        correct = refused
    else:
        correct = not refused and not missing and not forbidden_hits
    return {
        "case_id": case["case_id"],
        "status": summary["status"],
        "reason_code": summary["reason_code"],
        "correct": correct,
        "missing_values": missing,
        "forbidden_hits": forbidden_hits,
        "verified_statements": len(texts),
        "rejected_codes": [row["code"] for row in summary["rejected_statements"]],
        "summary_sha256": summary["summary_sha256"],
    }


def evaluate(cases: Iterable[dict[str, Any]], provider_for: ProviderFor) -> dict[str, Any]:
    cases = list(cases)
    validate_cases(cases)
    rows = []
    provider_info = None
    for case in cases:
        provider = provider_for(case)
        provider_info = provider_info or {"provider": provider.name, "model": provider.model}
        data = SummaryInput(
            identity=f"EVAL:{case['case_id']}",
            source_id="EVAL",
            official_url="https://example.gov.tw/synthetic/eval",
            before_text=case["before"],
            after_text=case["after"],
            before_version=1,
            after_version=2,
            synthetic=True,
        )
        rows.append(_score(case, summarize_change(data, provider)))

    disabled = bool(rows) and all(row["reason_code"] == "PROVIDER_DISABLED" for row in rows)
    report: dict[str, Any] = {
        "schema_version": 1,
        "evaluation_scope": "SYNTHETIC_DIAGNOSTIC_NOT_HOLDOUT",
        "evaluation_status": "NOT_RUN" if disabled else "EVALUATED",
        "prompt_version": PROMPT_VERSION,
        "case_count": len(cases),
        "case_file_sha256": canonical_sha256(cases),
        "ai_disclosure": provider_info,
        "official_records_transmitted": 0,
        "human_participants": 0,
        "metrics": None,
        "cases": rows,
    }
    if disabled:
        return report

    positives = [row for case, row in zip(cases, rows) if not case["should_refuse"]]
    refusals = [row for case, row in zip(cases, rows) if case["should_refuse"]]
    rejected = collections.Counter(code for row in rows for code in row["rejected_codes"])
    report["metrics"] = {
        "cases_correct": sum(row["correct"] for row in rows),
        "change_cases": len(positives),
        "change_cases_covered": sum(row["correct"] for row in positives),
        "refusal_cases": len(refusals),
        "correct_refusals": sum(row["correct"] for row in refusals),
        "false_refusals": sum(row["status"] == "REFUSED" for row in positives),
        "forbidden_value_cases": sum(bool(row["forbidden_hits"]) for row in rows),
        "verified_statements": sum(row["verified_statements"] for row in rows),
        "rejected_statements_by_code": dict(sorted(rejected.items())),
    }
    return report
