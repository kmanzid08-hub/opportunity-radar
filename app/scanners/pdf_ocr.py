"""Last-resort OCR for small image-only public tender PDFs.

Never OCR a PDF with selectable text. Keep CPU, pages and output bounded;
reuse prior text by document hash between daily GitHub Actions scans.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil

MAX_OCR_PDF_BYTES = 3_000_000
MAX_OCR_PAGES = 3
MAX_OCR_CHARS = 20_000
MAX_UNCACHED_DOCS_PER_RUN = 32
MAX_CACHE_FILE_BYTES = 5_000_000

_uncached_ocr_count = 0


def _load_cache(path: Path) -> dict[str, str]:
    if not path.is_file() or path.stat().st_size > MAX_CACHE_FILE_BYTES:
        return {}
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(loaded, dict):
            return {
                str(key): str(value)
                for key, value in loaded.items()
                if isinstance(key, str)
                and len(key) == 64
                and isinstance(value, str)
            }
    except (OSError, ValueError, TypeError):
        pass
    return {}


def _save_cache(path: Path, cache: dict[str, str]) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        data = json.dumps(cache, ensure_ascii=False)
        if len(data.encode("utf-8")) > MAX_CACHE_FILE_BYTES:
            return
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(data, encoding="utf-8")
        temporary.replace(path)
    except OSError:
        return


def _recognize(pdf_bytes: bytes, *, max_pages: int, max_chars: int) -> str:
    """Render only the initial pages; Tesseract handles their image pixels."""
    import pypdfium2 as pdfium
    import pytesseract

    document = pdfium.PdfDocument(pdf_bytes)
    results: list[str] = []
    remaining = max_chars
    try:
        for index in range(min(len(document), max_pages)):
            page = document[index]
            try:
                bitmap = page.render(scale=1.8)
                try:
                    image = bitmap.to_pil()
                    try:
                        text = pytesseract.image_to_string(
                            image, lang="eng", config="--psm 3"
                        )
                    finally:
                        image.close()
                finally:
                    bitmap.close()
            finally:
                page.close()
            clean = " ".join((text or "").split())
            if clean:
                fragment = clean[:remaining]
                results.append(fragment)
                remaining -= len(fragment)
                if remaining <= 0:
                    break
    finally:
        document.close()
    return "\n".join(results).strip()


def ocr_image_only_pdf(pdf_bytes: bytes) -> str:
    """Return OCR text or an empty string without breaking normal scanning."""
    global _uncached_ocr_count

    if (
        not pdf_bytes.startswith(b"%PDF-")
        or len(pdf_bytes) > MAX_OCR_PDF_BYTES
    ):
        return ""

    cache_path_str = os.getenv("PDF_OCR_CACHE_PATH", "").strip()
    cache_path = Path(cache_path_str) if cache_path_str else None
    fingerprint = hashlib.sha256(pdf_bytes).hexdigest()
    if cache_path is not None:
        cached = _load_cache(cache_path).get(fingerprint)
        if cached is not None:
            return cached

    # Hard cap controls OCR CPU on large daily crawls and preserves scan time.
    if _uncached_ocr_count >= MAX_UNCACHED_DOCS_PER_RUN:
        return ""
    if not shutil.which("tesseract"):
        return ""

    _uncached_ocr_count += 1
    try:
        extracted = _recognize(
            pdf_bytes,
            max_pages=MAX_OCR_PAGES,
            max_chars=MAX_OCR_CHARS,
        )
    except Exception as exc:
        print(f"  Image-only PDF OCR unavailable: {type(exc).__name__}")
        return ""

    if cache_path is not None and extracted:
        cache = _load_cache(cache_path)
        cache[fingerprint] = extracted
        _save_cache(cache_path, cache)
    return extracted
