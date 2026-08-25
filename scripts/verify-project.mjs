import { spawnSync } from "node:child_process";
import { existsSync } from "node:fs";
import { readFile, readdir, stat } from "node:fs/promises";
import { dirname, extname, relative, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const mode = process.argv[2] || "gate0";
const modes = new Set(["gate0", "specs", "quick", "full"]);
if (!modes.has(mode)) throw new Error(`Unknown verification mode: ${mode}`);

const failures = [];
const required = [
  ".git",
  ".gitignore",
  ".env.example",
  "README.md",
  "package.json",
  "requirements.txt",
  "collect.py",
  "online_collect.py",
  ".github/workflows/pages.yml",
  "SUBMISSION.md",
  "docs/DEMO_SCRIPT.md",
  "docs/SUBMISSION_CHECKLIST.md",
  "docs/KIRO_USAGE.md",
  "migrations/0001_ingestion_core.sql",
  "migrations/0002_source_snapshots.sql",
  "apps/web/scripts/migrate.mjs",
  "apps/web/next.config.mjs",
  "apps/web/public/data/source-status.json",
  "apps/web/public/data/intelligence-feed.json",
  "apps/web/app/api/health.json/route.js",
  "apps/web/app/api/status.json/route.js",
  "evaluation/ingestion-record.schema.json",
  "evaluation/ingestion-record.example.json",
  "tests/test_source_ingestion.py",
  ".kiro/steering/product.md",
  ".kiro/steering/tech.md",
  ".kiro/steering/structure.md",
  ".kiro/steering/evidence-and-safety.md",
  ".kiro/hooks/check-on-save.json",
  ".kiro/hooks/spec-ready-gate.json",
  ".kiro/hooks/task-verification.json",
];
const specNames = [
  "source-ingestion-and-provenance",
  "five-minute-homepage",
  "council-prep-and-evidence",
  "live-intelligence-feed",
];
for (const spec of specNames) {
  for (const artifact of ["requirements.md", "design.md", "tasks.md"]) {
    required.push(`.kiro/specs/${spec}/${artifact}`);
  }
}

for (const path of required) {
  if (!existsSync(resolve(root, path))) failures.push(`missing:${path}`);
}

async function read(path) {
  return readFile(resolve(root, path), "utf8");
}

if (!failures.length) {
  const packageJson = JSON.parse(await read("package.json"));
  if (!packageJson.private) failures.push("package.json must remain private");

  for (const hookFile of ["check-on-save.json", "spec-ready-gate.json", "task-verification.json"]) {
    const hook = JSON.parse(await read(`.kiro/hooks/${hookFile}`));
    if (hook.version !== "v1" || !Array.isArray(hook.hooks) || !hook.hooks.length) {
      failures.push(`invalid-hook-schema:${hookFile}`);
      continue;
    }
    for (const item of hook.hooks) {
      if (!/^[A-Z][A-Za-z]+$/.test(item.trigger || "")) failures.push(`invalid-trigger:${hookFile}`);
      if (item.action?.type !== "command" || !item.action.command) failures.push(`invalid-action:${hookFile}`);
    }
  }

  const readme = await read("README.md");
  for (const heading of ["## Setup", "## Run", "## Verification", "## Kiro workflow", "## Costs and third parties", "## Limitations", "## License and data rights"]) {
    if (!readme.includes(heading)) failures.push(`README missing ${heading}`);
  }

  const workflow = await read(".github/workflows/pages.yml");
  if ((workflow.match(/^\s*- cron:/gm) || []).length !== 2) failures.push("pages:expected-two-schedules");
  for (const token of ["30 22 * * *", "30 10 * * *", "actions/configure-pages@v5", "actions/deploy-pages@v4", "--demo-output apps/web/public/data/source-status.json"]) {
    if (!workflow.includes(token)) failures.push(`pages:missing-${token}`);
  }

  const status = JSON.parse(await read("apps/web/public/data/source-status.json"));
  if (status.mode !== "COMPETITION_DEMO") failures.push("demo-status:invalid-mode");
  if (status.sources?.length !== 5) failures.push("demo-status:expected-five-sources");
  if (!(Date.parse(status.next_update_at) > Date.parse(status.generated_at))) failures.push("demo-status:next-update-not-future");
  for (const source of status.sources || []) {
    if (!/^https:\/\//.test(source.source_url || "")) failures.push(`demo-status:${source.source_id}:invalid-url`);
    if (!/^[0-9a-f]{64}$/.test(source.manifest_sha256 || "")) failures.push(`demo-status:${source.source_id}:invalid-manifest`);
    if (!Array.isArray(source.intelligence_gaps)) failures.push(`demo-status:${source.source_id}:invalid-gaps`);
    if (source.source_health === "PASS" && !source.last_known_good?.source_run_id) failures.push(`demo-status:${source.source_id}:missing-lkg`);
  }

  const feed = JSON.parse(await read("apps/web/public/data/intelligence-feed.json"));
  if (feed.schema_version !== 1) failures.push("feed:invalid-schema-version");
  if (!Array.isArray(feed.items)) failures.push("feed:items-not-array");
  if (!feed.generated_at) failures.push("feed:missing-generated-at");
  if (!feed.source_summary) failures.push("feed:missing-source-summary");
  for (const item of feed.items || []) {
    if (!item.stable_id) failures.push(`feed:item-missing-stable-id`);
    if (!/^https:\/\//.test(item.official_url || "")) failures.push(`feed:${item.stable_id || "unknown"}:invalid-url`);
    if (item.freshness_status === "VERY_STALE" && item.eligibility === "HOME_CANDIDATE") {
      failures.push(`feed:${item.stable_id}:stale-marked-eligible`);
    }
    if (item.freshness_status === "NO_DATA" && item.eligibility === "HOME_CANDIDATE") {
      failures.push(`feed:${item.stable_id}:nodata-marked-eligible`);
    }
    if (item.change_type === "UNCHANGED" && item.eligibility === "HOME_CANDIDATE") {
      failures.push(`feed:${item.stable_id}:unchanged-marked-eligible`);
    }
    if (item.window_completeness === "PARTIAL" && item.eligibility === "HOME_CANDIDATE") {
      failures.push(`feed:${item.stable_id}:partial-marked-eligible`);
    }
  }
}

function run(program, args, label, capture = false) {
  const result = spawnSync(program, args, {
    cwd: root,
    encoding: "utf8",
    env: { ...process.env, PYTHONUTF8: "1" },
    stdio: capture ? "pipe" : "inherit",
  });
  if (result.error || result.status !== 0) failures.push(`${label}:exit=${result.status ?? "spawn-error"}`);
  return result;
}

function runNpm(args, label) {
  if (process.platform === "win32") {
    return run(process.env.ComSpec || "cmd.exe", ["/d", "/s", "/c", `npm ${args.join(" ")}`], label);
  }
  return run("npm", args, label);
}

const git = run("git", ["rev-parse", "--is-inside-work-tree"], "git-root", true);
if (git.status === 0 && git.stdout.trim() !== "true") failures.push("git-root:not-inside-work-tree");
const ignoredKiro = spawnSync("git", ["check-ignore", "-q", ".kiro/steering/product.md"], { cwd: root, stdio: "ignore" });
if (ignoredKiro.status === 0) failures.push(".kiro must not be ignored");
else if (ignoredKiro.status !== 1) failures.push(`kiro-ignore-probe:exit=${ignoredKiro.status ?? "spawn-error"}`);

const excludedDirs = new Set([".git", ".next", ".gstack", "node_modules", "__pycache__", "graphify-out"]);
const textExtensions = new Set([".css", ".env", ".html", ".js", ".json", ".md", ".mjs", ".py", ".sql", ".toml", ".txt", ".yaml", ".yml"]);
const secretPatterns = [
  /-----BEGIN (?:RSA |OPENSSH |EC |DSA )?PRIVATE KEY-----/,
  /AKIA[0-9A-Z]{16}/,
  /AIza[0-9A-Za-z_-]{35}/,
  /github_pat_[0-9A-Za-z_]{20,}/,
  /ghp_[0-9A-Za-z]{30,}/,
  /(?:^|[^A-Za-z0-9])(?:sk-|gsk_)[0-9A-Za-z_-]{20,}/,
];

async function scanSecrets(directory) {
  for (const entry of await readdir(directory, { withFileTypes: true })) {
    if (entry.isDirectory() && excludedDirs.has(entry.name)) continue;
    const path = resolve(directory, entry.name);
    if (entry.isDirectory()) {
      await scanSecrets(path);
      continue;
    }
    if (!textExtensions.has(extname(entry.name).toLowerCase()) && ![".env", ".env.example"].includes(entry.name)) continue;
    if ((await stat(path)).size > 2_000_000) continue;
    const content = await readFile(path, "utf8");
    if (secretPatterns.some((pattern) => pattern.test(content))) failures.push(`secret-pattern:${relative(root, path)}`);
  }
}
await scanSecrets(root);

if (["specs", "quick", "full"].includes(mode) && !failures.length) {
  for (const spec of specNames) {
    const base = `.kiro/specs/${spec}`;
    const requirements = await read(`${base}/requirements.md`);
    const design = await read(`${base}/design.md`);
    const tasks = await read(`${base}/tasks.md`);
    if (!requirements.includes("THE SYSTEM SHALL")) failures.push(`${spec}:missing-EARS-requirement`);
    for (const contract of ["Deterministic checks", "Generator", "Verifier", "Failure states", "Retry limit", "Acceptance command"]) {
      if (!design.includes(contract)) failures.push(`${spec}:missing-${contract}`);
    }
    if (!tasks.includes("Acceptance:")) failures.push(`${spec}:missing-task-acceptance`);
  }
}

function findPython() {
  for (const candidate of process.platform === "win32" ? ["python", "py"] : ["python3", "python"]) {
    const args = candidate === "py" ? ["-3", "--version"] : ["--version"];
    if (spawnSync(candidate, args, { stdio: "ignore" }).status === 0) return { command: candidate, prefix: candidate === "py" ? ["-3"] : [] };
  }
  return null;
}

if (["quick", "full"].includes(mode) && !failures.length) {
  const python = findPython();
  if (!python) failures.push("python:not-found");
  runNpm(["--prefix", "apps/web", "test"], "web-tests");
  if (python) {
    const checks = [
      ["contract-tests", ["-X", "utf8", "-m", "unittest", "discover", "-s", "evaluation", "-p", "test_source_value_contract.py", "-v"]],
      ["ingestion-contract-tests", ["-X", "utf8", "-m", "unittest", "discover", "-s", "tests", "-p", "test_source_ingestion.py", "-v"]],
      ["audit-self-check", ["-X", "utf8", "build-seven-day-source-audit.py", "--self-check"]],
      ["canary-self-check", ["-X", "utf8", "canary-s026-s029.py", "--self-check"]],
      ["s028-165-self-check", ["-X", "utf8", "canary-s028-165.py", "--self-check"]],
      ["inventory-self-check", ["-X", "utf8", "inventory-ey-questions.py", "--self-check"]],
      ["asr-self-check", ["-X", "utf8", "groq-asr-canary.py", "--self-check"]],
      ["cer-self-check", ["-X", "utf8", "evaluation/evaluate-asr-cer.py", "--self-check"]],
      ["online-collector-self-check", ["-X", "utf8", "online_collect.py", "--self-check"]],
    ];
    for (const [label, args] of checks) run(python.command, [...python.prefix, ...args], label);
  }
  run("node", ["apps/web/scripts/migrate.mjs", "--self-check"], "migration-self-check");
  if (mode === "full") runNpm(["--prefix", "apps/web", "run", "build"], "web-build");
}

if (failures.length) {
  for (const failure of failures) console.error(`VERIFY_FAIL ${failure}`);
  process.exit(1);
}
console.log(`VERIFY_OK mode=${mode} required=${required.length} specs=${specNames.length} secrets=0`);
