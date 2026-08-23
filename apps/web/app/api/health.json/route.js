import { readFile } from "node:fs/promises";
import { resolve } from "node:path";

export const dynamic = "force-static";

export async function GET() {
  const state = JSON.parse(await readFile(resolve(process.cwd(), "public/data/source-status.json"), "utf8"));
  if (state.mode !== "COMPETITION_DEMO" || state.sources?.length !== 5) throw new Error("invalid demo state");
  return Response.json({
    status: "ok",
    mode: state.mode,
    generated_at: state.generated_at,
    sources: state.sources.length,
    failed_sources: state.sources.filter((source) => source.source_health === "FAILED").length,
  });
}
