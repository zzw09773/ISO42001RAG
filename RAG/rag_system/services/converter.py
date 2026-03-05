"""
File Converter Service

Converts various document formats (PDF, RTF, DOCX) to Markdown for RAG indexing.
"""
import logging
from pathlib import Path
from typing import Optional
import tempfile
import shutil

logger = logging.getLogger(__name__)


class ConversionError(Exception):
    """Raised when file conversion fails."""
    pass


class FileConverter:
    """
    Converts various document formats to Markdown.

    Supported formats:
    - PDF (.pdf)
    - RTF (.rtf)
    - DOCX (.docx)
    - TXT (.txt) - pass through
    - MD (.md) - pass through
    """

    SUPPORTED_EXTENSIONS = {'.pdf', '.rtf', '.docx', '.txt', '.md'}

    def __init__(self, output_dir: Optional[Path] = None):
        """
        Initialize the converter.

        Args:
            output_dir: Directory to save converted files. If None, uses temp directory.
        """
        self.output_dir = output_dir or Path(tempfile.mkdtemp())
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def is_supported(self, file_path: Path) -> bool:
        """Check if file format is supported."""
        return file_path.suffix.lower() in self.SUPPORTED_EXTENSIONS

    def convert(self, file_path: Path, output_name: Optional[str] = None) -> Path:
        """
        Convert a file to Markdown.

        Args:
            file_path: Path to the input file
            output_name: Optional name for output file (without extension)

        Returns:
            Path to the converted Markdown file

        Raises:
            ConversionError: If conversion fails
        """
        if not file_path.exists():
            raise ConversionError(f"File not found: {file_path}")

        ext = file_path.suffix.lower()

        if ext not in self.SUPPORTED_EXTENSIONS:
            raise ConversionError(
                f"Unsupported format: {ext}. "
                f"Supported: {', '.join(self.SUPPORTED_EXTENSIONS)}"
            )

        output_name = output_name or file_path.stem
        output_path = self.output_dir / f"{output_name}.md"

        try:
            if ext == '.pdf':
                content = self._convert_pdf(file_path)
            elif ext == '.rtf':
                content = self._convert_rtf(file_path)
            elif ext == '.docx':
                content = self._convert_docx(file_path)
            elif ext in ('.txt', '.md'):
                content = file_path.read_text(encoding='utf-8')
            else:
                raise ConversionError(f"Unhandled extension: {ext}")

            # Write Markdown output
            output_path.write_text(content, encoding='utf-8')
            logger.info(f"Converted {file_path.name} -> {output_path.name}")

            return output_path

        except Exception as e:
            logger.error(f"Conversion failed for {file_path.name}: {e}")
            raise ConversionError(f"Failed to convert {file_path.name}: {e}") from e

    def _convert_pdf(self, file_path: Path) -> str:
        """Convert PDF to Markdown using PyMuPDF."""
        try:
            import fitz  # PyMuPDF
        except ImportError:
            raise ConversionError("PyMuPDF not installed. Run: pip install PyMuPDF")

        doc = fitz.open(file_path)
        content_parts = []

        # Add document title as heading
        content_parts.append(f"# {file_path.stem}\n")

        for page_num, page in enumerate(doc, 1):
            text = page.get_text("text")
            if text.strip():
                content_parts.append(f"\n## 第 {page_num} 頁\n\n{text}")

        doc.close()
        return "\n".join(content_parts)

    def _convert_rtf(self, file_path: Path) -> str:
        """Convert RTF to Markdown using striprtf."""
        try:
            from striprtf.striprtf import rtf_to_text
        except ImportError:
            raise ConversionError("striprtf not installed. Run: pip install striprtf")

        rtf_content = file_path.read_text(encoding='utf-8', errors='ignore')
        text = rtf_to_text(rtf_content)

        # Add document title as heading
        return f"# {file_path.stem}\n\n{text}"

    def _convert_docx(self, file_path: Path) -> str:
        """Convert DOCX to Markdown using python-docx."""
        try:
            from docx import Document
        except ImportError:
            raise ConversionError("python-docx not installed. Run: pip install python-docx")

        doc = Document(file_path)
        content_parts = []

        # Add document title as heading
        content_parts.append(f"# {file_path.stem}\n")

        for para in doc.paragraphs:
            text = para.text.strip()
            if text:
                # Check if it's a heading
                if para.style.name.startswith('Heading'):
                    level = int(para.style.name[-1]) if para.style.name[-1].isdigit() else 2
                    content_parts.append(f"\n{'#' * (level + 1)} {text}\n")
                else:
                    content_parts.append(f"{text}\n")

        return "\n".join(content_parts)

    def convert_bytes(self, content: bytes, filename: str) -> Path:
        """
        Convert file content from bytes.

        Args:
            content: File content as bytes
            filename: Original filename (used to determine format)

        Returns:
            Path to the converted Markdown file
        """
        # Save to temp file first
        temp_path = self.output_dir / filename
        temp_path.write_bytes(content)

        try:
            result = self.convert(temp_path)
            return result
        finally:
            # Clean up temp file if it's different from output
            if temp_path.suffix.lower() not in ('.md', '.txt'):
                temp_path.unlink(missing_ok=True)


class ConversionPipeline:
    """
    Pipeline for converting and indexing documents.
    """

    def __init__(self, config, converted_dir: Optional[Path] = None):
        """
        Initialize the pipeline.

        Args:
            config: RAGConfig instance
            converted_dir: Directory to store converted Markdown files
        """
        from .ingestion import IngestionService

        self.config = config
        self.converted_dir = converted_dir or Path("./data/converted_md")
        self.converted_dir.mkdir(parents=True, exist_ok=True)

        self.converter = FileConverter(output_dir=self.converted_dir)
        self.ingestion_service = IngestionService(config)

    def process_file(self, file_path: Path) -> dict:
        """
        Convert a file to Markdown and index it.

        Returns:
            dict with 'converted_path', 'indexed', and 'message'
        """
        result = {
            'original_file': str(file_path),
            'converted_path': None,
            'indexed': False,
            'message': ''
        }

        try:
            # Step 1: Convert to Markdown
            md_path = self.converter.convert(file_path)
            result['converted_path'] = str(md_path)

            # Step 2: Index the Markdown file
            self.ingestion_service.index_file(md_path)
            result['indexed'] = True
            result['message'] = f"Successfully converted and indexed: {file_path.name}"

        except ConversionError as e:
            result['message'] = f"Conversion failed: {e}"
        except Exception as e:
            result['message'] = f"Indexing failed: {e}"

        return result

    def process_bytes(self, content: bytes, filename: str) -> dict:
        """
        Process file content from bytes (for API uploads).

        Returns:
            dict with processing results
        """
        result = {
            'original_file': filename,
            'converted_path': None,
            'indexed': False,
            'message': ''
        }

        try:
            # Step 1: Convert to Markdown
            md_path = self.converter.convert_bytes(content, filename)
            result['converted_path'] = str(md_path)

            # Step 2: Index the Markdown file
            self.ingestion_service.index_file(md_path)
            result['indexed'] = True
            result['message'] = f"Successfully converted and indexed: {filename}"

        except ConversionError as e:
            result['message'] = f"Conversion failed: {e}"
        except Exception as e:
            result['message'] = f"Indexing failed: {e}"

        return result
