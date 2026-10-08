"""Conector de PDF: texto por pagina con PyMuPDF y tablas con pdfplumber."""
from __future__ import annotations

import hashlib
from pathlib import Path

from ..models import NormalizedDocument, Section, Table


def extract_pdf(path: str | Path, model_code: str | None = None) -> NormalizedDocument:
    import fitz        # PyMuPDF
    import pdfplumber

    path = Path(path)
    raw = path.read_bytes()
    sections: list[Section] = []
    with fitz.open(stream=raw, filetype="pdf") as doc, pdfplumber.open(path) as plumber:
        for i, page in enumerate(doc):
            sec = Section(path=[path.stem, f"page {i + 1}"])
            text = page.get_text("text").strip()
            if text:
                sec.paragraphs.append(text)
            for rows in plumber.pages[i].extract_tables():
                clean = [[(c or "").strip() for c in r] for r in rows if any(r)]
                if clean:
                    sec.tables.append(Table(headers=clean[0], rows=clean[1:]))
            if not sec.is_empty():
                sections.append(sec)
    return NormalizedDocument(
        source_kind="pdf", external_id=path.name, title=path.stem, url=None, breadcrumb=[],
        revision_id=None, content_hash=hashlib.sha256(raw).hexdigest(), sections=sections,
        raw_path=str(path), model_code=model_code,
    )
