"""Persistencia en PostgreSQL (psycopg 3)."""
from __future__ import annotations

import json

from .models import NormalizedDocument
from .parsing import make_chunks


def ensure_catalog(conn, manufacturer: str, family: str, model_code: str, wiki_title: str) -> str:
    """Crea fabricante/familia/modelo si no existen y devuelve model_id."""
    cur = conn.cursor()
    cur.execute("INSERT INTO manufacturer (name) VALUES (%s) ON CONFLICT (name) DO UPDATE SET name = EXCLUDED.name RETURNING id",
                (manufacturer,))
    manufacturer_id = cur.fetchone()[0]
    cur.execute("""INSERT INTO device_family (manufacturer_id, code) VALUES (%s, %s)
                   ON CONFLICT (manufacturer_id, code) DO UPDATE SET code = EXCLUDED.code RETURNING id""",
                (manufacturer_id, family))
    family_id = cur.fetchone()[0]
    cur.execute("""INSERT INTO device_model (family_id, code, wiki_title) VALUES (%s, %s, %s)
                   ON CONFLICT (family_id, code) DO UPDATE SET wiki_title = EXCLUDED.wiki_title RETURNING id""",
                (family_id, model_code, wiki_title))
    return cur.fetchone()[0]


def ensure_source(conn, name: str, kind: str, base_url: str, license_note: str) -> str:
    cur = conn.cursor()
    cur.execute("""INSERT INTO source (name, kind, base_url, license_note) VALUES (%s, %s, %s, %s)
                   ON CONFLICT (name) DO UPDATE SET base_url = EXCLUDED.base_url RETURNING id""",
                (name, kind, base_url, license_note))
    return cur.fetchone()[0]


def save_document(conn, source_id: str, doc: NormalizedDocument, model_id: str, target_model: str,
                  family_pages: set[str]) -> str:
    """Guarda un documento nuevo o cambiado. Devuelve 'new', 'updated' o 'unchanged'."""
    cur = conn.cursor()
    cur.execute("SELECT id, content_hash FROM source_document WHERE source_id = %s AND external_id = %s AND is_current",
                (source_id, doc.external_id))
    row = cur.fetchone()
    if row and row[1] == doc.content_hash:
        return "unchanged"
    outcome = "updated" if row else "new"
    if row:
        cur.execute("UPDATE source_document SET is_current = false WHERE id = %s", (row[0],))

    cur.execute("""
        INSERT INTO source_document
          (source_id, external_id, url, title, breadcrumb, language, revision_id, content_hash, raw_path, is_current)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, true)
        ON CONFLICT (source_id, external_id, content_hash)
        DO UPDATE SET is_current = true, fetched_at = now()
        RETURNING id""",
        (source_id, doc.external_id, doc.url, doc.title, doc.breadcrumb, doc.language,
         doc.revision_id, doc.content_hash, doc.raw_path))
    document_id = cur.fetchone()[0]
    cur.execute("DELETE FROM chunk WHERE document_id = %s", (document_id,))
    cur.execute("DELETE FROM document_table WHERE document_id = %s", (document_id,))

    # Aplicabilidad: por ruta (confiable) o por pagina de familia (requiere revision).
    if doc.model_code == target_model:
        basis, status = "breadcrumb", "verified"
    elif doc.model_code is None and doc.external_id in family_pages:
        basis, status = "family_page", "draft"
    else:
        basis = status = None

    for ordinal, (path, content) in enumerate(make_chunks(doc.sections)):
        cur.execute("INSERT INTO chunk (document_id, ordinal, section_path, content) VALUES (%s, %s, %s, %s) RETURNING id",
                    (document_id, ordinal, path, content))
        chunk_id = cur.fetchone()[0]
        if basis:
            cur.execute("""INSERT INTO chunk_applicability (chunk_id, model_id, basis, status)
                           VALUES (%s, %s, %s, %s)""", (chunk_id, model_id, basis, status))

    ordinal = 0
    for sec in doc.sections:
        for tbl in sec.tables:
            cur.execute("""INSERT INTO document_table (document_id, ordinal, section_path, headers, rows)
                           VALUES (%s, %s, %s, %s, %s)""",
                        (document_id, ordinal, sec.path, json.dumps(tbl.headers), json.dumps(tbl.rows)))
            ordinal += 1
    return outcome
