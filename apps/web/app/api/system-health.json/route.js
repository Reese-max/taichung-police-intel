import { readFile } from "node:fs/promises";
import { resolve } from "node:path";

// Machine-readable end-to-end health receipt (#35). It serves the same saved
// artifact the dashboard renders, so operators can read the stage model, the
// measurement-only SLO fields and the upstream operating state without parsing
// the UI. Unknown stays unknown here too: nothing is recomputed at request time.
export const dynamic = "force-static";

export async function GET() {
  const health = JSON.parse(
    await readFile(resolve(process.cwd(), "public/data/system-health.json"), "utf8"),
  );
  return Response.json(health);
}
