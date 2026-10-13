"""Offline PDF bytes -> exact page text and integrity receipt. No OCR or I/O URLs.

The public entry point always parses in a bounded child process. Extraction is
not admission: the caller must retain separately approved document permissions.
"""
from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path
import subprocess
import sys
import threading

EXTRACTOR_VERSION = "document-pdf/1"
PARSER_VERSION = "6.19.0"
DOCUMENT_PARSER_VERSION = "pdf-text-v1:pypdf-6.19.0"
PAGE_SEPARATOR = "\n\f\n"
MAX_BINARY_BYTES = 8 * 1024 * 1024
MAX_PAGES = 32
MAX_TEXT_BYTES = 65536
PARSER_TIMEOUT_SECONDS = 10
MAX_OUTPUT_BYTES = 512 * 1024
MAX_STDERR_BYTES = 16384
# Closed cross-language set, mirrored exactly in replay-document-pdf.mjs.
# Classification only: preserve every original character and hash unchanged.
BLANK_TEXT_CODEPOINTS = frozenset([
    *range(0x0009, 0x000E), *range(0x001C, 0x0020), 0x0020,
    0x0085, 0x00A0, 0x1680, *range(0x2000, 0x200B),
    0x2028, 0x2029, 0x202F, 0x205F, 0x3000, 0xFEFF,
])


def has_pdf_text(text: str) -> bool:
    return any(ord(char) not in BLANK_TEXT_CODEPOINTS for char in text)


class DocumentPdfError(ValueError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def _sha(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def _canonical(value) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")


def _check_bytes(body: bytes) -> None:
    if not isinstance(body, bytes):
        raise DocumentPdfError("PDF_BYTES_REQUIRED")
    if len(body) > MAX_BINARY_BYTES:
        raise DocumentPdfError("PDF_BINARY_LIMIT_EXCEEDED")
    if not body.startswith(b"%PDF-"):
        raise DocumentPdfError("PDF_INVALID")


def _assemble(body: bytes, texts: list[str]) -> dict:
    if not texts or len(texts) > MAX_PAGES:
        raise DocumentPdfError("PDF_PAGE_LIMIT_EXCEEDED")
    pages, gaps, pieces = [], [], []
    utf8 = utf16 = 0
    for number, text in enumerate(texts, 1):
        if not isinstance(text, str):
            raise DocumentPdfError("PDF_PAGE_EXTRACTION_FAILED")
        if number > 1:
            pieces.append(PAGE_SEPARATOR)
            utf8 += len(PAGE_SEPARATOR.encode("utf-8"))
            utf16 += len(PAGE_SEPARATOR)
        try:
            raw = text.encode("utf-8")
            units = len(text.encode("utf-16-le")) // 2
        except UnicodeError as exc:
            raise DocumentPdfError("PDF_INVALID_TEXT") from exc
        if utf8 + len(raw) > MAX_TEXT_BYTES:
            raise DocumentPdfError("PDF_TEXT_LIMIT_EXCEEDED")
        status = "TEXT" if has_pdf_text(text) else "NO_EXTRACTABLE_TEXT"
        pages.append({"page_number": number, "status": status,
                      "start_utf8": utf8, "end_utf8": utf8 + len(raw),
                      "start_utf16": utf16, "end_utf16": utf16 + units,
                      "text_sha256": _sha(raw)})
        if status != "TEXT":
            gaps.append({"code": "PDF_PAGE_NO_EXTRACTABLE_TEXT", "page_number": number})
        pieces.append(text)
        utf8 += len(raw)
        utf16 += units
    if not any(page["status"] == "TEXT" for page in pages):
        raise DocumentPdfError("PDF_NO_EXTRACTABLE_TEXT")
    text = "".join(pieces)
    receipt = {"schema_version": 1, "extractor_version": EXTRACTOR_VERSION,
               "parser": {"name": "pypdf", "version": PARSER_VERSION},
               "binary_sha256": _sha(body), "binary_bytes": len(body),
               "page_count": len(pages), "page_separator": PAGE_SEPARATOR,
               "offset_basis": "EXACT_TEXT_UTF16_AND_UTF8_END_EXCLUSIVE",
               "content_sha256": _sha(text.encode("utf-8")), "text_bytes": utf8,
               "pages": pages, "gaps": gaps}
    receipt["receipt_sha256"] = _sha(_canonical(receipt))
    return {"text": text, "receipt": receipt}


def _parse(body: bytes) -> dict:
    _check_bytes(body)
    try:
        import pypdf
    except ImportError as exc:
        raise DocumentPdfError("PDF_PARSER_UNAVAILABLE") from exc
    if pypdf.__version__ != PARSER_VERSION:
        raise DocumentPdfError("PDF_PARSER_VERSION_MISMATCH")
    try:
        reader = pypdf.PdfReader(io.BytesIO(body), strict=True)
        # Even an empty-password PDF is rejected; no password acquisition.
        if reader.is_encrypted:
            raise DocumentPdfError("PDF_ENCRYPTED")
        count = len(reader.pages)
        if not count or count > MAX_PAGES:
            raise DocumentPdfError("PDF_PAGE_LIMIT_EXCEEDED")
        texts = []
        for page in reader.pages:
            try:
                texts.append(page.extract_text(extraction_mode="plain") or "")
            except Exception as exc:
                raise DocumentPdfError("PDF_PAGE_EXTRACTION_FAILED") from exc
            # Bound cumulative text as pages are read. Never return partial text.
            if sum(len(t.encode("utf-8")) for t in texts) + (len(texts) - 1) * 3 > MAX_TEXT_BYTES:
                raise DocumentPdfError("PDF_TEXT_LIMIT_EXCEEDED")
        return _assemble(body, texts)
    except DocumentPdfError:
        raise
    except Exception as exc:
        raise DocumentPdfError("PDF_INVALID") from exc


def _run_worker(body: bytes) -> bytes:
    process = subprocess.Popen(
        [sys.executable, "-I", "-B", str(Path(__file__).resolve()), "--worker"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        shell=False,
    )
    output, overflow = bytearray(), threading.Event()

    def drain(stream, cap, destination=None):
        count = 0
        while chunk := stream.read(4096):
            count += len(chunk)
            if count > cap:
                overflow.set()
                process.kill()
                break
            if destination is not None:
                destination.extend(chunk)
        stream.close()

    def feed():
        try:
            process.stdin.write(body)
            process.stdin.close()
        except (BrokenPipeError, OSError):
            process.stdin.close()

    threads = [threading.Thread(target=drain, args=(process.stdout, MAX_OUTPUT_BYTES, output)),
               threading.Thread(target=drain, args=(process.stderr, MAX_STDERR_BYTES)),
               threading.Thread(target=feed)]
    for thread in threads:
        thread.start()
    timed_out = False
    try:
        process.wait(timeout=PARSER_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        timed_out = True
        process.kill()
        process.wait()
    finally:
        for thread in threads:
            thread.join()
    if overflow.is_set():
        raise DocumentPdfError("PDF_PARSER_OUTPUT_LIMIT_EXCEEDED")
    if timed_out:
        raise DocumentPdfError("PDF_PARSER_TIMEOUT")
    if process.returncode != 0:
        raise DocumentPdfError("PDF_PARSER_FAILED")
    return bytes(output)


def extract_pdf_bytes(body: bytes) -> dict:
    """Accept only already-held bytes; fail whole-document on invalid/overlimit PDF."""
    _check_bytes(body)
    try:
        result = json.loads(_run_worker(body))
    except (json.JSONDecodeError, UnicodeError) as exc:
        raise DocumentPdfError("PDF_PARSER_FAILED") from exc
    if isinstance(result, dict) and set(result) == {"error"}:
        raise DocumentPdfError(result["error"])
    if not isinstance(result, dict) or set(result) != {"text", "receipt"}:
        raise DocumentPdfError("PDF_PARSER_FAILED")
    return result


if __name__ == "__main__":
    if sys.argv[1:] not in (["--worker"], ["--stdin"]):
        raise SystemExit("Usage: document_pdf.py --stdin (PDF bytes on stdin)")
    try:
        body = sys.stdin.buffer.read(MAX_BINARY_BYTES + 1)
        result = _parse(body) if sys.argv[1] == "--worker" else extract_pdf_bytes(body)
    except DocumentPdfError as error:
        result = {"error": error.code}
    sys.stdout.buffer.write(_canonical(result))
