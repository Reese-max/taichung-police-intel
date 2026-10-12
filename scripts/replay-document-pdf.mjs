// Offline bytes adapter and fixture-only CLI. No collector, provider or HTTP route.
import { createHash } from "node:crypto";
import { spawn } from "node:child_process";
import { readFile } from "node:fs/promises";
import { fileURLToPath, pathToFileURL } from "node:url";
import {
  createDocumentEvidenceStore, canonicalDocumentJson,
  DOCUMENT_EVIDENCE_LIMITS as E,
} from "../workers/query-gateway/src/document-evidence.js";

export const PDF_LIMITS = Object.freeze({ binaryBytes: 8388608, corpusBinaryBytes: 33554432,
  pages: 32, receiptBytes: 16384, replayBytes: 16384, timeoutMs: 12000, outputBytes: 524288, stderrBytes: 16384 });
export const PDF_PARSER_VERSION = "pdf-text-v1:pypdf-6.19.0";
const SEP = "\n\f\n";
// Closed 30-code-point set, mirrored in document_pdf.py; never normalize text.
const BLANK_TEXT_CODEPOINTS = new Set([
  0x0009, 0x000a, 0x000b, 0x000c, 0x000d, 0x001c, 0x001d, 0x001e, 0x001f, 0x0020,
  0x0085, 0x00a0, 0x1680, 0x2000, 0x2001, 0x2002, 0x2003, 0x2004, 0x2005,
  0x2006, 0x2007, 0x2008, 0x2009, 0x200a, 0x2028, 0x2029, 0x202f, 0x205f, 0x3000, 0xfeff,
]);
export function hasPdfText(text) {
  for (const char of text) if (!BLANK_TEXT_CODEPOINTS.has(char.codePointAt(0))) return true;
  return false;
}
const sha = value => createHash("sha256").update(value).digest("hex");
const sum = value => sha(canonicalDocumentJson(value));
const fail = code => { throw Object.assign(new Error(code), { code }); };
const shape = (value, keys, code) => {
  if (!value || Object.getPrototypeOf(value) !== Object.prototype ||
      Object.keys(value).length !== keys.length || keys.some(key => !Object.hasOwn(value, key))) fail(code);
};
const freeze = value => { if (value && typeof value === "object") { Object.values(value).forEach(freeze); Object.freeze(value); } return value; };
const boundedClone = (value, cap, code) => {
  let json;
  try { json = JSON.stringify(value); } catch { fail(code); }
  if (typeof json !== "string" || Buffer.byteLength(json) > cap) fail(code);
  return JSON.parse(json);
};

function verifyExtraction(binary, extraction, metadata, pin) {
  shape(extraction, ["text", "receipt"], "PDF_INVALID_EXTRACTION");
  const { text, receipt: r } = extraction;
  if (typeof text !== "string" || !text.isWellFormed() || Buffer.byteLength(text) > E.documentBytes) fail("PDF_TEXT_LIMIT_EXCEEDED");
  shape(r, ["schema_version", "extractor_version", "parser", "binary_sha256", "binary_bytes",
    "page_count", "page_separator", "offset_basis", "content_sha256", "text_bytes", "pages", "gaps", "receipt_sha256"], "PDF_INVALID_RECEIPT");
  shape(r.parser, ["name", "version"], "PDF_INVALID_RECEIPT");
  if (r.schema_version !== 1 || r.extractor_version !== "document-pdf/1" || r.parser.name !== "pypdf" ||
      r.parser.version !== "6.19.0" || metadata.parser_version !== PDF_PARSER_VERSION) fail("PDF_PARSER_VERSION_MISMATCH");
  shape(pin, ["document_binding_sha256", "binary_sha256", "receipt_sha256"], "PDF_EXTRACTION_PIN_REQUIRED");
  for (const hash of Object.values(pin)) if (typeof hash !== "string" || !/^[a-f0-9]{64}$/.test(hash)) fail("PDF_EXTRACTION_PIN_REQUIRED");
  const { receipt_sha256, ...core } = r;
  if (r.binary_bytes !== binary.length || r.binary_sha256 !== sha(binary) || r.binary_sha256 !== pin.binary_sha256) fail("PDF_BINARY_HASH_MISMATCH");
  if (receipt_sha256 !== sum(core) || receipt_sha256 !== pin.receipt_sha256) fail("PDF_RECEIPT_HASH_MISMATCH");
  if (pin.document_binding_sha256 !== sum(metadata)) fail("PDF_DOCUMENT_BINDING_MISMATCH");
  if (r.content_sha256 !== sha(text) || metadata.content_sha256 !== r.content_sha256 || r.text_bytes !== Buffer.byteLength(text)) fail("PDF_CONTENT_HASH_MISMATCH");
  if (r.page_separator !== SEP || r.offset_basis !== "EXACT_TEXT_UTF16_AND_UTF8_END_EXCLUSIVE" ||
      !Number.isSafeInteger(r.page_count) || r.page_count < 1 || r.page_count > PDF_LIMITS.pages ||
      !Array.isArray(r.pages) || r.pages.length !== r.page_count) fail("PDF_INVALID_PAGE_MAP");
  let utf16 = 0, utf8 = 0;
  const gaps = [];
  for (const [index, p] of r.pages.entries()) {
    shape(p, ["page_number", "status", "start_utf16", "end_utf16", "start_utf8", "end_utf8", "text_sha256"], "PDF_INVALID_PAGE_MAP");
    if (index) {
      if (text.slice(utf16, utf16 + SEP.length) !== SEP) fail("PDF_INVALID_PAGE_MAP");
      utf16 += SEP.length; utf8 += Buffer.byteLength(SEP);
    }
    if (![p.start_utf16, p.end_utf16, p.start_utf8, p.end_utf8].every(Number.isSafeInteger) ||
        p.page_number !== index + 1 || p.start_utf16 !== utf16 || p.start_utf8 !== utf8 ||
        p.end_utf16 < utf16 || p.end_utf16 > text.length) fail("PDF_INVALID_PAGE_MAP");
    const pageText = text.slice(utf16, p.end_utf16);
    if (!pageText.isWellFormed() || p.end_utf8 !== utf8 + Buffer.byteLength(pageText) ||
        p.text_sha256 !== sha(pageText) || p.status !== (hasPdfText(pageText) ? "TEXT" : "NO_EXTRACTABLE_TEXT")) fail("PDF_INVALID_PAGE_MAP");
    if (p.status === "NO_EXTRACTABLE_TEXT") gaps.push({ code: "PDF_PAGE_NO_EXTRACTABLE_TEXT", page_number: p.page_number });
    utf16 = p.end_utf16; utf8 = p.end_utf8;
  }
  if (utf16 !== text.length || utf8 !== r.text_bytes || canonicalDocumentJson(gaps) !== canonicalDocumentJson(r.gaps)) fail("PDF_INVALID_PAGE_MAP");
  if (!r.pages.some(p => p.status === "TEXT")) fail("PDF_NO_EXTRACTABLE_TEXT");
}

/** Trusted offline inputs only. Permissions and all independent pins are required.
 * No permission hashes are minted or rewritten by this adapter.
 */
export async function createDocumentPdfReplay({ evidenceConfig, pdfs, expectedExtractionPins }) {
  if (!Array.isArray(pdfs) || pdfs.length > E.documents) fail("DOCUMENT_LIMIT_EXCEEDED");
  if (!Array.isArray(expectedExtractionPins) || expectedExtractionPins.length !== pdfs.length) fail("PDF_EXTRACTION_PIN_REQUIRED");
  if (!evidenceConfig || Object.hasOwn(evidenceConfig, "documents")) fail("PDF_INVALID_EVIDENCE_CONFIG");
  const documents = [], receipts = new Map();
  let totalBinary = 0;
  for (const [index, raw] of pdfs.entries()) {
    shape(raw, ["metadata", "binary", "extraction"], "PDF_INVALID_INPUT");
    if (!(raw.binary instanceof Uint8Array)) fail("PDF_BYTES_REQUIRED");
    if (raw.binary.byteLength > PDF_LIMITS.binaryBytes) fail("PDF_BINARY_LIMIT_EXCEEDED");
    totalBinary += raw.binary.byteLength;
    if (totalBinary > PDF_LIMITS.corpusBinaryBytes) fail("PDF_BINARY_CORPUS_LIMIT_EXCEEDED");
    const binary = Buffer.from(raw.binary);
    if (binary.subarray(0, 5).toString() !== "%PDF-") fail("PDF_INVALID");
    const metadata = boundedClone(raw.metadata, E.policyBytes, "PDF_INVALID_METADATA");
    const extraction = boundedClone(raw.extraction, PDF_LIMITS.outputBytes, "PDF_INVALID_EXTRACTION");
    boundedClone(extraction.receipt, PDF_LIMITS.receiptBytes, "PDF_RECEIPT_LIMIT_EXCEEDED");
    const pin = boundedClone(expectedExtractionPins[index], 1024, "PDF_EXTRACTION_PIN_REQUIRED");
    verifyExtraction(binary, extraction, metadata, pin);
    if (receipts.has(pin.document_binding_sha256)) fail("DUPLICATE_DOCUMENT_VERSION");
    documents.push({ ...metadata, text: extraction.text });
    receipts.set(pin.document_binding_sha256, freeze(extraction));
  }
  // The existing store validates admission, per-document permissions, independent
  // policy pins, text hashes, 16 docs / 64 KiB each / 256 KiB corpus and expiry.
  const store = await createDocumentEvidenceStore({ ...evidenceConfig, documents });
  async function compileDraft(retrieval, candidate) {
    const compilation = await store.compileDraft(retrieval, candidate);
    const citations = [], gaps = [];
    for (const [binding, extraction] of receipts) {
      gaps.push(...extraction.receipt.gaps.map(gap => ({ ...gap, document_binding_sha256: binding,
        binary_sha256: extraction.receipt.binary_sha256, receipt_sha256: extraction.receipt.receipt_sha256 })));
    }
    for (const { quote, citation: c } of compilation.excerpts) {
      if (!hasPdfText(quote)) fail("PDF_QUOTE_HAS_NO_TEXT");
      const { text, receipt: r } = receipts.get(c.document_binding_sha256);
      const pages = r.pages.filter(p => p.status === "TEXT" && p.end_utf16 > p.start_utf16 && p.start_utf16 < c.quote_end_utf16 && p.end_utf16 > c.quote_start_utf16)
        .map(p => {
          const start = Math.max(p.start_utf16, c.quote_start_utf16), end = Math.min(p.end_utf16, c.quote_end_utf16);
          const start8 = Buffer.byteLength(text.slice(0, start)), end8 = Buffer.byteLength(text.slice(0, end));
          return { page_number: p.page_number, start_utf16: start, end_utf16: end, start_utf8: start8, end_utf8: end8,
            page_start_utf16: start - p.start_utf16, page_end_utf16: end - p.start_utf16,
            page_start_utf8: start8 - p.start_utf8, page_end_utf8: end8 - p.start_utf8 };
        });
      if (!pages.length) fail("PDF_QUOTE_NO_EXTRACTABLE_PAGE");
      if (r.pages.slice(1).some(p => p.start_utf16 - SEP.length < c.quote_end_utf16 && p.start_utf16 > c.quote_start_utf16)) {
        gaps.push({ code: "PDF_QUOTE_INCLUDES_PAGE_SEPARATOR", citation_sha256: c.citation_sha256 });
      }
      if (r.pages.some(p => p.status !== "TEXT" && p.start_utf16 < c.quote_end_utf16 && p.end_utf16 > c.quote_start_utf16)) {
        gaps.push({ code: "PDF_QUOTE_INCLUDES_UNEXTRACTABLE_PAGE_TEXT", citation_sha256: c.citation_sha256 });
      }
      citations.push({ citation_sha256: c.citation_sha256, document_binding_sha256: c.document_binding_sha256,
        binary_sha256: r.binary_sha256, receipt_sha256: r.receipt_sha256, pages });
    }
    const core = { schema_version: 1, kind: "PDF_QUOTE_REPLAY", compilation_sha256: compilation.compilation_sha256,
      citations, gaps, ocr_performed: false,
      text_completeness: [...receipts.values()].some(e => e.receipt.gaps.length) ? "INCOMPLETE" : "EXTRACTED_TEXT_ONLY" };
    const pdfReplay = freeze({ ...core, replay_sha256: sum(core) });
    if (Buffer.byteLength(JSON.stringify(pdfReplay)) > PDF_LIMITS.replayBytes) fail("PDF_REPLAY_LIMIT_EXCEEDED");
    store.assertPermissionsCurrent();
    return freeze({ compilation, pdf_replay: pdfReplay });
  }
  return Object.freeze({ retrieve: store.retrieve, compileDraft, assertPermissionsCurrent: store.assertPermissionsCurrent });
}

// Only this closed allowlist is readable by the CLI. No arbitrary PDF path/URL.
const FIXTURES = new URL("../tests/fixtures/document-pdf/", import.meta.url);
export async function extractFixturePdf(name, python) {
  if (!["two-page.pdf", "mixed.pdf", "encrypted.pdf", "corrupt.pdf", "too-many-pages.pdf", "too-much-text.pdf",
    "unicode-spaces.pdf", "blank-only.pdf", "scan-only.pdf", "whitespace-only.pdf", "page-failure.pdf",
    "empty-password.pdf", "many-text-pages.pdf"].includes(name)) fail("PDF_FIXTURE_REQUIRED");
  if (typeof python !== "string" || !python) fail("PDF_PYTHON_REQUIRED");
  const binary = await readFile(new URL(name, FIXTURES));
  return new Promise((resolve, reject) => {
    const child = spawn(python, ["-I", "-B", fileURLToPath(new URL("../intel_v2/document_pdf.py", import.meta.url)), "--stdin"], { shell: false, windowsHide: true });
    const chunks = []; let count = 0, stderr = 0, error = null;
    const stop = code => { error ??= Object.assign(new Error(code), { code }); child.kill(); };
    const timer = setTimeout(() => stop("PDF_PARSER_TIMEOUT"), PDF_LIMITS.timeoutMs);
    child.stdout.on("data", data => { count += data.length; if (count > PDF_LIMITS.outputBytes) stop("PDF_PARSER_OUTPUT_LIMIT_EXCEEDED"); else chunks.push(data); });
    child.stderr.on("data", data => { stderr += data.length; if (stderr > PDF_LIMITS.stderrBytes) stop("PDF_PARSER_OUTPUT_LIMIT_EXCEEDED"); });
    child.stdin.on("error", () => {});
    child.on("error", () => { clearTimeout(timer); reject(Object.assign(new Error("PDF_PARSER_UNAVAILABLE"), { code: "PDF_PARSER_UNAVAILABLE" })); });
    child.on("close", code => {
      clearTimeout(timer);
      if (error) return reject(error);
      if (code !== 0) return reject(Object.assign(new Error("PDF_PARSER_FAILED"), { code: "PDF_PARSER_FAILED" }));
      try {
        const extraction = JSON.parse(Buffer.concat(chunks).toString("utf8"));
        if (extraction.error) return reject(Object.assign(new Error(extraction.error), { code: extraction.error }));
        resolve({ binary, extraction });
      } catch { reject(Object.assign(new Error("PDF_PARSER_FAILED"), { code: "PDF_PARSER_FAILED" })); }
    });
    child.stdin.end(binary);
  });
}

export async function loadFixtureTrust() {
  const trust = JSON.parse(await readFile(new URL("trust.json", FIXTURES), "utf8"));
  if (trust.fixture_type !== "ORIGINAL_SYNTHETIC_OFFLINE_ONLY") fail("PDF_FIXTURE_REQUIRED");
  return trust;
}

async function main(args) {
  if (args.length !== 2 || args[0] !== "--python") fail("Usage: node scripts/replay-document-pdf.mjs --python <existing trusted interpreter>");
  const trust = await loadFixtureTrust(), { binary, extraction } = await extractFixturePdf("two-page.pdf", args[1]);
  const replay = await createDocumentPdfReplay({ evidenceConfig: { ...trust.evidenceConfig, clock: () => trust.as_of },
    pdfs: [{ metadata: trust.metadata, binary, extraction }], expectedExtractionPins: trust.extractionPins });
  const retrieval = await replay.retrieve({ query: "offline answer" });
  const passage = retrieval.passages.find(row => row.text.includes(trust.quote));
  if (!passage) fail("PDF_FIXTURE_ANSWER_MISSING");
  const start = passage.text.indexOf(trust.quote);
  const result = await replay.compileDraft(retrieval, { schema_version: 1, generation_id: retrieval.generation_id,
    excerpts: [{ passage_id: passage.passage_id, start_utf16: start, end_utf16: start + trust.quote.length, quote: trust.quote }] });
  console.log(JSON.stringify({ fixture_type: trust.fixture_type, network_calls: 0, provider_calls: 0, extraction, ...result }, null, 2));
}

if (process.argv[1] && pathToFileURL(process.argv[1]).href === import.meta.url) {
  main(process.argv.slice(2)).catch(error => { console.error(error.code ?? "PDF_REPLAY_FAILED"); process.exitCode = 1; });
}
