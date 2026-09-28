import { readFile } from "node:fs/promises";
import { resolve } from "node:path";
import { buildHealthResponse } from "../../../lib/health-response.mjs";

export const dynamic = "force-static";

export async function GET() {
  const state = JSON.parse(await readFile(resolve(process.cwd(), "public/data/source-status.json"), "utf8"));
  const publication = JSON.parse(await readFile(resolve(process.cwd(), "public/data/v2-daily-brief.json"), "utf8"));
  return Response.json(buildHealthResponse(state, publication));
}
