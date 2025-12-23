"""
Documents Module - Document parsing capabilities for Open Interpreter.

Provides parsing for:
- PDF documents (via PyMuPDF)
- Word documents (via python-docx)
- Web pages (via trafilatura/html2text)

All parsers are lazy-loaded to avoid importing optional dependencies
until they're actually needed.

Example:
    doc = computer.documents.parse_pdf("/path/to/file.pdf")
    summary = computer.ai.summarize(doc.text)
    answer = computer.ai.query(doc.text, "What is the main argument?")
"""

from typing import TYPE_CHECKING, Any

from .parsers.base import DocumentType, ParsedDocument

if TYPE_CHECKING:
    from .parsers.docx_parser import DocxParser
    from .parsers.pdf_parser import PDFParser
    from .parsers.web_parser import WebParser


class Documents:
    """
    Document parsing facade for Open Interpreter.

    Provides a unified interface for parsing various document types.
    Parsers are lazy-loaded on first use to avoid importing optional
    dependencies until needed.

    Usage:
        # Parse a PDF
        doc = computer.documents.parse_pdf("/path/to/file.pdf")

        # Parse a Word document
        doc = computer.documents.parse_docx("/path/to/file.docx")

        # Parse a web page
        doc = computer.documents.parse_webpage("https://example.com/article")

        # Auto-detect and parse
        doc = computer.documents.parse("/path/to/any/document.pdf")

        # Quick text extraction
        text = computer.documents.extract_text("/path/to/file.pdf")

        # Query a document
        answer = computer.documents.query("/path/to/file.pdf", "What is this about?")
    """

    def __init__(self, computer: Any):
        """
        Initialize the Documents module.

        Args:
            computer: The Computer instance
        """
        self.computer = computer

        # Lazy-loaded parsers
        self._pdf_parser: PDFParser | None = None
        self._docx_parser: DocxParser | None = None
        self._web_parser: WebParser | None = None

    @property
    def pdf_parser(self) -> "PDFParser":
        """Get the PDF parser (lazy-loaded)."""
        if self._pdf_parser is None:
            from .parsers.pdf_parser import PDFParser

            self._pdf_parser = PDFParser(self.computer)
        return self._pdf_parser

    @property
    def docx_parser(self) -> "DocxParser":
        """Get the Word document parser (lazy-loaded)."""
        if self._docx_parser is None:
            from .parsers.docx_parser import DocxParser

            self._docx_parser = DocxParser(self.computer)
        return self._docx_parser

    @property
    def web_parser(self) -> "WebParser":
        """Get the web page parser (lazy-loaded)."""
        if self._web_parser is None:
            from .parsers.web_parser import WebParser

            self._web_parser = WebParser(self.computer)
        return self._web_parser

    def parse_pdf(
        self,
        path: str,
        extract_images: bool = False,
        extract_tables: bool = True,
        page_range: tuple[int, int] | None = None,
    ) -> ParsedDocument:
        """
        Parse a PDF document.

        Args:
            path: Path to the PDF file
            extract_images: Extract embedded images (slower)
            extract_tables: Attempt to extract tables
            page_range: Optional (start, end) page numbers (0-indexed)

        Returns:
            ParsedDocument with extracted text and structure
        """
        return self.pdf_parser.parse(
            path,
            extract_images=extract_images,
            extract_tables=extract_tables,
            page_range=page_range,
        )

    def parse_docx(self, path: str) -> ParsedDocument:
        """
        Parse a Word document (.docx).

        Args:
            path: Path to the Word document

        Returns:
            ParsedDocument with extracted text and structure
        """
        return self.docx_parser.parse(path)

    def parse_webpage(
        self,
        url: str,
        mode: str = "readability",
        include_tables: bool = True,
        timeout: int = 30,
    ) -> ParsedDocument:
        """
        Extract article content from a web page.

        Args:
            url: URL to fetch and parse
            mode: Extraction mode
                - "readability": Main content only (default)
                - "full": Include sidebars, footers
                - "raw": Raw HTML to text
            include_tables: Preserve table structure
            timeout: Request timeout in seconds

        Returns:
            ParsedDocument with extracted text
        """
        return self.web_parser.parse(
            url,
            mode=mode,
            include_tables=include_tables,
            timeout=timeout,
        )

    def parse(self, source: str, **options: Any) -> ParsedDocument:
        """
        Parse any supported document type (auto-detection).

        Detects document type by extension or URL scheme and routes
        to the appropriate parser.

        Args:
            source: File path or URL
            **options: Parser-specific options

        Returns:
            ParsedDocument with extracted content
        """
        source_lower = source.lower()

        # Web URLs
        if source.startswith(("http://", "https://")):
            return self.web_parser.parse(source, **options)

        # PDF files
        if source_lower.endswith(".pdf"):
            return self.pdf_parser.parse(source, **options)

        # Word documents
        if source_lower.endswith(".docx"):
            return self.docx_parser.parse(source, **options)

        # Plain text fallback
        try:
            with open(source, encoding="utf-8", errors="ignore") as f:
                text = f.read()
            return ParsedDocument(
                text=text,
                document_type=DocumentType.TEXT,
                source=source,
            )
        except Exception as e:
            return ParsedDocument(
                text="",
                document_type=DocumentType.UNKNOWN,
                source=source,
                parse_errors=[str(e)],
            )

    def extract_text(self, source: str, **options: Any) -> str:
        """
        Extract text from any supported source.

        Convenience method that parses the document and returns just the text.

        Args:
            source: File path or URL
            **options: Parser-specific options

        Returns:
            Extracted text as string
        """
        doc = self.parse(source, **options)
        return doc.text

    def query(
        self,
        source: str,
        query: str,
        custom_reduce_query: str | None = None,
        **options: Any,
    ) -> str:
        """
        Parse a document and query it using the AI module.

        Combines document parsing with the AI module's query capability
        for map-reduce style question answering over large documents.

        Args:
            source: File path or URL
            query: Question to ask about the document
            custom_reduce_query: Custom query for reducing chunked responses
            **options: Parser-specific options

        Returns:
            AI-generated answer
        """
        doc = self.parse(source, **options)
        return self.computer.ai.query(doc.text, query, custom_reduce_query)

    def summarize(self, source: str, **options: Any) -> str:
        """
        Parse a document and summarize it using the AI module.

        Args:
            source: File path or URL
            **options: Parser-specific options

        Returns:
            AI-generated summary
        """
        doc = self.parse(source, **options)
        return self.computer.ai.summarize(doc.text)

    def get_chunks(
        self,
        source: str,
        chunk_size: int = 2000,
        overlap: int = 200,
        **options: Any,
    ) -> list[str]:
        """
        Parse a document and return text chunks for RAG processing.

        Args:
            source: File path or URL
            chunk_size: Target chunk size in characters
            overlap: Overlap between chunks in characters
            **options: Parser-specific options

        Returns:
            List of text chunks
        """
        doc = self.parse(source, **options)
        return doc.to_chunks(chunk_size=chunk_size, overlap=overlap)
