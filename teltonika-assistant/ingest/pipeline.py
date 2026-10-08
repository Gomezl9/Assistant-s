"""Pipeline de ingesta: wiki -> documentos normalizados -> PostgreSQL.

Uso:
    python -m ingest.pipeline --plan crawl_plan.yaml --dry-run
    python -m ingest.pipeline --plan crawl_plan.yaml --dsn postgresql://user:pass@localhost/assistant
"""
from __future__ import annotations

import argparse
import hashlib
import re
from collections import deque
from pathlib import Path

import yaml

from .connectors.wiki import MediaWikiConnector, WikiPage
from .models import NormalizedDocument
from .parsing import clean_soup, extract_breadcrumb, make_chunks, model_from_breadcrumb, split_sections


def crawl(wiki: MediaWikiConnector, plan: dict):
    """Recorre paginas semilla y sigue enlaces SOLO dentro de los prefijos permitidos."""
    cfg = plan["follow_links"]
    prefixes = tuple(cfg["prefixes"])
    queue = deque((t, 0) for t in plan["seed_pages"])
    seen: set[str] = set()
    count = 0
    while queue and count < cfg["max_pages"]:
        title, depth = queue.popleft()
        key = title.replace("_", " ")
        if key in seen:
            continue
        seen.add(key)
        page = wiki.get_page(title)
        if page is None:
            print(f"  ! pagina no encontrada (revisar semilla): {title}")
            continue
        seen.add(page.title)
        count += 1
        yield page, depth
        if depth < cfg["max_depth"]:
            for link in page.links:
                if link not in seen and link.startswith(prefixes):
                    queue.append((link, depth + 1))


def to_document(page: WikiPage, known_models: set[str], raw_dir: Path) -> NormalizedDocument:
    soup = clean_soup(page.html)
    breadcrumb = extract_breadcrumb(soup)
    sections = split_sections(soup, page.title)
    raw_dir.mkdir(parents=True, exist_ok=True)
    slug = re.sub(r"[^A-Za-z0-9]+", "_", page.title).strip("_")
    raw_path = raw_dir / f"{page.revision_id}_{slug}.html"
    raw_path.write_text(page.html, encoding="utf-8")
    return NormalizedDocument(
        source_kind="wiki", external_id=page.title, title=page.title, url=page.url,
        breadcrumb=breadcrumb, revision_id=page.revision_id,
        content_hash=hashlib.sha256(page.html.encode("utf-8")).hexdigest(),
        sections=sections, raw_path=str(raw_path),
        model_code=model_from_breadcrumb(breadcrumb, known_models),
    )


def run(plan_path: str, dry_run: bool, dsn: str | None, raw_dir: str) -> None:
    plan = yaml.safe_load(Path(plan_path).read_text(encoding="utf-8"))
    w, m = plan["wiki"], plan["model"]
    wiki = MediaWikiConnector(w["base_url"], w["api_path"], w["user_agent"], w["min_interval_seconds"])
    known = {m["code"]}
    family_pages = set(plan["seed_pages"]) - {m["code"]}

    conn = source_id = model_id = None
    if not dry_run:
        import psycopg
        from . import store
        conn = psycopg.connect(dsn)
        model_id = store.ensure_catalog(conn, m["manufacturer"], m["family"], m["code"], m["code"])
        source_id = store.ensure_source(conn, w["source_name"], "wiki", w["base_url"], w.get("license_note", ""))

    totals = {"new": 0, "updated": 0, "unchanged": 0}
    for page, depth in crawl(wiki, plan):
        doc = to_document(page, known, Path(raw_dir))
        n_tables = sum(len(s.tables) for s in doc.sections)
        n_chunks = len(make_chunks(doc.sections))
        bc = " > ".join(doc.breadcrumb) or "(sin ruta detectada)"
        print(f"[d{depth}] {doc.title} | rev {doc.revision_id} | modelo por ruta: {doc.model_code} | "
              f"{len(doc.sections)} secciones, {n_tables} tablas, {n_chunks} fragmentos\n       ruta: {bc}")
        if not dry_run:
            totals[store.save_document(conn, source_id, doc, model_id, m["code"], family_pages)] += 1
            conn.commit()
    if not dry_run:
        print("Resumen:", totals)
        conn.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--plan", default="crawl_plan.yaml")
    ap.add_argument("--dry-run", action="store_true", help="No escribe en la base; solo reporta lo que encontraria")
    ap.add_argument("--dsn", help="cadena de conexion a PostgreSQL")
    ap.add_argument("--raw-dir", default="data/raw")
    args = ap.parse_args()
    if not args.dry_run and not args.dsn:
        ap.error("--dsn es obligatorio salvo con --dry-run")
    run(args.plan, args.dry_run, args.dsn, args.raw_dir)
