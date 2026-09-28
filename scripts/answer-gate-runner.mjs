#!/usr/bin/env node

import { createHash } from "node:crypto";

import { gateAnswer } from "../apps/web/lib/answer-evidence-gate.js";

const HEX64 = /^[0-9a-f]{64}$/;
const RENDERER_VERSION = "controlled-answer-renderer/1";
const FACTUAL_TYPES = new Set(["TIME", "LOCATION", "STATUS", "CAUSE", "STATISTIC", "AGENCY"]);

function hash(value) {
  return createHash("sha256").update(JSON.stringify(value), "utf8").digest("hex");
}

function propositions(raw) {
  const values = Array.isArray(raw)
    ? raw
    : raw && typeof raw === "object"
      ? [raw]
      : [];
  return values
    .filter((value) => value && typeof value === "object" && value.subject !== undefined && value.value !== undefined)
    .map((value) => `${String(value.subject)}=${String(value.value)}`);
}

function controlledText(entry) {
  if (!FACTUAL_TYPES.has(entry?.claim_type)) return null;
  const facts = propositions(entry.propositions);
  if (!facts.length) return null;
  const subjects = facts.map((fact) => fact.split("=")[0]).join("、");
  if (entry.support_status === "CONFLICT") {
    const values = (entry.conflict_values || []).map((value) => `${value.source_id}：${value.value}`);
    return `官方來源對 ${subjects} 記載不一致${values.length ? `：${values.join("；")}` : ""}。`;
  }
  if (entry.support_status === "STALE") return "官方資料可能已過期，未作為目前情況回答。";
  if (entry.support_status === "PARTIAL") return "官方來源僅部分支持，未核對部分不納入回答。";
  if (entry.support_status === "UNSUPPORTED") return "未找到可驗證的官方證據，此項說法已移除。";
  return `官方來源已核對：${facts.join("；")}。`;
}

async function readInput() {
  const chunks = [];
  for await (const chunk of process.stdin) chunks.push(chunk);
  return JSON.parse(Buffer.concat(chunks).toString("utf8"));
}

const input = await readInput();
if (!input || typeof input !== "object" || !Array.isArray(input.claims) || !Array.isArray(input.evidence)) {
  throw new Error("server-bound answer gate input is invalid");
}
if (input.publication_hash !== undefined && !HEX64.test(input.publication_hash)) {
  throw new Error("publication_hash must be a SHA-256 hex digest");
}
if (input.evidence_catalog_hash !== undefined && !HEX64.test(input.evidence_catalog_hash)) {
  throw new Error("evidence_catalog_hash must be a SHA-256 hex digest");
}

const result = gateAnswer({
  claims: input.claims,
  evidence: input.evidence,
  generated_at: input.generated_at,
});
const receiptClaims = Array.isArray(result.receipt?.claims) ? result.receipt.claims : [];
const finalClaims = receiptClaims.map((entry) => ({
  claim_id: entry.claim_id,
  claim_type: entry.claim_type,
  support_status: entry.support_status,
  text: controlledText(entry),
}));
const answer = finalClaims.map((entry) => entry.text).filter(Boolean);
const receipt = {
  ...result.receipt,
  publication_hash: input.publication_hash ?? result.receipt?.publication_hash ?? null,
  evidence_catalog_hash: input.evidence_catalog_hash ?? null,
  renderer_version: RENDERER_VERSION,
  answer_sha256: hash(answer),
};

process.stdout.write(JSON.stringify({
  schema_version: 1,
  gate_status: result.gate_status,
  final_claims: finalClaims,
  answer,
  receipt,
}));
