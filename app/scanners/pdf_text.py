"""Bounded extraction of public PDF tender notices.

This module deliberately does not OCR images or execute PDF content.
It is used only for already approved, robots-allowed publisher URLs.
"""
from __future__ import annotations

from io import BytesIO

import requests
from pypdf import PdfReader


MAX_PDF_BYTES = 8_000_000
MAX_PDF_PAGES = 24
MAX_EXTRACTED_CHARS = 30_000


def extract_pdf_text(
    session: requests.Session,
    url: str,
    *,
    max_bytes: int = MAX_PDF_BYTES,
    max_pages: int = MAX_PDF_PAGES,
    max_chars: int = MAX_EXTRACTED_CHARS,
) -> str:
    """Fetch a small PDF and return text, or empty string when unreadable.

    Streaming avoids loading arbitrarily large files. The caller checks
    robots.txt and same-domain restrictions before invoking this method.
    """
    response = session.get(url, timeout=(6, 22), stream=True)
    try:
        response.raise_for_status()
        content_type = response.headers.get("Content-Type", "").lower()
        if content_type and not (
            "application/pdf" in content_type
            or "application/octet-stream" in content_type
        ):
            return ""
        length = response.headers.get("Content-Length")
        if length:
            try:
                if int(length) > max_bytes:
                    return ""
            except ValueError:
                return ""

        buffer = bytearray()
        for chunk in response.iter_content(chunk_size=65_536):
            buffer.extend(chunk)
            if len(buffer) > max_bytes:
                return ""
        if not buffer.startswith(b"%PDF-"):
            return ""

        try:
            reader = PdfReader(BytesIO(buffer), strict=False)
            texts: list[str] = []
            extracted = 0
            for page in reader.pages[:max_pages]:
                try:
                    text = page.extract_text() or ""
                except Exception:
                    continue
                if text:
                    text = " ".join(text.split())
                    texts.append(text[: max_chars - extracted])
                    extracted += len(texts[-1])
                    if extracted >= max_chars:
                        break
        except Exception:
            return ""
        return "\n".join(texts).strip()
    finally:
        response.close()
