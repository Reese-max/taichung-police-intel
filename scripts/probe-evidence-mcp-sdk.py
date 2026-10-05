#!/usr/bin/env python3
"""Optional offline research probe using the official Python MCP SDK 1.30.0.

Archive the selected committed source into an owned temporary directory; only
synthetic publication copies there are modified. No production dependency is
added. Run with networking disabled as documented in the companion receipt.
"""
from __future__ import annotations

import argparse
import copy
import datetime as dt
import hashlib
import importlib.metadata
import io
import json
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile

SDK_VERSION = "1.30.0"
ARTIFACTS = {"feed": "intelligence-feed.json", "status": "source-status.json", "brief": "v2-daily-brief.json"}
MARKER = "SYNTHETIC_PRIVATE_MARKER_NO_REAL_DATA"
ROOT = Path(__file__).resolve().parents[1]


def git(*args: str) -> bytes:
    return subprocess.check_output(["git", "-C", str(ROOT), *args])


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


async def probe(source: Path, mode: str) -> list[dict]:
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    data = source / "apps/web/public/data"
    docs = {key: json.loads((data / name).read_bytes()) for key, name in ARTIFACTS.items()}
    now = dt.datetime.now(dt.timezone.utc).isoformat()
    run = "CR-SYNTHETIC-ISSUE15-OFFICIAL-SDK"
    docs["feed"]["collection_run_id"] = run
    docs["status"]["latest_collection_run"].update(collection_run_id=run, status="SUCCEEDED")
    docs["brief"].update(source_collection_run_id=run, source_status_generated_at=now,
                         snapshot_complete=True, publication_status="READY")
    for value in docs.values():
        value["generated_at"] = now
    for row in docs["status"]["sources"]:
        row.update(source_health="PASS", window_completeness="COMPLETE_WITH_ITEMS", result="NEW_ITEMS",
                   freshness_status="FRESH", data_as_of=now, last_checked_at=now, last_success_at=now,
                   intelligence_gaps=[])
    for row in docs["feed"]["items"]:
        row.update(freshness_status="FRESH", source_health="PASS", data_as_of=now)
    base = copy.deepcopy(docs)
    rows: list[dict] = []
    previous: dict = {}

    def write(value: dict) -> None:
        for key, name in ARTIFACTS.items():
            (data / name).write_text(json.dumps(value[key], ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def hashes() -> dict:
        return {key: sha256(data / name) for key, name in ARTIFACTS.items()}

    def payload(result):
        if result.isError:
            return {"isError": True, "error": json.loads(result.content[0].text)["error"]}
        return result.structuredContent

    async def case(name, body) -> None:
        params = StdioServerParameters(command=sys.executable,
                                       args=["-X", "utf8", str(source / "scripts/query-gateway-stdio.py")],
                                       cwd=str(source))
        async with stdio_client(params) as streams:
            async with ClientSession(*streams) as session:
                initialized = await session.initialize()
                result = await body(session)
                rows.append({"case": name, "protocol_version": initialized.protocolVersion,
                             "artifact_hashes": hashes(), "result": result})

    if mode == "journeys":
        write(base)

        async def journey(session):
            tools = (await session.list_tools()).tools
            names = [tool.name for tool in tools]
            assert set(names) == {"search_evidence", "get_current_brief", "get_publication_receipt",
                                  "get_source_health", "validate_answer"}
            assert all(tool.annotations.readOnlyHint and not tool.annotations.destructiveHint for tool in tools)
            brief = payload(await session.call_tool("get_current_brief", {}))
            search = payload(await session.call_tool("search_evidence", {"limit": 1}))
            repeated = payload(await session.call_tool("search_evidence", {"limit": 1}))
            health = payload(await session.call_tool("get_source_health", {}))
            receipt = payload(await session.call_tool("get_publication_receipt", {}))
            assert brief["schema_version"] == receipt["schema_version"] == 1
            assert receipt["publication_receipt"]["schema_version"] == 1
            assert search["results"] == repeated["results"]
            assert search["has_more"] and search["truncated"] and search["next_cursor"]
            item = search["results"][0]
            assert item["official_url"].startswith("https://")
            exact = payload(await session.call_tool("search_evidence", {"canonical_id": item["canonical_id"]}))
            assert [row["canonical_id"] for row in exact["results"]] == [item["canonical_id"]]
            page = payload(await session.call_tool("search_evidence", {"limit": 1, "cursor": search["next_cursor"]}))
            assert page["results"][0]["canonical_id"] != item["canonical_id"]
            assert receipt["publication_hash"] == brief["publication_hash"] == search["publication_hash"] == health["publication_hash"]
            assert receipt["publication_receipt"]["artifact_hashes"] == hashes()
            assert receipt["publication_receipt"]["current_as_of_server_clock"]
            for arguments in [{"url": "https://example.invalid"}, {"path": "/etc/passwd"},
                              {"sql": "SELECT * FROM secret"}, {"limit": 101}]:
                assert payload(await session.call_tool("search_evidence", arguments))["isError"]
            previous.update(generation=receipt["query_generation_id"], publication_hash=receipt["publication_hash"],
                            cursor=search["next_cursor"])
            return {"tool_names": names, "publication_hash": receipt["publication_hash"],
                    "generation": receipt["query_generation_id"], "freshness": receipt["freshness"],
                    "current": True, "deterministic_projection": True, "exact_official_locator": item["official_url"],
                    "canonical_ref": item["canonical_ref"], "pagination_truncation": True,
                    "malicious_argument_and_overlimit_rejections": 4}

        await case("sdk_full_brief_search_evidence_health_receipt_journey", journey)
        mutations = {
            "FAILED": {"source_health": "FAIL", "result": "FAILED", "window_completeness": "FAILED"},
            "PARTIAL": {"source_health": "WARN", "result": "PARTIAL", "window_completeness": "PARTIAL"},
            "CONFLICT": {"source_health": "FAIL", "result": "CONFLICT", "window_completeness": "PARTIAL"},
            "STALE": {"source_health": "PASS", "result": "NEW_ITEMS", "freshness_status": "STALE"},
        }
        for state, mutation in mutations.items():
            fixture = copy.deepcopy(base)
            fixture["status"]["sources"][0].update(mutation)
            write(fixture)
            source_id = fixture["status"]["sources"][0]["source_id"]

            async def failure(session, expected=state, selected=source_id):
                health = payload(await session.call_tool("get_source_health", {"source_id": selected}))
                empty = payload(await session.call_tool("search_evidence", {"source_id": selected, "q": "NEVER_MATCH_SDK_SYNTHETIC"}))
                assert empty["result_count"] == 0 and not empty["answerable_no_match"] and empty["source_gaps"]
                assert not health["query_coverage"]["can_state_bounded_no_match"]
                visible = health["sources"][0]
                assert expected in json.dumps(visible)
                return {"source_state": expected, "client_source": visible, "freshness": empty["freshness"],
                        "client_source_gaps": empty["source_gaps"], "answerable_no_match": False,
                        "can_state_bounded_no_match": False}

            await case("sdk_client_visible_" + state, failure)
        write(base)

        async def missing(session):
            result = payload(await session.call_tool("search_evidence", {"source_id": "S-NOT-IN-APPROVED-SNAPSHOT", "q": "NEVER_MATCH"}))
            assert result["source_gaps"] and result["source_gaps"][0]["reason"] == "NOT_IN_APPROVED_SNAPSHOT"
            assert not result["answerable_no_match"]
            return {"freshness": result["freshness"], "source_gaps": result["source_gaps"],
                    "verification_summary": result["verification_summary"], "answerable_no_match": False}

        await case("sdk_client_source_not_available", missing)
        moved = copy.deepcopy(base)
        moved["brief"]["status_message"] = "Synthetic local Issue15 head-B fixture; no real publication claim"
        write(moved)

        async def head(session):
            receipt = payload(await session.call_tool("get_publication_receipt", {}))
            assert receipt["publication_hash"] != previous["publication_hash"]
            assert receipt["query_generation_id"] != previous["generation"]
            stale_generation = payload(await session.call_tool("search_evidence", {"expected_generation": previous["generation"]}))
            stale_cursor = payload(await session.call_tool("search_evidence", {"limit": 1, "cursor": previous["cursor"]}))
            assert stale_generation["isError"] and stale_cursor["isError"]
            return {"old_publication_hash": previous["publication_hash"], "new_publication_hash": receipt["publication_hash"],
                    "old_generation": previous["generation"], "new_generation": receipt["query_generation_id"],
                    "old_expected_generation_rejected": stale_generation, "old_cursor_rejected": stale_cursor,
                    "scope": "Fresh server restart; no hot-reload or deployed-host claim"}

        await case("sdk_publication_head_switch", head)
    else:
        for name, tool in [("brief_nested_private_field", "get_current_brief"),
                           ("source_gap_nested_private_field", "get_source_health")]:
            fixture = copy.deepcopy(base)
            if tool == "get_current_brief":
                fixture["brief"]["source_health"]["private_operational_note"] = MARKER
            else:
                fixture["status"]["sources"][0]["intelligence_gaps"] = [{"private_case": MARKER}]
            write(fixture)

            async def private(session, selected=tool):
                result = await session.call_tool(selected, {})
                returned = MARKER in json.dumps(result.model_dump(mode="json"))
                error = payload(result).get("error") if result.isError else None
                if mode == "private-red":
                    assert not result.isError and returned
                else:
                    assert result.isError and not returned and error["code"] == "PUBLIC_PROJECTION_INVALID"
                return {"isError": result.isError, "error": error, "private_marker_returned": returned,
                        "fixture": "Synthetic canary only; no real private data"}

            await case(name, private)
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-ref", default="HEAD", help="Committed source to archive and probe")
    parser.add_argument("--mode", choices=("journeys", "private-green", "private-red"), default="journeys")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if importlib.metadata.version("mcp") != SDK_VERSION:
        parser.error("use an isolated diagnostic environment with mcp==" + SDK_VERSION)
    import anyio

    commit = git("rev-parse", "--verify", args.source_ref + "^{commit}").decode().strip()
    tree = git("rev-parse", commit + "^{tree}").decode().strip()
    archive = git("archive", "--format=tar", commit)
    with tempfile.TemporaryDirectory(prefix="issue15-offline-mcp-") as owned:
        source = Path(owned)
        with tarfile.open(fileobj=io.BytesIO(archive)) as bundle:
            bundle.extractall(source, filter="data")
        runtime_files = ["scripts/query-gateway.py", "scripts/query-gateway-stdio.py", "intel_v2/query_store.py",
                         "intel_v2/source_policy.py", "intel_v2/public_brief.py"]
        runtime_hashes = {name: sha256(source / name) for name in runtime_files if (source / name).exists()}
        rows = anyio.run(probe, source, args.mode)
    receipt = {"schema_version": 1, "repository": "Reese-max/taichung-police-intel", "issue": 15,
               "mode": args.mode, "source_commit": commit, "source_tree": tree,
               "runtime_file_sha256": runtime_hashes, "client": "Official Python MCP SDK", "client_version": SDK_VERSION,
               "transport": "STDIO", "observed_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
               "fixture_scope": "Owned temporary git archive with synthetic public metadata; live checkout unmodified",
               "network_requirement": "Run inside a network-disabled sandbox; probe performs no provider calls",
               "cases": rows, "limitations": ["Default five read-only metadata tools only",
                   "Head change is exercised by fresh-server restart, not live hot reload",
                   "No real-source promotion, production host, OAuth or human usability claim"],
               "result": "CONFIRMED_RED_MAIN_NESTED_FIELD_LEAK" if args.mode == "private-red" else "PASS"}
    args.output.write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"result": receipt["result"], "cases": len(rows), "source_commit": commit, "client_version": SDK_VERSION}))


if __name__ == "__main__":
    main()
