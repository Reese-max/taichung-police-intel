import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

test("demo separates supplement receipt from unresolved registration gates (#163)", async () => {
  const docs = new URL("../../../docs/govintel/competition-2026/", import.meta.url);
  const demo = await readFile(new URL("DEMO_SCRIPT.md", docs), "utf8");
  const checklist = await readFile(new URL("ACCEPTANCE_CHECKLIST.md", docs), "utf8");
  assert.match(demo, /補件收妥/);
  assert.match(demo, /未接受更名|更名未被接受/);
  assert.doesNotMatch(demo, /主辦受理／更名仍未確認/);
  assert.match(demo, /原報名完整題名.*代表資格.*封面.*重交.*待確認/);
  assert.match(demo, /\]\(registration-verification-20261006\.md\)/);
  assert.match(checklist, /^- \[ \] #163 .*補件收妥不代表全部核准。$/m);
});
