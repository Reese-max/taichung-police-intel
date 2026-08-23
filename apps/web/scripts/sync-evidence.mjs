import { copyFile, mkdir, readFile } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const webRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const projectRoot = resolve(webRoot, "../..");
const outputDir = resolve(webRoot, "public/data");
const files = [
  resolve(projectRoot, "groq-asr-canary-2026-08-14.json"),
  resolve(projectRoot, "evaluation/groq-asr-reference-2026-08-14.json"),
  resolve(projectRoot, "evaluation/groq-asr-cer-2026-08-14.json"),
];

const canary = JSON.parse(await readFile(files[0], "utf8"));
const cer = JSON.parse(await readFile(files[2], "utf8"));
if (canary.status !== "PASS" || cer.status !== "PASS") {
  throw new Error("證據資料尚未通過 ASR 或 CER Gate，拒絕同步到前端。");
}

await mkdir(outputDir, { recursive: true });
for (const source of files) {
  await copyFile(source, resolve(outputDir, source.split(/[\\/]/).at(-1)));
}
console.log(`EVIDENCE_SYNC_OK segments=${canary.asr.segments.length} words=${canary.asr.words.length} cer=${cer.cer_percent}%`);
