"""Parseo de HTML de MediaWiki a secciones y tablas estructuradas.

Reglas de diseno:
  * El modelo de dispositivo se deduce de la ruta (breadcrumb), nunca de menciones en el texto.
  * Las tablas se conservan como filas/columnas, no como texto aplanado.
  * Los bloques <pre> (comandos) se conservan literales.
"""
from __future__ import annotations

import re

from bs4 import BeautifulSoup

from .models import Section, Table

_HEADING_LEVEL = {"h1": 1, "h2": 2, "h3": 3, "h4": 4, "h5": 5}
_BLOCKS = ["h1", "h2", "h3", "h4", "h5", "p", "ul", "ol", "dl", "pre", "table"]
_NOISE = [".mw-editsection", "#toc", ".toc", "style", "script", ".navbox", ".mw-empty-elt"]
_BC_START = re.compile(r"^\s*Main Page\s*[>›»]")
_BC_SPLIT = re.compile(r"\s*[>›»]\s*")


def clean_soup(html: str) -> BeautifulSoup:
    soup = BeautifulSoup(html, "html.parser")
    for selector in _NOISE:
        for el in soup.select(selector):
            el.decompose()
    return soup


def extract_breadcrumb(soup: BeautifulSoup) -> list[str]:
    """Busca la ruta 'Main Page > Categoria > Modelo > ...'. Se elige el elemento mas pequeno que la contiene."""
    best: str | None = None
    for el in soup.find_all(["div", "p", "span", "td"]):
        text = el.get_text(" ", strip=True)
        if _BC_START.match(text) and len(text) < 300 and (best is None or len(text) < len(best)):
            best = text
    if not best:
        return []
    return [p for p in _BC_SPLIT.split(best) if p]


def model_from_breadcrumb(breadcrumb: list[str], known_codes: set[str]) -> str | None:
    for part in breadcrumb:
        if part in known_codes:
            return part
    return None


def table_to_table(el) -> Table:
    rows: list[list[str]] = []
    header_row = False
    for i, tr in enumerate(el.find_all("tr")):
        cells = [c.get_text(" ", strip=True) for c in tr.find_all(["th", "td"])]
        if not any(cells):
            continue
        if not rows and tr.find("th") and not tr.find("td"):
            header_row = True
        rows.append(cells)
    if not rows:
        return Table([], [])
    if header_row:
        return Table(rows[0], rows[1:])
    return Table([], rows)


def split_sections(soup: BeautifulSoup, root_title: str) -> list[Section]:
    root = soup.find("div", class_="mw-parser-output") or soup
    heading_stack: list[tuple[int, str]] = []
    sections: list[Section] = []
    current = Section(path=[root_title])

    def flush() -> None:
        nonlocal current
        if not current.is_empty():
            sections.append(current)

    for el in root.find_all(_BLOCKS):
        if el.name == "table":
            if el.find_parent("table"):
                continue
        elif el.find_parent(["table", "ul", "ol", "dl"]):
            continue

        if el.name in _HEADING_LEVEL:
            title = el.get_text(" ", strip=True)
            if not title:
                continue
            level = _HEADING_LEVEL[el.name]
            flush()
            while heading_stack and heading_stack[-1][0] >= level:
                heading_stack.pop()
            heading_stack.append((level, title))
            current = Section(path=[root_title] + [t for _, t in heading_stack])
        elif el.name == "table":
            tbl = table_to_table(el)
            if tbl.rows:
                current.tables.append(tbl)
        elif el.name == "pre":
            code = el.get_text().strip("\n")
            if code.strip():
                current.paragraphs.append(f"```\n{code}\n```")
        elif el.name in ("ul", "ol"):
            items = [li.get_text(" ", strip=True) for li in el.find_all("li", recursive=False)]
            text = "\n".join(f"- {i}" for i in items if i)
            if text:
                current.paragraphs.append(text)
        else:  # p, dl
            text = el.get_text(" ", strip=True)
            if text:
                current.paragraphs.append(text)

    flush()
    return sections


def render_table(tbl: Table) -> str:
    lines = []
    if tbl.headers:
        lines.append(" | ".join(tbl.headers))
    lines.extend(" | ".join(r) for r in tbl.rows)
    return "\n".join(lines)


def make_chunks(sections: list[Section], max_chars: int = 1500) -> list[tuple[list[str], str]]:
    """Devuelve [(ruta_de_secciones, texto)]. Corta por parrafos, nunca a mitad de una tabla pequena."""
    chunks: list[tuple[list[str], str]] = []
    for sec in sections:
        blocks = list(sec.paragraphs) + [render_table(t) for t in sec.tables]
        buf = ""
        for block in blocks:
            if buf and len(buf) + len(block) + 2 > max_chars:
                chunks.append((sec.path, buf))
                buf = ""
            buf = f"{buf}\n\n{block}" if buf else block
        if buf.strip():
            chunks.append((sec.path, buf))
    return chunks
