// National bill metadata is independent of the publication evidence catalog.
const FIELDS = "id title proposer cosigners case_number committee outcome official_url fetched_at".split(" ");
const TERM = "CAST(substr(r.case_number, 2, instr(r.case_number, '屆') - 2) AS INTEGER)";
const SESSION = "CAST(replace(substr(r.case_number, instr(r.case_number, '屆') + 1, instr(r.case_number, '次') - instr(r.case_number, '屆') - 1), '第', '') AS INTEGER)";
const like = value => value.replace(/[\\%_]/g, "\\$&");
const phrase = value => '"' + value.replaceAll('"', '""') + '"';

export class NationalError extends Error {
  constructor(code, message, status = 400) { super(message); this.code = code; this.status = status; }
}

function encodeCursor(row) {
  return btoa(JSON.stringify({ term: row.bill_term, session: row.bill_session, id: row.id }))
    .replaceAll("+", "-").replaceAll("/", "_").replace(/=+$/, "");
}

function decodeCursor(value) {
  try {
    if (typeof value !== "string" || !/^[A-Za-z0-9_-]{1,256}$/.test(value)) throw new Error();
    const cursor = JSON.parse(atob(value.replaceAll("-", "+").replaceAll("_", "/")));
    if (!cursor || Object.keys(cursor).sort().join(",") !== "id,session,term"
        || !Number.isSafeInteger(cursor.id) || cursor.id < 1
        || ![cursor.term, cursor.session].every(n => Number.isSafeInteger(n) && n >= 0)) throw new Error();
    return cursor;
  } catch { throw new NationalError("INVALID_ARGUMENTS", "invalid cursor"); }
}

export async function searchCouncilBills(db, args) {
  const conditions = ["r.source_id = ?", "r.kind = 'bill'"], values = ["N-COUNCIL-TXG"];
  for (const [key, max] of [["q", 200], ["proposer", 50], ["session", 50], ["outcome", 50]]) {
    if (args[key] !== undefined && (typeof args[key] !== "string" || args[key].length > max || args[key].includes("\0")))
      throw new NationalError("INVALID_ARGUMENTS", "invalid " + key);
  }
  const q = (args.q || "").trim(), proposer = (args.proposer || "").trim();
  if (q) {
    conditions.push([...q].length >= 3 ? "r.id IN (SELECT rowid FROM records_fts WHERE records_fts MATCH ?)" : "r.title LIKE ? ESCAPE '\\'");
    values.push([...q].length >= 3 ? phrase(q) : "%" + like(q) + "%");
  }
  if (proposer) {
    conditions.push([...proposer].length >= 3 ? "r.id IN (SELECT rowid FROM bill_people_fts WHERE bill_people_fts MATCH ?)" : "r.proposer LIKE ? ESCAPE '\\'");
    values.push([...proposer].length >= 3 ? "proposer : " + phrase(proposer) : "%" + like(proposer) + "%");
  }
  if (args.session?.trim()) { conditions.push("r.case_number LIKE ? ESCAPE '\\'"); values.push(like(args.session.trim()) + "%"); }
  if (args.outcome?.trim()) { conditions.push("r.outcome LIKE ? ESCAPE '\\'"); values.push("%" + like(args.outcome.trim()) + "%"); }
  if (args.cursor !== undefined) {
    const cursor = decodeCursor(args.cursor);
    conditions.push("(" + TERM + ", " + SESSION + ", r.id) < (?, ?, ?)");
    values.push(cursor.term, cursor.session, cursor.id);
  }
  const limit = args.limit ?? 20;
  if (!Number.isInteger(limit) || limit < 1 || limit > 20) throw new NationalError("INVALID_ARGUMENTS", "invalid limit");
  if (!db) throw new NationalError("CAPABILITY_NOT_AVAILABLE", "National D1 is not configured", 422);
  try {
    const sql = "SELECT " + FIELDS.map(field => "r." + field).join(",") + ", " + TERM + " AS bill_term, "
      + SESSION + " AS bill_session FROM records r WHERE " + conditions.join(" AND ")
      + " ORDER BY bill_term DESC, bill_session DESC, r.id DESC LIMIT ?";
    const result = await db.prepare(sql).bind(...values, limit + 1).all();
    if (result.success === false || !Array.isArray(result.results)) throw new Error("query failed");
    const rows = result.results.slice(0, limit);
    return { note: "Bills have no official date; results are ordered by newest term and session first. People metadata and search may be incomplete during backfill.",
      records: rows.map(row => ({ evidence_id: "NAT-" + row.id,
        ...Object.fromEntries(FIELDS.filter(field => field !== "id").map(field => [field, row[field] ?? null])) })),
      next_cursor: result.results.length > limit ? encodeCursor(rows.at(-1)) : null };
  } catch { throw new NationalError("NATIONAL_STORE_UNAVAILABLE", "National D1 query failed", 503); }
}

export async function getNationalBill(db, id) {
  if (!db) throw new NationalError("CAPABILITY_NOT_AVAILABLE", "National D1 is not configured", 422);
  try {
    return await db.prepare("SELECT " + FIELDS.map(field => "r." + field).join(",")
      + ", r.source_id, r.content_sha256 FROM records r WHERE r.id = ? AND r.kind = 'bill'").bind(id).first();
  } catch { throw new NationalError("NATIONAL_STORE_UNAVAILABLE", "National D1 query failed", 503); }
}
