import pg from "pg";

let pool;

export function database() {
  if (!process.env.DATABASE_URL) throw new Error("DATABASE_URL is required");
  pool ??= new pg.Pool({ connectionString: process.env.DATABASE_URL, max: 5 });
  return pool;
}
