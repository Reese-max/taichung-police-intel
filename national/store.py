"""SQLite and Wrangler backends; SQL values are encoded, never interpolated raw."""
from datetime import datetime, timezone
import json
from pathlib import Path
import shutil
import sqlite3
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = Path(__file__).with_name("schema.sql")
# Reserve the audit row and its primary-key index, separately from ingestion totals.
RUN_WRITE_RESERVE = 2


def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def sql_value(value):
    if value is None:
        return "NULL"
    if type(value) is int:
        return str(value)
    if not isinstance(value, str):
        raise ValueError("SQL values must be strings, integers or null")
    # Hex literals round-trip NUL, quotes, backslashes and newlines in both engines.
    return "CAST(X'" + value.encode("utf-8").hex() + "' AS TEXT)"


class Store:
    def apply_schema(self):
        return self.execute_batch(SCHEMA.read_text(encoding="utf-8"))

    def existing_hashes(self, source_id, external_ids):
        found = {}
        ids = list(external_ids)
        for start in range(0, len(ids), 10):
            rows = self.query("SELECT external_id, content_sha256 FROM records WHERE source_id="
                              + sql_value(source_id) + " AND external_id IN ("
                              + ",".join(sql_value(v) for v in ids[start:start + 10]) + ")")
            found.update((row["external_id"], row["content_sha256"]) for row in rows)
        return found

    def source_hash(self, source_id):
        rows = self.query("SELECT registry_sha256 FROM sources WHERE source_id=" + sql_value(source_id))
        return rows[0]["registry_sha256"] if rows else None

    def rows_written_today(self):
        # The loader stops before starting a batch on a different UTC day.
        rows = self.query("SELECT COALESCE(SUM(rows_written),0) + COUNT(*) * "
                          + str(RUN_WRITE_RESERVE) + " AS total FROM runs WHERE substr(started_at,1,10)="
                          + sql_value(utc_now()[:10]))
        return int(rows[0]["total"])

    def resume_cursor(self, source_id):
        rows = self.query("SELECT status,cursor_after FROM runs WHERE source_id=" + sql_value(source_id)
                          + " ORDER BY rowid DESC LIMIT 1")
        if rows and rows[0]["status"] in {"BUDGET_STOPPED", "CAPACITY_BLOCKED", "ERROR"}:
            return json.loads(rows[0]["cursor_after"]) if rows[0]["cursor_after"] is not None else None
        return None

    def record_run(self, run):
        keys = ("run_id", "source_id", "started_at", "finished_at", "status", "fetched", "changed",
                "rows_written", "cursor_before", "cursor_after", "error")
        values = dict(run)
        for key in ("cursor_before", "cursor_after"):
            values[key] = json.dumps(values[key], ensure_ascii=False) if values[key] is not None else None
        return self.execute_batch("INSERT INTO runs (" + ",".join(keys) + ") VALUES ("
                                  + ",".join(sql_value(values[key]) for key in keys) + ");")


class SqliteStore(Store):
    def __init__(self, path):
        self.connection = sqlite3.connect(path)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys=ON")

    def query(self, sql):
        return [dict(row) for row in self.connection.execute(sql)]

    def execute_batch(self, sql):
        before = self.connection.total_changes
        try:
            self.connection.executescript("BEGIN;\n" + sql + "\nCOMMIT;")
        except Exception:
            self.connection.rollback()
            raise
        return self.connection.total_changes - before

    def database_size_bytes(self):
        return (self.query("PRAGMA page_count")[0]["page_count"]
                * self.query("PRAGMA page_size")[0]["page_size"])

    def close(self):
        self.connection.close()


class WranglerD1Store(Store):
    def __init__(self, *, remote=False, database="govintel-national"):
        self.remote = remote
        self.database = database
        self.cwd = ROOT / "workers/query-gateway"
        self.command = shutil.which("wrangler")
        if not self.command:
            raise RuntimeError("Wrangler must already be installed")

    def _call(self, args):
        result = subprocess.run([self.command, "d1", *args, "--json"], cwd=self.cwd,
                                capture_output=True, text=True, encoding="utf-8", check=False)
        if result.returncode:
            raise RuntimeError("Wrangler command failed (exit " + str(result.returncode) + ")")
        data = json.loads(result.stdout)
        entries = data if isinstance(data, list) else [data]
        if any(entry.get("success") is False for entry in entries):
            raise RuntimeError("Wrangler reported an unsuccessful operation")
        return entries

    def _execute(self, *args):
        return self._call(["execute", self.database, "--remote" if self.remote else "--local", *args])

    def query(self, sql):
        return [row for entry in self._execute("--command", sql) for row in entry.get("results", [])]

    def execute_batch(self, sql):
        # Closed before spawning: Windows does not allow Wrangler to reopen a locked file.
        if not self.remote:
            # Wrangler local strips meta.rows_written; measure SQLite changes in
            # the SAME batch/connection, including FTS triggers, rather than guess.
            sql = "SELECT total_changes() AS national_changes;\n" + sql + "\nSELECT total_changes() AS national_changes;"
        with tempfile.TemporaryDirectory(prefix="national-", dir=self.cwd) as directory:
            path = Path(directory) / "batch.sql"
            path.write_text(sql, encoding="utf-8")
            entries = self._execute("--file", str(path))
        if not self.remote and not all("rows_written" in entry.get("meta", {}) for entry in entries):
            return int(entries[-1]["results"][0]["national_changes"]) - int(entries[0]["results"][0]["national_changes"])
        # Missing remote accounting must fail closed, not silently become zero.
        return sum(int(entry["meta"]["rows_written"]) for entry in entries)

    def database_size_bytes(self):
        if self.remote:
            info = self._call(["info", self.database])[0]
            # Recent Wrangler renames the API's file_size to database_size.
            return int(info["file_size"] if "file_size" in info else info["database_size"])
        # d1 info is remote-only and D1 denies PRAGMA page_count (SQLITE_AUTH); size the miniflare files.
        # shortcut: sums every local D1 file in this worker's state, fine while it binds one database.
        return sum(path.stat().st_size for path in (self.cwd / ".wrangler/state/v3/d1").rglob("*.sqlite*"))

    def close(self):
        pass
