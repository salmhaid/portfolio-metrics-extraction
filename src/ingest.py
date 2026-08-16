"""Ingest: PDF -> text per page (pdfplumber). Files are text-native; no OCR (see spec)."""

from __future__ import annotations

import logging
from pathlib import Path

import pdfplumber

log = logging.getLogger("ingest")


def ingest_pdf(path: Path) -> list[str]:
    """Return extracted text for each page of the PDF."""
    pages: list[str] = []
    with pdfplumber.open(path) as pdf:
        for page in pdf.pages:
            pages.append(page.extract_text() or "")
    if not any(pages):
        raise ValueError(f"No extractable text in {path.name} (scanned PDF? OCR is out of scope)")
    log.debug("%s: %d pages, %d chars", path.name, len(pages), sum(len(p) for p in pages))
    return pages
