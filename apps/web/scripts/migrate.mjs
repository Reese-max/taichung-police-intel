import { createHash } from "node:crypto";
import { readFile, readdir } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import pg from "pg";


const root = resolve(dirname(fileURLToPath(import.meta.url)), "../../..");
const migrationDir = resolve(root, "migrations");
const names = (await readdir(migrationDir)).filter((name) => /^\d{4}_.+\.sql$/.test(name)).sort();
if (!names.length || new Set(names).size !== names.length) throw new Error("No ordered migrations found");

const migrations = await Promise.all(names.map(async (name) => {
  const sql = await readFile(resolve(migrationDir, name), "utf8");
  return { name, sql, sha256: createHash("sha256").update(sql).digest("hex") };
}));

if (process.argv.includes("--self-check")) {
  console.log(`MIGRATION_SELF_CHECK_OK files=${migrations.length}`);
  process.exit(0);
}

if (!process.env.DATABASE_URL) throw new Error("DATABASE_URL is required");
const client = new pg.Client({ connectionString: process.env.DATABASE_URL });
await client.connect();
try {
  await client.query(`
    CREATE TABLE IF NOT EXISTS schema_migrations (
      name TEXT PRIMARY KEY,
      sha256 TEXT NOT NULL CHECK (sha256 ~ '^[0-9a-f]{64}$'),
      applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
    )
  `);
  for (const migration of migrations) {
    const applied = await client.query("SELECT sha256 FROM schema_migrations WHERE name = $1", [migration.name]);
    if (applied.rows[0]?.sha256 === migration.sha256) continue;
    if (applied.rowCount) throw new Error(`Applied migration changed: ${migration.name}`);
    await client.query("BEGIN");
    try {
      await client.query(migration.sql);
      await client.query(
        "INSERT INTO schema_migrations (name, sha256) VALUES ($1, $2)",
        [migration.name, migration.sha256],
      );
      await client.query("COMMIT");
      console.log(`MIGRATION_APPLIED ${migration.name}`);
    } catch (error) {
      await client.query("ROLLBACK");
      throw error;
    }
  }
  console.log(`MIGRATION_OK files=${migrations.length}`);
} finally {
  await client.end();
}
