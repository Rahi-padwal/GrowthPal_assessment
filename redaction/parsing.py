import io

from docx import Document
import pdfplumber

SUPPORTED_EXTENSIONS = {"txt", "docx", "pdf"}


class UnsupportedFileTypeError(Exception):
    """Raised when the uploaded file's extension isn't one we support."""


class EmptyFileError(Exception):
    """Raised when the uploaded file has no extractable text."""


def parse_file(filename: str, content: bytes) -> str:
    """Extract raw text from an uploaded .txt/.docx/.pdf file.

    Raises UnsupportedFileTypeError / EmptyFileError with clear, user-facing
    messages instead of letting a parser exception bubble up as a 500.
    """
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""

    if ext not in SUPPORTED_EXTENSIONS:
        raise UnsupportedFileTypeError(
            f"Unsupported file type '.{ext or 'unknown'}'. Supported types: .txt, .docx, .pdf"
        )

    if ext == "txt":
        text = content.decode("utf-8", errors="replace")
    elif ext == "docx":
        document = Document(io.BytesIO(content))
        text = "\n".join(paragraph.text for paragraph in document.paragraphs)
    else:  # pdf
        text_parts = []
        with pdfplumber.open(io.BytesIO(content)) as pdf:
            for page in pdf.pages:
                page_text = page.extract_text()
                if page_text:
                    text_parts.append(page_text)
        text = "\n".join(text_parts)

    if not text or not text.strip():
        raise EmptyFileError("The uploaded file contains no extractable text.")

    return text
