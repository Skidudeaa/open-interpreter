"""
PDF Parser - Extract text and structure from PDF documents.

Uses PyMuPDF (fitz) for PDF parsing with support for:
- Text extraction with layout preservation
- Table detection and extraction
- Image extraction (optional)
- Metadata extraction
"""

from typing import Any

from .base import BaseParser, DependencyMissingError, DocumentType, ParsedDocument

# Lazy import for optional dependency
fitz = None


def _get_fitz():
    """Lazy load PyMuPDF."""
    global fitz
    if fitz is None:
        try:
            import fitz as _fitz

            fitz = _fitz
        except ImportError:
            pass
    return fitz


class PDFParser(BaseParser):
    """
    Parse PDF documents using PyMuPDF.

    Features:
    - Text extraction with structure preservation
    - Table detection and extraction
    - Image extraction (optional)
    - Metadata extraction (author, title, dates)
    """

    def can_parse(self, source: str) -> bool:
        """Check if source is a PDF file."""
        return source.lower().endswith(".pdf")

    def parse(
        self,
        source: str,
        extract_images: bool = False,
        extract_tables: bool = True,
        page_range: tuple[int, int] | None = None,
        **options: Any,
    ) -> ParsedDocument:
        """
        Parse a PDF file.

        Args:
            source: Path to PDF file
            extract_images: Extract embedded images (slower)
            extract_tables: Attempt table extraction
            page_range: Optional (start, end) page numbers (0-indexed)
            **options: Additional options

        Returns:
            ParsedDocument with extracted text and structure

        Raises:
            DependencyMissingError: If PyMuPDF is not installed
            FileNotFoundError: If PDF file doesn't exist
        """
        pdf_lib = _get_fitz()
        if pdf_lib is None:
            raise DependencyMissingError(
                "PyMuPDF (fitz)",
                "pip install 'open-interpreter[documents]' or pip install PyMuPDF",
            )

        doc = pdf_lib.open(source)

        try:
            text_parts = []
            sections = []
            tables = []
            images = []
            parse_errors = []
            parse_warnings = []

            # Determine page range
            start_page = page_range[0] if page_range else 0
            end_page = page_range[1] if page_range else len(doc)
            end_page = min(end_page, len(doc))

            current_section = None
            prev_font_size = 0

            for page_num in range(start_page, end_page):
                page = doc[page_num]

                # Extract text
                try:
                    text = page.get_text("text")
                    text_parts.append(text)
                except Exception as e:
                    parse_errors.append(f"Page {page_num}: text extraction failed: {e}")
                    continue

                # Extract structure via text blocks
                try:
                    blocks = page.get_text("dict", flags=11)["blocks"]
                    for block in blocks:
                        if block.get("type") == 0:  # Text block
                            lines = block.get("lines", [])
                            for line in lines:
                                spans = line.get("spans", [])
                                for span in spans:
                                    font_size = span.get("size", 12)
                                    text_content = span.get("text", "").strip()

                                    # Detect headings by font size change
                                    if (
                                        text_content
                                        and font_size > 14
                                        and font_size > prev_font_size * 1.2
                                    ):
                                        if current_section:
                                            sections.append(current_section)
                                        current_section = {
                                            "heading": text_content,
                                            "level": 1 if font_size > 18 else 2,
                                            "content": "",
                                            "page": page_num,
                                        }
                                    elif current_section and text_content:
                                        current_section["content"] += text_content + " "

                                    prev_font_size = font_size
                except Exception as e:
                    parse_warnings.append(
                        f"Page {page_num}: structure extraction failed: {e}"
                    )

                # Extract tables if requested
                if extract_tables:
                    try:
                        # PyMuPDF 1.23+ has find_tables()
                        if hasattr(page, "find_tables"):
                            page_tables = page.find_tables()
                            for table in page_tables:
                                table_data = table.extract()
                                if table_data:
                                    tables.append(table_data)
                    except Exception as e:
                        parse_warnings.append(
                            f"Page {page_num}: table extraction failed: {e}"
                        )

                # Extract images if requested
                if extract_images:
                    try:
                        for _img_index, img in enumerate(page.get_images()):
                            xref = img[0]
                            try:
                                base_image = doc.extract_image(xref)
                                images.append(
                                    {
                                        "page": page_num,
                                        "data": base_image["image"],
                                        "format": base_image["ext"],
                                        "width": base_image.get("width"),
                                        "height": base_image.get("height"),
                                    }
                                )
                            except Exception:
                                pass  # Skip problematic images
                    except Exception as e:
                        parse_warnings.append(
                            f"Page {page_num}: image extraction failed: {e}"
                        )

            # Add last section
            if current_section:
                sections.append(current_section)

            # Extract metadata
            metadata = dict(doc.metadata) if doc.metadata else {}

            return ParsedDocument(
                text="\n\n".join(text_parts),
                document_type=DocumentType.PDF,
                source=source,
                title=metadata.get("title") or None,
                sections=sections,
                tables=tables,
                images=images,
                metadata=metadata,
                page_count=len(doc),
                parse_errors=parse_errors,
                parse_warnings=parse_warnings,
            )

        finally:
            doc.close()
