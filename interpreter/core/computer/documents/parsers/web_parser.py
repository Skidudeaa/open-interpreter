"""
Web Page Parser - Extract article content from web pages.

Uses trafilatura for high-quality extraction with html2text as fallback.
Supports:
- Main article content extraction (readability mode)
- Full page text conversion
- Metadata extraction (title, author, date)
"""

from typing import Any

import requests

from .base import BaseParser, DocumentParseError, DocumentType, ParsedDocument

# Lazy imports for optional dependencies
trafilatura = None
html2text = None


def _get_trafilatura():
    """Lazy load trafilatura."""
    global trafilatura
    if trafilatura is None:
        try:
            import trafilatura as _trafilatura

            trafilatura = _trafilatura
        except ImportError:
            pass
    return trafilatura


def _get_html2text():
    """Lazy load html2text."""
    global html2text
    if html2text is None:
        try:
            import html2text as _html2text

            html2text = _html2text
        except ImportError:
            pass
    return html2text


class WebParser(BaseParser):
    """
    Extract content from web pages.

    Features:
    - Main article extraction using trafilatura
    - Fallback to html2text for full page conversion
    - Metadata extraction (title, author, date, site)
    """

    def can_parse(self, source: str) -> bool:
        """Check if source is a URL."""
        return source.startswith(("http://", "https://"))

    def parse(
        self,
        source: str,
        mode: str = "readability",
        include_tables: bool = True,
        include_links: bool = False,
        timeout: int = 30,
        **options: Any,
    ) -> ParsedDocument:
        """
        Extract content from a web page.

        Args:
            source: URL to fetch and parse
            mode: Extraction mode
                - "readability": Main article content only (default)
                - "full": Include sidebars, footers
                - "raw": Raw HTML to text conversion
            include_tables: Preserve table structure
            include_links: Include hyperlinks in output
            timeout: Request timeout in seconds
            **options: Additional options

        Returns:
            ParsedDocument with extracted text

        Raises:
            DocumentParseError: If fetching or parsing fails
        """
        # Fetch page
        try:
            response = requests.get(
                source,
                timeout=timeout,
                headers={
                    "User-Agent": "Mozilla/5.0 (compatible; OpenInterpreter/1.0; +https://github.com/OpenInterpreter/open-interpreter)"
                },
            )
            response.raise_for_status()
            html = response.text
        except requests.RequestException as e:
            raise DocumentParseError(f"Failed to fetch {source}: {e}") from e

        title = None
        text = ""
        metadata = {
            "url": source,
            "status_code": response.status_code,
            "content_type": response.headers.get("content-type"),
        }
        parse_warnings = []

        # Try trafilatura first (best quality)
        traf = _get_trafilatura()
        h2t = _get_html2text()

        if mode == "raw" or (traf is None and h2t is not None):
            # Use html2text for raw conversion
            if h2t is None:
                raise DocumentParseError(
                    "No HTML parser available. Install trafilatura or html2text."
                )

            converter = h2t.HTML2Text()
            converter.ignore_links = not include_links
            converter.ignore_images = True
            converter.body_width = 0  # Don't wrap lines
            text = converter.handle(html)

            # Try to extract title from HTML
            import re

            title_match = re.search(r"<title[^>]*>([^<]+)</title>", html, re.IGNORECASE)
            if title_match:
                title = title_match.group(1).strip()

        elif traf is not None:
            # Use trafilatura for readability extraction
            try:
                extracted = traf.extract(
                    html,
                    include_tables=include_tables,
                    include_links=include_links,
                    output_format="txt",
                    favor_precision=mode == "readability",
                    favor_recall=mode == "full",
                )
                text = extracted or ""

                # Get metadata
                try:
                    meta = traf.extract_metadata(html)
                    if meta:
                        title = meta.title
                        metadata.update(
                            {
                                "author": meta.author,
                                "date": str(meta.date) if meta.date else None,
                                "sitename": meta.sitename,
                                "description": meta.description,
                            }
                        )
                except Exception as e:
                    parse_warnings.append(f"Metadata extraction failed: {e}")

            except Exception as e:
                parse_warnings.append(f"Trafilatura extraction failed: {e}")
                # Fallback to html2text
                if h2t is not None:
                    converter = h2t.HTML2Text()
                    converter.ignore_links = not include_links
                    converter.ignore_images = True
                    text = converter.handle(html)
                else:
                    raise DocumentParseError(f"Content extraction failed: {e}") from e
        else:
            raise DocumentParseError(
                "No HTML parser available. Install with: pip install trafilatura"
            )

        # Clean up text
        if text:
            # Remove excessive whitespace
            import re

            text = re.sub(r"\n{3,}", "\n\n", text)
            text = re.sub(r" {2,}", " ", text)
            text = text.strip()

        # Remove None values from metadata
        metadata = {k: v for k, v in metadata.items() if v is not None}

        return ParsedDocument(
            text=text,
            document_type=DocumentType.WEB,
            source=source,
            title=title,
            metadata=metadata,
            parse_warnings=parse_warnings,
        )
