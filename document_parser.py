"""Extract text from PDF, DOCX, PPTX, and TXT files for chat context."""

import base64
import os
from pathlib import Path

# If pypdf extracts less than this many chars, treat PDF as image-based and try OCR
MIN_TEXT_CHARS_FOR_PDF = 20
MAX_OCR_PAGES = 20  # Limit OCR pages to avoid long processing (scanned PDFs)

OCR_PROMPT = (
    "Extract all text from this image. "
    "Return only the raw text, preserving paragraphs and structure. "
    "Do not add any commentary or formatting."
)


def _extract_pdf_with_ocr(path: Path) -> str:
    """Extract text from image-based (scanned) PDF using Claude Vision."""
    import fitz  # pymupdf

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY not set — required for image-based PDF OCR")

    import anthropic

    client = anthropic.Anthropic(api_key=api_key)
    doc = fitz.open(path)
    parts: list[str] = []
    pages_to_process = min(len(doc), MAX_OCR_PAGES)

    for i in range(pages_to_process):
        page = doc[i]
        pix = page.get_pixmap(dpi=150)
        png_bytes = pix.tobytes("png")
        b64 = base64.b64encode(png_bytes).decode("utf-8")

        response = client.messages.create(
            model="claude-3-5-haiku-20241022",
            max_tokens=4096,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": "image/png",
                                "data": b64,
                            },
                        },
                        {"type": "text", "text": OCR_PROMPT},
                    ],
                }
            ],
        )
        text = ""
        for block in response.content:
            if hasattr(block, "text") and block.text:
                text += block.text
        text = text.strip()
        if text:
            parts.append(text)

    doc.close()
    return "\n\n".join(parts)


def extract_text_from_file(file_path: Path) -> str:
    """
    Extract text from PDF, DOCX, PPTX, or TXT.
    For PDFs: tries text extraction first; if empty (image-based), falls back to OCR.
    Returns plain text. Raises ValueError for unsupported formats.
    """
    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"File not found: {path}")

    suffix = path.suffix.lower()

    if suffix == ".txt":
        return path.read_text(encoding="utf-8", errors="replace")

    if suffix == ".pdf":
        from pypdf import PdfReader

        reader = PdfReader(path)
        if reader.is_encrypted:
            try:
                reader.decrypt("")
            except Exception:
                pass

        parts = []
        for page in reader.pages:
            text = page.extract_text()
            if text:
                parts.append(text)
        text = "\n".join(parts) if parts else ""

        if len(text.strip()) < MIN_TEXT_CHARS_FOR_PDF and len(reader.pages) > 0:
            try:
                ocr_text = _extract_pdf_with_ocr(path)
                if ocr_text:
                    return ocr_text
            except Exception:
                pass

        return text

    if suffix == ".docx":
        from docx import Document

        doc = Document(path)
        return "\n".join(p.text for p in doc.paragraphs)

    if suffix in (".ppt", ".pptx"):
        from pptx import Presentation

        prs = Presentation(path)
        parts = []
        for slide in prs.slides:
            for shape in slide.shapes:
                if hasattr(shape, "text") and shape.text:
                    parts.append(shape.text)
        return "\n".join(parts)

    raise ValueError(f"Unsupported document format: {suffix}")
