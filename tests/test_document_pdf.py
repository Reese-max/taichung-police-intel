import hashlib
import importlib.util
import json
from pathlib import Path
import socket
import tempfile
import unittest
from unittest import mock

# Exercise the standalone bytes module without loading unrelated package
# semantics (and its existing platform timezone-data dependency).
spec = importlib.util.spec_from_file_location("govintel_document_pdf", Path(__file__).resolve().parents[1] / "intel_v2/document_pdf.py")
pdf = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pdf)

FIXTURES = Path(__file__).parent / "fixtures" / "document-pdf"
EXPECTED = json.loads((FIXTURES / "expected.json").read_text(encoding="utf-8"))
REVIEW = json.loads((FIXTURES / "review-expected.json").read_text(encoding="utf-8"))


class DocumentPdfCoreTests(unittest.TestCase):
    def test_originals_are_fixed_not_real_source_captures(self):
        self.assertEqual(EXPECTED["fixture_type"], "ORIGINAL_SYNTHETIC_OFFLINE_ONLY")
        for name, digest in EXPECTED["binary_sha256"].items():
            self.assertEqual(hashlib.sha256((FIXTURES / name).read_bytes()).hexdigest(), digest)

    def test_unicode_offsets_separator_and_fixed_receipt(self):
        extraction = EXPECTED["extractions"]["two-page.pdf"]
        self.assertEqual(pdf._assemble((FIXTURES / "two-page.pdf").read_bytes(), extraction["text"].split(pdf.PAGE_SEPARATOR)), extraction)
        text, receipt = extraction["text"], extraction["receipt"]
        raw, utf16 = text.encode(), text.encode("utf-16-le")
        first = receipt["pages"][0]
        self.assertNotIn("16:00", utf16[first["start_utf16"] * 2:first["end_utf16"] * 2].decode("utf-16-le"))
        for page in receipt["pages"]:
            page_text = utf16[page["start_utf16"] * 2:page["end_utf16"] * 2].decode("utf-16-le")
            self.assertEqual(raw[page["start_utf8"]:page["end_utf8"]].decode(), page_text)
            self.assertEqual(hashlib.sha256(page_text.encode()).hexdigest(), page["text_sha256"])
        self.assertIn("第二頁🚓", raw[receipt["pages"][1]["start_utf8"]:].decode())

    def test_blank_and_scanned_pages_have_honest_gaps(self):
        extraction = EXPECTED["extractions"]["mixed.pdf"]
        self.assertEqual(pdf._assemble((FIXTURES / "mixed.pdf").read_bytes(), ["", "Synthetic readable page 第二頁🚓", ""]), extraction)
        self.assertEqual([gap["page_number"] for gap in extraction["receipt"]["gaps"]], [1, 3])

    def assert_code(self, operation, code):
        with self.assertRaises(pdf.DocumentPdfError) as raised:
            operation()
        self.assertEqual(raised.exception.code, code)

    def test_fail_closed_bounds_no_silent_truncation(self):
        for body, code in [(bytearray(b"%PDF-"), "PDF_BYTES_REQUIRED"), (b"invalid", "PDF_INVALID"),
                           (b"%PDF-" + b"x" * pdf.MAX_BINARY_BYTES, "PDF_BINARY_LIMIT_EXCEEDED")]:
            with mock.patch.object(pdf, "_run_worker") as worker:
                self.assert_code(lambda: pdf.extract_pdf_bytes(body), code)
                worker.assert_not_called()
        for texts, code in [([], "PDF_PAGE_LIMIT_EXCEEDED"), ([""] * 33, "PDF_PAGE_LIMIT_EXCEEDED"),
                            (["a" * 65536, ""], "PDF_TEXT_LIMIT_EXCEEDED"), (["\ud800"], "PDF_INVALID_TEXT")]:
            self.assert_code(lambda: pdf._assemble(b"%PDF-", texts), code)
        self.assertEqual(len(pdf._assemble(b"%PDF-", ["a" * 65536])["text"].encode()), 65536)

    def worker_script(self, content):
        with tempfile.TemporaryDirectory(dir=FIXTURES, prefix=".worker-") as temp:
            self.assertTrue(Path(temp).resolve().is_relative_to(FIXTURES.resolve()))
            path = Path(temp) / "worker.py"
            path.write_text(content, encoding="utf-8")
            with mock.patch.object(pdf, "__file__", str(path)):
                return pdf._run_worker(b"%PDF-1.7")

    def test_subprocess_timeout_output_and_stderr_caps(self):
        with mock.patch.object(pdf, "PARSER_TIMEOUT_SECONDS", 0.15):
            self.assert_code(lambda: self.worker_script("import time; time.sleep(10)"), "PDF_PARSER_TIMEOUT")
        with mock.patch.object(pdf, "MAX_OUTPUT_BYTES", 128):
            self.assert_code(lambda: self.worker_script("import sys; sys.stdout.buffer.write(b'x'*8192)"), "PDF_PARSER_OUTPUT_LIMIT_EXCEEDED")
        with mock.patch.object(pdf, "MAX_STDERR_BYTES", 128):
            self.assert_code(lambda: self.worker_script("import sys; sys.stderr.buffer.write(b'x'*8192)"), "PDF_PARSER_OUTPUT_LIMIT_EXCEEDED")
        self.assert_code(lambda: self.worker_script("raise SystemExit(3)"), "PDF_PARSER_FAILED")

    def test_invalid_subprocess_json_is_not_evidence(self):
        for output in [b"invalid", b"{}", b"[]"]:
            with mock.patch.object(pdf, "_run_worker", return_value=output):
                self.assert_code(lambda: pdf.extract_pdf_bytes(b"%PDF-1.7"), "PDF_PARSER_FAILED")

    def test_pinned_version_required_no_network_on_failure(self):
        fake = type("Parser", (), {"__version__": "6.10.0"})()
        with mock.patch.dict("sys.modules", {"pypdf": fake}), mock.patch.object(socket, "socket", side_effect=AssertionError("network forbidden")) as network:
            self.assert_code(lambda: pdf._parse(b"%PDF-1.7"), "PDF_PARSER_VERSION_MISMATCH")
            network.assert_not_called()


class DocumentPdfParserAcceptanceTests(unittest.TestCase):
    assert_code = DocumentPdfCoreTests.assert_code
    # No skips or substitute parser: these fail explicitly if pinned 6.19.0 is
    # unavailable. Run this class for real parser acceptance, not just core tests.
    def test_actual_pinned_parser_matches_fixed_page_maps(self):
        with mock.patch.object(socket, "socket", side_effect=AssertionError("network forbidden")):
            for name, expected in EXPECTED["extractions"].items():
                with self.subTest(name=name):
                    self.assertEqual(pdf.extract_pdf_bytes((FIXTURES / name).read_bytes()), expected)

    def test_actual_pinned_parser_failures(self):
        for name, code in [("encrypted.pdf", "PDF_ENCRYPTED"), ("corrupt.pdf", "PDF_INVALID"),
                           ("too-many-pages.pdf", "PDF_PAGE_LIMIT_EXCEEDED"), ("too-much-text.pdf", "PDF_TEXT_LIMIT_EXCEEDED")]:
            with self.subTest(name=name):
                self.assert_code(lambda: pdf.extract_pdf_bytes((FIXTURES / name).read_bytes()), code)


class DocumentPdfReviewTests(unittest.TestCase):
    assert_code = DocumentPdfCoreTests.assert_code

    def test_explicit_blank_set_and_adjacent_significant_characters(self):
        self.assertEqual(sorted(pdf.BLANK_TEXT_CODEPOINTS), REVIEW["blank_codepoints"])
        self.assertEqual(len(pdf.BLANK_TEXT_CODEPOINTS), 30)
        blank = "".join(map(chr, REVIEW["blank_codepoints"]))
        self.assertFalse(pdf.has_pdf_text(blank))
        for value in ["A", "中", "🚓", "\u0301", "\u200b", "\u0008", "\u000e", "\u001b", "\u0021"]:
            self.assertTrue(pdf.has_pdf_text(blank + value + blank), repr(value))
        self.assert_code(lambda: pdf._assemble(b"%PDF-", [blank, ""]), "PDF_NO_EXTRACTABLE_TEXT")
        result = pdf._assemble(b"%PDF-", ["\u0085", blank + "A" + blank, "\ufeff"])
        self.assertEqual([p["status"] for p in result["receipt"]["pages"]], ["NO_EXTRACTABLE_TEXT", "TEXT", "NO_EXTRACTABLE_TEXT"])
        self.assertEqual(result["text"], "\u0085\n\f\n" + blank + "A" + blank + "\n\f\n\ufeff")

    def test_real_review_originals_match_maps_or_fail_without_partial_text(self):
        for name, digest in REVIEW["binary_sha256"].items():
            body = (FIXTURES / name).read_bytes()
            self.assertEqual(hashlib.sha256(body).hexdigest(), digest)
            with self.subTest(name=name):
                if name in REVIEW["failures"]:
                    self.assert_code(lambda: pdf.extract_pdf_bytes(body), REVIEW["failures"][name])
                else:
                    self.assertEqual(pdf.extract_pdf_bytes(body), REVIEW["extractions"][name])

    def test_real_empty_password_encryption_is_readable_but_still_rejected(self):
        import pypdf
        import io
        body = (FIXTURES / "empty-password.pdf").read_bytes()
        reader = pypdf.PdfReader(io.BytesIO(body))
        self.assertTrue(reader.is_encrypted)
        self.assertTrue(reader.decrypt(""))
        self.assertEqual(reader.pages[0].extract_text(), "Synthetic encrypted original")
        self.assert_code(lambda: pdf.extract_pdf_bytes(body), "PDF_ENCRYPTED")


if __name__ == "__main__":
    unittest.main()
