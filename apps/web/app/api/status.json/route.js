import { readFile } from "node:fs/promises";
import { resolve } from "node:path";

export const dynamic = "force-static";

export async function GET() {
  const state = JSON.parse(await readFile(resolve(process.cwd(), "public/data/source-status.json"), "utf8"));
  return Response.json(state);
}
