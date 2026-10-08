"""Formato interno comun: tanto wiki como PDF se normalizan a esto."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Table:
    headers: list[str]
    rows: list[list[str]]


@dataclass
class Section:
    path: list[str]                       # ruta de titulos, p.ej. ['FMB920 Manual', 'SMS commands']
    paragraphs: list[str] = field(default_factory=list)
    tables: list[Table] = field(default_factory=list)

    @property
    def text(self) -> str:
        return "\n\n".join(p for p in self.paragraphs if p.strip())

    def is_empty(self) -> bool:
        return not self.text and not self.tables


@dataclass
class NormalizedDocument:
    source_kind: str                      # 'wiki' | 'pdf'
    external_id: str                      # titulo de pagina o nombre de archivo
    title: str
    url: str | None
    breadcrumb: list[str]
    revision_id: int | None
    content_hash: str
    sections: list[Section]
    language: str = "en"
    raw_path: str | None = None
    model_code: str | None = None         # modelo detectado por la RUTA (no por el texto)
