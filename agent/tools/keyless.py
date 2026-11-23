"""Keyless fallbacks: ddgs web search and plain httpx + trafilatura fetch."""

from __future__ import annotations

import logging
import re

from agent.tools.base import Page, SearchResult

log = logging.getLogger(__name__)

USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)


class DDGSSearch:
    """ddgs metasearch. Backends are tried in order; on this box only yahoo returns results."""

    name = "ddgs"

    def __init__(self, backends: tuple[str, ...] = ("yahoo",)):
        self.backends = backends

    def search(self, query: str, max_results: int = 5) -> tuple[str, list[SearchResult]]:
        from ddgs import DDGS

        for backend in self.backends:
            try:
                raw = DDGS().text(query, max_results=max_results, backend=backend)
            except Exception as e:  # ddgs raises on "No results found" and on blocks
                log.debug("ddgs backend %s failed: %s", backend, type(e).__name__)
                continue
            results = [
                SearchResult(url=r["href"], title=r.get("title", ""), snippet=r.get("body", ""))
                for r in raw or []
                if r.get("href")
            ]
            if results:
                return f"ddgs:{backend}", results[:max_results]
        return self.name, []


class HttpFetch:
    name = "http"

    def __init__(self, timeout: float = 20.0, max_chars: int = 20000, client=None):
        self.timeout = timeout
        self.max_chars = max_chars
        self._client = client

    def _get_client(self):
        if self._client is None:
            import httpx

            self._client = httpx.Client(
                follow_redirects=True,
                timeout=self.timeout,
                headers={"User-Agent": USER_AGENT, "Accept-Language": "en-US,en;q=0.9"},
            )
        return self._client

    def fetch(self, url: str) -> Page:
        try:
            r = self._get_client().get(url)
        except Exception as e:
            return Page(url, url, 0, self.name, error=f"{type(e).__name__}: {str(e)[:200]}")
        ctype = r.headers.get("content-type", "")
        body = r.text if ("text" in ctype or "json" in ctype or "xml" in ctype or not ctype) else ""
        text = html_to_text(body, str(r.url)) if "html" in ctype or body.lstrip().startswith("<") else body
        return Page(url, str(r.url), r.status_code, self.name, text=text[: self.max_chars])


def html_to_text(html: str, url: str | None = None) -> str:
    if not html:
        return ""
    try:
        import trafilatura

        text = trafilatura.extract(html, url=url, include_comments=False, include_tables=True, favor_recall=True)
        if text:
            return text
    except Exception as e:
        log.debug("trafilatura failed: %s", type(e).__name__)
    # Crude fallback so a page trafilatura rejects still yields searchable text.
    text = re.sub(r"(?is)<(script|style|noscript).*?</\1>", " ", html)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text).strip()
