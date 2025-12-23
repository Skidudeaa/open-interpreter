"""
Tests for the Documents module.

Tests document parsing functionality including:
- ParsedDocument dataclass
- PDF parsing (mocked)
- Word document parsing (mocked)
- Web page parsing (mocked)
- Documents facade
"""

import unittest
from unittest import mock

from interpreter.core.computer.documents.parsers.base import (
    DocumentType,
    ParsedDocument,
)


class TestParsedDocument(unittest.TestCase):
    """Tests for ParsedDocument dataclass."""

    def test_basic_creation(self):
        """Test basic ParsedDocument creation."""
        doc = ParsedDocument(
            text="Hello world",
            document_type=DocumentType.TEXT,
            source="/path/to/file.txt",
        )
        self.assertEqual(doc.text, "Hello world")
        self.assertEqual(doc.document_type, DocumentType.TEXT)
        self.assertEqual(doc.source, "/path/to/file.txt")

    def test_word_count_computed(self):
        """Test that word_count is computed automatically."""
        doc = ParsedDocument(
            text="one two three four five",
            document_type=DocumentType.TEXT,
            source="test.txt",
        )
        self.assertEqual(doc.word_count, 5)

    def test_to_chunks_basic(self):
        """Test text chunking."""
        long_text = "This is a test. " * 100
        doc = ParsedDocument(
            text=long_text,
            document_type=DocumentType.TEXT,
            source="test.txt",
        )
        chunks = doc.to_chunks(chunk_size=200, overlap=50)
        self.assertGreater(len(chunks), 1)
        for chunk in chunks:
            self.assertLessEqual(len(chunk), 250)  # Allow some flexibility

    def test_to_chunks_empty(self):
        """Test chunking empty text."""
        doc = ParsedDocument(
            text="",
            document_type=DocumentType.TEXT,
            source="test.txt",
        )
        chunks = doc.to_chunks()
        self.assertEqual(chunks, [])

    def test_get_section(self):
        """Test section retrieval."""
        doc = ParsedDocument(
            text="Full text here",
            document_type=DocumentType.PDF,
            source="test.pdf",
            sections=[
                {"heading": "Introduction", "content": "Intro content"},
                {"heading": "Methods", "content": "Methods content"},
            ],
        )
        self.assertEqual(doc.get_section("Introduction"), "Intro content")
        self.assertEqual(doc.get_section("introduction"), "Intro content")  # Case insensitive
        self.assertIsNone(doc.get_section("Nonexistent"))

    def test_to_dict(self):
        """Test dictionary conversion."""
        doc = ParsedDocument(
            text="Test text",
            document_type=DocumentType.PDF,
            source="test.pdf",
            title="Test Document",
            page_count=5,
        )
        d = doc.to_dict()
        self.assertEqual(d["text"], "Test text")
        self.assertEqual(d["document_type"], "pdf")
        self.assertEqual(d["title"], "Test Document")
        self.assertEqual(d["page_count"], 5)


class TestDocumentType(unittest.TestCase):
    """Tests for DocumentType enum."""

    def test_document_types(self):
        """Test all document types exist."""
        self.assertEqual(DocumentType.PDF.value, "pdf")
        self.assertEqual(DocumentType.DOCX.value, "docx")
        self.assertEqual(DocumentType.WEB.value, "web")
        self.assertEqual(DocumentType.TEXT.value, "text")
        self.assertEqual(DocumentType.UNKNOWN.value, "unknown")


class TestDocumentsFacade(unittest.TestCase):
    """Tests for Documents facade class."""

    def setUp(self):
        """Set up test fixtures."""
        self.mock_computer = mock.Mock()
        self.mock_computer.ai = mock.Mock()

    def test_documents_init(self):
        """Test Documents initialization."""
        from interpreter.core.computer.documents.documents import Documents

        docs = Documents(self.mock_computer)
        self.assertIsNone(docs._pdf_parser)
        self.assertIsNone(docs._docx_parser)
        self.assertIsNone(docs._web_parser)

    def test_parse_auto_detect_pdf(self):
        """Test auto-detection of PDF files."""
        from interpreter.core.computer.documents.documents import Documents

        docs = Documents(self.mock_computer)

        # Mock the underlying parser attribute
        mock_parser = mock.Mock()
        mock_parser.parse.return_value = ParsedDocument(
            text="PDF content",
            document_type=DocumentType.PDF,
            source="test.pdf",
        )
        docs._pdf_parser = mock_parser
        result = docs.parse("test.pdf")
        mock_parser.parse.assert_called_once()
        self.assertEqual(result.document_type, DocumentType.PDF)

    def test_parse_auto_detect_docx(self):
        """Test auto-detection of Word documents."""
        from interpreter.core.computer.documents.documents import Documents

        docs = Documents(self.mock_computer)

        # Mock the underlying parser attribute
        mock_parser = mock.Mock()
        mock_parser.parse.return_value = ParsedDocument(
            text="Word content",
            document_type=DocumentType.DOCX,
            source="test.docx",
        )
        docs._docx_parser = mock_parser
        result = docs.parse("test.docx")
        mock_parser.parse.assert_called_once()
        self.assertEqual(result.document_type, DocumentType.DOCX)

    def test_parse_auto_detect_url(self):
        """Test auto-detection of URLs."""
        from interpreter.core.computer.documents.documents import Documents

        docs = Documents(self.mock_computer)

        # Mock the underlying parser attribute
        mock_parser = mock.Mock()
        mock_parser.parse.return_value = ParsedDocument(
            text="Web content",
            document_type=DocumentType.WEB,
            source="https://example.com",
        )
        docs._web_parser = mock_parser
        result = docs.parse("https://example.com")
        mock_parser.parse.assert_called_once()
        self.assertEqual(result.document_type, DocumentType.WEB)

    def test_extract_text(self):
        """Test extract_text convenience method."""
        from interpreter.core.computer.documents.documents import Documents

        docs = Documents(self.mock_computer)

        with mock.patch.object(docs, 'parse') as mock_parse:
            mock_parse.return_value = ParsedDocument(
                text="Extracted text",
                document_type=DocumentType.TEXT,
                source="test.txt",
            )
            text = docs.extract_text("test.txt")
            self.assertEqual(text, "Extracted text")

    def test_get_chunks(self):
        """Test get_chunks convenience method."""
        from interpreter.core.computer.documents.documents import Documents

        docs = Documents(self.mock_computer)

        with mock.patch.object(docs, 'parse') as mock_parse:
            mock_parse.return_value = ParsedDocument(
                text="Word " * 500,  # Long text
                document_type=DocumentType.TEXT,
                source="test.txt",
            )
            # Use sensible overlap value (smaller than chunk_size)
            chunks = docs.get_chunks("test.txt", chunk_size=100, overlap=20)
            self.assertIsInstance(chunks, list)
            self.assertGreater(len(chunks), 1)


class TestPDFParser(unittest.TestCase):
    """Tests for PDF parser."""

    def test_can_parse(self):
        """Test PDF file detection."""
        from interpreter.core.computer.documents.parsers.pdf_parser import PDFParser

        parser = PDFParser(mock.Mock())
        self.assertTrue(parser.can_parse("document.pdf"))
        self.assertTrue(parser.can_parse("DOCUMENT.PDF"))
        self.assertFalse(parser.can_parse("document.docx"))
        self.assertFalse(parser.can_parse("document.txt"))


class TestDocxParser(unittest.TestCase):
    """Tests for Word document parser."""

    def test_can_parse(self):
        """Test Word file detection."""
        from interpreter.core.computer.documents.parsers.docx_parser import DocxParser

        parser = DocxParser(mock.Mock())
        self.assertTrue(parser.can_parse("document.docx"))
        self.assertTrue(parser.can_parse("DOCUMENT.DOCX"))
        self.assertFalse(parser.can_parse("document.doc"))  # Only .docx
        self.assertFalse(parser.can_parse("document.pdf"))


class TestWebParser(unittest.TestCase):
    """Tests for web page parser."""

    def test_can_parse(self):
        """Test URL detection."""
        from interpreter.core.computer.documents.parsers.web_parser import WebParser

        parser = WebParser(mock.Mock())
        self.assertTrue(parser.can_parse("https://example.com"))
        self.assertTrue(parser.can_parse("http://example.com/page"))
        self.assertFalse(parser.can_parse("/local/file.html"))
        self.assertFalse(parser.can_parse("ftp://example.com"))

    @mock.patch('interpreter.core.computer.documents.parsers.web_parser.requests')
    def test_parse_web_page(self, mock_requests):
        """Test web page parsing with mocked requests."""
        from interpreter.core.computer.documents.parsers.web_parser import WebParser

        # Mock response
        mock_response = mock.Mock()
        mock_response.text = "<html><head><title>Test</title></head><body>Content here</body></html>"
        mock_response.status_code = 200
        mock_response.headers = {"content-type": "text/html"}
        mock_requests.get.return_value = mock_response

        parser = WebParser(mock.Mock())
        result = parser.parse("https://example.com", mode="raw")

        self.assertEqual(result.document_type, DocumentType.WEB)
        self.assertEqual(result.source, "https://example.com")
        self.assertIn("Content", result.text)


if __name__ == "__main__":
    unittest.main()
