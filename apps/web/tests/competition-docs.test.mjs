import assert from "node:assert/strict";
import { existsSync, readFileSync, readdirSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import test from "node:test";

const root = fileURLToPath(new URL("../../../", import.meta.url));
const competition = "docs/govintel/competition-2026/";
const pages = readdirSync(resolve(root, competition)).filter((name) => name.endsWith(".md"))
  .map((name) => `${competition}${name}`);
const read = (path) => readFileSync(resolve(root, path), "utf8");

test("judge entry links resolve locally, including preserved historical evidence", () => {
  const broken = [];
  for (const page of ["README.md", ...pages, "docs/govintel/historical/README.md"]) {
    for (const [, target] of read(page).matchAll(/\[[^\]\r\n]*\]\(([^\s)]+)\)/g)) {
      if (/^(?:[a-z][a-z0-9+.-]*:|#|\/\/)/i.test(target)) continue;
      const path = decodeURIComponent(target.split(/[?#]/)[0]);
      if (!existsSync(resolve(root, dirname(page), path))) broken.push(`${page}: ${target}`);
    }
  }
  assert.deepEqual(broken, []);
});

test("claim table uses all five fixed statuses and keeps publication blockers visible", () => {
  const status = read(`${competition}CURRENT_STATUS.md`);
  const rows = status.split("\n").filter((line) => /^\|.*\| `(?:PRODUCTION_ACTIVE|IMPLEMENTED_NOT_PRODUCTION|CANDIDATE_CANARY|DESIGN_ONLY|BLOCKED)` \|/.test(line));
  for (const state of ["PRODUCTION_ACTIVE", "IMPLEMENTED_NOT_PRODUCTION", "CANDIDATE_CANARY", "DESIGN_ONLY", "BLOCKED"]) {
    assert.ok(rows.some((line) => line.includes(`\`${state}\``)), `Missing claim/evidence status: ${state}`);
  }
  for (const claim of ["五來源", "collector", "Twinkle", "latest-information", "追蹤", "融合", "#20"]) {
    assert.ok(rows.some((line) => line.includes(claim)), `Missing claim/evidence row: ${claim}`);
  }
  for (const page of ["README.md", `${competition}LIMITATIONS_AND_SAFETY.md`]) {
    assert.match(read(page), /#20/);
    assert.match(read(page), /RIGHTS_BLOCKED/);
  }
});

test("four-minute demo specifies a built loopback replay and every saved snapshot", () => {
  const demo = read(`${competition}DEMO_SCRIPT.md`);
  assert.match(demo, /npm --prefix apps\/web run build/);
  assert.match(demo, /python3 -m http\.server 8000 --bind 127\.0\.0\.1 --directory apps\/web\/out/);
  for (const label of ["合成通勤示例 · 保存快照重播", "合成示例篩選條件（僅在示例中可用）", "合成通勤重播（與真實清單隔離）", "重播資料截止"]) {
    assert.ok(demo.includes(label), `Missing replay control: ${label}`);
  }
  const bundle = JSON.parse(read("apps/web/public/data/public-query-replay.json"));
  for (const id of Object.keys(bundle.snapshots)) {
    assert.match(demo, new RegExp(`^\\| ${id} \\|`, "m"), `Missing saved snapshot: ${id}`);
  }
  assert.match(demo.match(/^\| R8 \|.*$/m)?.[0] || "", /回公開查詢.*合成 R8.*清空路段／期間/);
  assert.doesNotMatch(demo, /已簽章/);
  assert.match(read(`${competition}JUDGE_PATH.md`), /scripts\/replay-commute-reopen\.py --self-check/);
  assert.match(read(`${competition}JUDGE_PATH.md`), /scripts\/e2e-v6-browser\.mjs --base-url http:\/\/127\.0\.0\.1:8000 --out runtime-evidence\/judge-replay/);
});

test("current overview and demo agree on dated deployment and organizer evidence", () => {
  const status = read(`${competition}CURRENT_STATUS.md`);
  for (const pattern of [/文件核對日：(\d{4}-\d{2}-\d{2})/, /main@([a-f0-9]{40})/, /\[PR #\d+ 驗收包\]\(([^)]+)\)/]) {
    const match = status.match(pattern);
    assert.ok(match, `Missing dated public evidence: ${pattern}`);
    assert.ok(read("README.md").includes(match[1]), `Overview differs from current status: ${match[1]}`);
  }
  const demo = read(`${competition}DEMO_SCRIPT.md`);
  assert.match(demo, /補件收妥/);
  assert.match(demo, /更名未接受/);
  assert.doesNotMatch(demo, /主辦受理／更名仍未確認/);
  assert.doesNotMatch(read(`${competition}ARCHITECTURE.md`), /PR124 query、125 tracking、126 replay 在驗收中/);
});
