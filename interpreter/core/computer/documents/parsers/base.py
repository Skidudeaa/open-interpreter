"""
Base classes for document parsing.

Provides:
- ParsedDocument: Dataclass for parsed document results
- DocumentType: Enum for document types
- BaseParser: Abstract base class for parsers
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class DocumentType(Enum):
    """Supported document types."""

    PDF = "pdf"
    DOCX = "docx"
    DOC = "doc"
    WEB = "web"
    TEXT = "text"
    UNKNOWN = "unknown"


@dataclass
class ParsedDocument:
    """
    Result of parsing a document.

    Contains the extracted text content along with optional
    structural information like sections, tables, and metadata.
    """

    # Core content
    text: str
    document_type: DocumentType
    source: str  # File path or URL

    # Structure (optional)
    title: str | None = None
    sections: list[dict[str, Any]] = field(default_factory=list)
    tables: list[list[list[str]]] = field(default_factory=list)
    images: list[dict[str, Any]] = field(default_factory=list)

    # Metadata
    metadata: dict[str, Any] = field(default_factory=dict)
    page_count: int | None = None
    word_count: int = 0

    # Processing info
    parse_errors: list[str] = field(default_factory=list)
    parse_warnings: list[str] = field(default_factory=list)

    def __post_init__(self):
        """Compute derived fields."""
        if not self.word_count and self.text:
            self.word_count = len(self.text.split())

    def to_chunks(self, chunk_size: int = 2000, overlap: int = 200) -> list[str]:
        """
        Split text into chunks for RAG processing.

        Args:
            chunk_size: Target size of each chunk in characters
            overlap: Number of characters to overlap between chunks

        Returns:
            List of text chunks
        """
        if not self.text:
            return []

        # Ensure overlap doesn't exceed chunk_size to prevent infinite loops
        overlap = min(overlap, chunk_size - 1) if chunk_size > 0 else 0

        chunks = []
        text = self.text
        start = 0

        while start < len(text):
            end = start + chunk_size

            # Try to break at a sentence or paragraph boundary
            if end < len(text):
                # Look for paragraph break
                para_break = text.rfind("\n\n", start, end)
                if para_break > start + chunk_size // 2:
                    end = para_break + 2
                else:
                    # Look for sentence break
                    for sep in [". ", "! ", "? ", "\n"]:
                        sent_break = text.rfind(sep, start, end)
                        if sent_break > start + chunk_size // 2:
                            end = sent_break + len(sep)
                            break

            chunk = text[start:end].strip()
            if chunk:
                chunks.append(chunk)

            start = end - overlap if end < len(text) else end

        return chunks

    def get_section(self, heading: str) -> str | None:
        """
        Get content of a specific section by heading.

        Args:
            heading: Section heading to find (case-insensitive)

        Returns:
            Section content or None if not found
        """
        for section in self.sections:
            if section.get("heading", "").lower() == heading.lower():
                return section.get("content")
        return None

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary representation."""
        return {
            "text": self.text,
            "document_type": self.document_type.value,
            "source": self.source,
            "title": self.title,
            "sections": self.sections,
            "tables": self.tables,
            "metadata": self.metadata,
            "page_count": self.page_count,
            "word_count": self.word_count,
        }


class BaseParser(ABC):
    """
    Abstract base class for document parsers.

    Subclasses must implement:
    - parse(): Parse document from path or URL
    - can_parse(): Check if parser handles this source type
    """

    def __init__(self, computer: Any):
        """
        Initialize the parser.

        Args:
            computer: The Computer instance for accessing other modules
        """
        self.computer = computer

    @abstractmethod
    def parse(self, source: str, **options: Any) -> ParsedDocument:
        """
        Parse document from path or URL.

        Args:
            source: File path or URL to parse
            **options: Parser-specific options

        Returns:
            ParsedDocument with extracted content
        """
        pass

    @abstractmethod
    def can_parse(self, source: str) -> bool:
        """
        Check if this parser can handle the source.

        Args:
            source: File path or URL

        Returns:
            True if this parser can handle the source
        """
        pass

    def _detect_encoding(self, file_path: str) -> str:
        """
        Detect file encoding.

        Args:
            file_path: Path to file

        Returns:
            Detected encoding name, defaults to utf-8
        """
        try:
            import charset_normalizer

            result = charset_normalizer.from_path(file_path)
            best = result.best()
            return best.encoding if best else "utf-8"
        except ImportError:
            return "utf-8"
        except Exception:
            return "utf-8"


class DocumentParseError(Exception):
    """Base exception for document parsing errors."""

    pass


class UnsupportedFormatError(DocumentParseError):
    """Raised when document format is not supported."""

    pass


class DependencyMissingError(DocumentParseError):
    """Raised when required optional dependency is missing."""

    def __init__(self, dependency: str, install_command: str):
        self.dependency = dependency
        self.install_command = install_command
        super().__init__(f"{dependency} is required. Install with: {install_command}")
