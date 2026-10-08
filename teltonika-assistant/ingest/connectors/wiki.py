"""Conector de MediaWiki (Teltonika Telematics Wiki).

Usa la API oficial de MediaWiki en lugar de hacer scraping de paginas.
OJO: hay que confirmar en la primera ejecucion que /api.php esta habilitado.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from urllib.parse import quote

import requests


@dataclass
class WikiPage:
    title: str
    revision_id: int
    html: str
    links: list[str] = field(default_factory=list)
    url: str = ""


class MediaWikiConnector:
    def __init__(self, base_url: str, api_path: str, user_agent: str, min_interval: float = 1.5):
        self.base_url = base_url.rstrip("/")
        self.api_url = self.base_url + api_path
        self.min_interval = min_interval
        self._last = 0.0
        self.session = requests.Session()
        self.session.headers["User-Agent"] = user_agent

    def _get(self, params: dict) -> dict:
        wait = self.min_interval - (time.monotonic() - self._last)
        if wait > 0:
            time.sleep(wait)
        resp = self.session.get(self.api_url, params={**params, "format": "json", "formatversion": 2}, timeout=30)
        self._last = time.monotonic()
        resp.raise_for_status()
        data = resp.json()
        if "error" in data:
            raise RuntimeError(f"MediaWiki API error: {data['error']}")
        return data

    def page_url(self, title: str) -> str:
        return f"{self.base_url}/view/{quote(title.replace(' ', '_'), safe='/:()')}"

    def get_page(self, title: str) -> WikiPage | None:
        try:
            data = self._get({
                "action": "parse", "page": title, "redirects": 1,
                "prop": "text|revid|links", "disableeditsection": 1, "disablelimitreport": 1,
            })
        except RuntimeError as exc:
            if "missingtitle" in str(exc):
                return None
            raise
        p = data["parse"]
        links = [l["title"] for l in p.get("links", []) if l.get("ns") == 0 and l.get("exists")]
        return WikiPage(title=p["title"], revision_id=p["revid"], html=p["text"], links=links,
                        url=self.page_url(p["title"]))

    def latest_revisions(self, titles: list[str]) -> dict[str, int]:
        """Deteccion barata de cambios: hasta 50 titulos por llamada."""
        out: dict[str, int] = {}
        for i in range(0, len(titles), 50):
            batch = titles[i:i + 50]
            data = self._get({"action": "query", "prop": "info", "titles": "|".join(batch), "redirects": 1})
            for page in data["query"]["pages"]:
                if not page.get("missing"):
                    out[page["title"]] = page["lastrevid"]
        return out
