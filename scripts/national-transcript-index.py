#!/usr/bin/env python3
"""Internal-only local transcript index; never publish or load into D1."""
import argparse
from contextlib import closing
from datetime import date, datetime, timedelta, timezone
import json
from pathlib import Path
import sqlite3
import sys
from urllib.parse import urlencode, urlsplit
from uuid import UUID

from requests import RequestException

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from national.adapters.http import HttpAdapter, text

DEFAULT_DB = Path.home() / ".cache/govintel/transcripts.sqlite"
SCHEMA = """
CREATE TABLE IF NOT EXISTS meetings (
  proceedings_id TEXT PRIMARY KEY, date TEXT NOT NULL, term TEXT, sub_term TEXT,
  session_display TEXT, meeting_type TEXT, name TEXT,
  paragraph_count INTEGER NOT NULL, fetched_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS paragraphs (
  proceedings_id TEXT NOT NULL REFERENCES meetings(proceedings_id),
  ordinal INTEGER NOT NULL, speaker TEXT NOT NULL, content TEXT NOT NULL,
  PRIMARY KEY (proceedings_id, ordinal)
);
CREATE VIRTUAL TABLE IF NOT EXISTS paragraphs_fts USING fts5(
  content, content='paragraphs', content_rowid='rowid', tokenize='trigram'
);
CREATE TRIGGER IF NOT EXISTS paragraphs_ai AFTER INSERT ON paragraphs BEGIN
  INSERT INTO paragraphs_fts(rowid, content) VALUES (new.rowid, new.content);
END;
CREATE TRIGGER IF NOT EXISTS paragraphs_ad AFTER DELETE ON paragraphs BEGIN
  INSERT INTO paragraphs_fts(paragraphs_fts, rowid, content)
    VALUES ('delete', old.rowid, old.content);
END;
CREATE TRIGGER IF NOT EXISTS paragraphs_au AFTER UPDATE ON paragraphs BEGIN
  INSERT INTO paragraphs_fts(paragraphs_fts, rowid, content)
    VALUES ('delete', old.rowid, old.content);
  INSERT INTO paragraphs_fts(rowid, content) VALUES (new.rowid, new.content);
END;
CREATE TABLE IF NOT EXISTS sync_state (
  id INTEGER PRIMARY KEY CHECK(id=1), since TEXT, until TEXT
);
"""


def source_config():
    registry = json.loads((ROOT / "docs/govintel/national-sources.v1.json").read_text(encoding="utf-8"))
    return next(s for s in registry["sources"] if s["national_source_id"] == "N-COUNCIL-TXG")


def origin(source):
    endpoint = urlsplit(source["endpoints"][0])
    return f"{endpoint.scheme}://{endpoint.netloc}"


def db_path(value):
    path = Path(value).expanduser().resolve()
    if path.is_relative_to(ROOT):
        raise ValueError("transcript database must be outside the repository")
    # Also reject another checkout, including linked worktrees.
    if any((parent / ".git").exists() for parent in path.parents):
        raise ValueError("transcript database must be outside a Git checkout")
    return path


def iso_date(value):
    if date.fromisoformat(value).isoformat() != value:
        raise ValueError("date must be YYYY-MM-DD")
    return value


def date_range(start, end):
    for value in (start, end):
        if value is not None:
            iso_date(value)
    if start and end and start > end:
        raise ValueError("start date must not exceed end date")


def sync_window(db, since, until):
    if since is None and until is None:
        pending = db.execute("SELECT since, until FROM sync_state WHERE id=1").fetchone()
        if pending is not None:
            return tuple(pending)
    if since is None:
        latest = db.execute("SELECT MAX(date) FROM meetings").fetchone()[0]
        if latest:
            since = (date.fromisoformat(latest) - timedelta(days=14)).isoformat()
    date_range(since, until)
    return since, until


def list_meetings(adapter, since, until, page_size):
    meetings, scanned, expected, page = {}, 0, None, 1
    while True:
        query = {"pageNumber": page, "pageSize": page_size}
        if since:
            query["dateStart"] = since
        if until:
            query["dateEnd"] = until
        payload = adapter.request(origin(adapter.source) + "/api/ProceedingsBackWeb/FrontList?"
                                  + urlencode(query), max_bytes=8 * 1024 * 1024)
        data = payload.get("data")
        if payload.get("success") is not True or not isinstance(data, dict):
            raise ValueError("invalid transcript list payload")
        total, pages = data.get("totalCount"), data.get("totalPages")
        if (type(total) is not int or total < 0 or type(pages) is not int
                or pages not in ({0, 1} if total == 0 else {(total + page_size - 1) // page_size})):
            raise ValueError("invalid transcript pagination totals")
        if expected is None:
            expected = total, pages
        if expected != (total, pages):
            raise ValueError("transcript pagination totals changed")
        if (type(data.get("pageNumber")) is not int or data["pageNumber"] != page
                or type(data.get("pageSize")) is not int or data["pageSize"] != page_size):
            raise ValueError("transcript page metadata mismatch")
        rows = data.get("data")
        if not isinstance(rows, list) or len(rows) != min(page_size, total - scanned):
            raise ValueError(f"transcript page row count mismatch: page={page}")
        for row in rows:
            if not isinstance(row, dict):
                raise ValueError("invalid transcript list row")
            pid = text(row, "proceedingsId")
            UUID(pid)
            meeting_date = datetime.fromisoformat(text(row, "date")).date().isoformat()
            if (since and meeting_date < since) or (until and meeting_date > until):
                raise ValueError("transcript row outside requested date range")
            item = meetings.setdefault(pid, {"date": meeting_date, "count": 0})
            if item["date"] != meeting_date:
                raise ValueError("conflicting dates for the same meeting")
            item["count"] += 1
        scanned += len(rows)
        if page >= pages:
            return meetings, scanned
        page += 1


def sync(db, adapter, *, since=None, until=None, max_meetings=None, page_size=1000):
    if not 1 <= page_size <= 1000 or (max_meetings is not None and max_meetings <= 0):
        raise ValueError("page_size must be 1..1000; max_meetings must be positive")
    since, until = sync_window(db, since, until)
    meetings, scanned = list_meetings(adapter, since, until, page_size)
    report = dict(since=since, until=until, scanned_rows=scanned, added=0, updated=0,
                  detail_requests=0, skipped=[], warnings=[])
    # Preserve an unfinished full/backdated window across interruption or a cap.
    with db:
        db.execute("INSERT OR REPLACE INTO sync_state VALUES (1, ?, ?)", (since, until))
    incomplete = False
    for pid, item in meetings.items():
        stored = db.execute("SELECT paragraph_count FROM meetings WHERE proceedings_id=?", (pid,)).fetchone()
        if stored is not None and stored[0] == item["count"]:
            report["skipped"].append(dict(proceedings_id=pid, reason="unchanged"))
            continue
        if max_meetings is not None and report["detail_requests"] >= max_meetings:
            report["skipped"].append(dict(proceedings_id=pid, reason="max_meetings"))
            incomplete = True
            continue
        report["detail_requests"] += 1
        payload = adapter.request(origin(adapter.source) + "/api/ProceedingsBackWeb/FrontDetail?"
                                  + urlencode({"id": pid}), max_bytes=4 * 1024 * 1024)
        detail = payload.get("data")
        if (payload.get("success") is not True or not isinstance(detail, dict)
                or detail.get("proceedingsId") != pid
                or not isinstance(detail.get("proceedingContents"), list)):
            raise ValueError("invalid transcript detail payload")
        paragraphs = []
        for ordinal, row in enumerate(detail["proceedingContents"]):
            if not isinstance(row, dict) or type(row.get("isDeleted")) is not bool:
                raise ValueError("invalid transcript paragraph")
            if row["isDeleted"]:
                continue
            if not isinstance(row.get("speaker"), dict) or not isinstance(row.get("content"), str):
                raise ValueError("invalid transcript content/speaker")
            paragraphs.append((pid, ordinal, text(row["speaker"], "name"), row["content"]))
        if len(paragraphs) != item["count"]:
            warning = dict(proceedings_id=pid, reason="paragraph_count_mismatch",
                           expected=item["count"], actual=len(paragraphs))
            report["skipped"].append(warning)
            report["warnings"].append(warning)
            incomplete = True
            continue
        values = (pid, item["date"], text(detail, "term"), text(detail, "subTerm"),
                  text(detail, "sessionDisplay"), text(detail, "meetingTypeName"),
                  text(detail, "name"), len(paragraphs), datetime.now(timezone.utc).isoformat())
        with db:
            db.execute("DELETE FROM paragraphs WHERE proceedings_id=?", (pid,))
            db.execute("INSERT OR REPLACE INTO meetings VALUES (?,?,?,?,?,?,?,?,?)", values)
            db.executemany("INSERT INTO paragraphs VALUES (?,?,?,?)", paragraphs)
        report["updated" if stored else "added"] += 1
    if not incomplete:
        with db:
            db.execute("DELETE FROM sync_state")
    report["complete"] = not incomplete
    return report


def search(db, query, *, limit=20, speaker=None, start=None, end=None):
    query = query.strip()
    if not query or len(query) > 1000 or "\0" in query or limit <= 0:
        raise ValueError("invalid query or limit")
    date_range(start, end)
    if len(query) >= 3:
        conditions = ["p.rowid IN (SELECT rowid FROM paragraphs_fts WHERE paragraphs_fts MATCH ?)"]
        values = ['"' + query.replace('"', '""') + '"']
    else:
        conditions = ["p.content LIKE ? ESCAPE '\\'"]
        values = ["%" + query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"]
    for field, operator, value in (("p.speaker", "=", speaker), ("m.date", ">=", start), ("m.date", "<=", end)):
        if value is not None:
            conditions.append(f"{field} {operator} ?")
            values.append(value)
    rows = db.execute("SELECT m.date, m.session_display, m.meeting_type, m.name, p.speaker, "
                      "p.content, p.proceedings_id, p.ordinal FROM paragraphs p JOIN meetings m "
                      "ON m.proceedings_id=p.proceedings_id WHERE " + " AND ".join(conditions)
                      + " ORDER BY m.date DESC, p.proceedings_id, p.ordinal LIMIT ?", values + [limit])
    results = []
    base = origin(source_config())
    for row in rows:
        day, session, meeting_type, name, who, content, pid, ordinal = row
        offset = content.lower().find(query.lower())
        offset = max(0, offset)
        left, right = max(0, offset - 40), min(len(content), offset + len(query) + 40)
        results.append(dict(date=day, sessionDisplay=session, meetingTypeName=meeting_type,
                            name=name, speaker=who, ordinal=ordinal,
                            snippet=("…" if left else "") + content[left:right] + ("…" if right < len(content) else ""),
                            official_url=base + "/meeting-records/" + pid))
    return results


def stats(db, path):
    count, earliest, latest = db.execute("SELECT COUNT(*), MIN(date), MAX(date) FROM meetings").fetchone()
    return dict(meetings=count, paragraphs=db.execute("SELECT COUNT(*) FROM paragraphs").fetchone()[0],
                earliest_date=earliest, latest_date=latest, database_bytes=path.stat().st_size)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    sync_parser = commands.add_parser("sync")
    sync_parser.add_argument("--since")
    sync_parser.add_argument("--until")
    sync_parser.add_argument("--max-meetings", type=int)
    sync_parser.add_argument("--page-size", type=int, default=1000)
    search_parser = commands.add_parser("search")
    search_parser.add_argument("query")
    search_parser.add_argument("--limit", type=int, default=20)
    search_parser.add_argument("--speaker")
    search_parser.add_argument("--from", dest="start")
    search_parser.add_argument("--to", dest="end")
    search_parser.add_argument("--json", action="store_true")
    stats_parser = commands.add_parser("stats")
    for command in (sync_parser, search_parser, stats_parser):
        command.add_argument("--db", default=str(DEFAULT_DB))
    args = parser.parse_args(argv)
    try:
        path = db_path(args.db)
        if args.command == "sync":
            date_range(args.since, args.until)
            if not 1 <= args.page_size <= 1000 or (args.max_meetings is not None and args.max_meetings <= 0):
                raise ValueError("invalid page size or meeting limit")
            path.parent.mkdir(parents=True, exist_ok=True)
            with closing(sqlite3.connect(path)) as db:
                db.execute("PRAGMA foreign_keys=ON")
                db.executescript(SCHEMA)
                with closing(HttpAdapter(source_config())) as adapter:
                    result = sync(db, adapter, since=args.since, until=args.until,
                                  max_meetings=args.max_meetings, page_size=args.page_size)
        else:
            with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as db:
                result = (stats(db, path) if args.command == "stats" else
                          search(db, args.query, limit=args.limit, speaker=args.speaker,
                                 start=args.start, end=args.end))
        if args.command == "search" and not args.json:
            for row in result:
                print("\t".join(str(row[k]) for k in ("date", "sessionDisplay", "meetingTypeName", "name", "speaker", "snippet", "official_url")))
        else:
            print(json.dumps(result, ensure_ascii=False))
        return 0
    except (ValueError, OSError, sqlite3.Error, RuntimeError, RequestException) as error:
        print(json.dumps({"status": "ERROR", "error": str(error)}, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
