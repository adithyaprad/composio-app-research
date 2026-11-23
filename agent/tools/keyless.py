"""Keyless fallbacks: ddgs web search and plain httpx + trafilatura fetch."""

from __future__ import annotations

import logging
import re
from urllib.parse import urlsplit, urlunsplit

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
        final = str(r.url)
        ctype = r.headers.get("content-type", "")
        body = r.text if ("text" in ctype or "json" in ctype or "xml" in ctype or not ctype) else ""
        if not ("html" in ctype or body.lstrip().startswith("<")):
            return Page(url, final, r.status_code, self.name, text=body[: self.max_chars], method="raw")

        method, text = "trafilatura", _trafilatura(body, final, recall=False)
        source = None
        if len(text) < THIN_CHARS and r.status_code < 400:
            candidates = [(method, text, None), ("trafilatura_recall", _trafilatura(body, final, recall=True), None)]
            if is_docs_url(final):
                twin_url, twin = self._md_twin(final)
                candidates.append(("md_twin", twin, twin_url))
            candidates.append(("visible_text", visible_text(body), None))
            # First candidate that is no longer thin, in preference order; otherwise the longest.
            good = [c for c in candidates if len(c[1]) >= THIN_CHARS]
            method, text, source = good[0] if good else max(candidates, key=lambda c: len(c[1]))
        return Page(url, final, r.status_code, self.name, text=text[: self.max_chars], method=method,
                    text_source_url=source)

    def _md_twin(self, url: str) -> tuple[str | None, str]:
        """Many docs platforms (Mintlify, GitBook, ...) serve `<page>.md` as raw Markdown."""
        p = urlsplit(url)
        path = p.path.rstrip("/")
        if not path or path.endswith(".md"):
            return None, ""
        twin = urlunsplit((p.scheme, p.netloc, path + ".md", "", ""))
        try:
            r = self._get_client().get(twin)
        except Exception:
            return twin, ""
        if r.status_code >= 400 or "html" in r.headers.get("content-type", "") or r.text.lstrip().startswith("<"):
            return twin, ""
        return twin, r.text


THIN_CHARS = 1500
_DOCS_HOST = re.compile(r"^(docs?|developers?|dev-docs|api|api-docs|apidocs|reference|help)[.-]")
_DOCS_PATH = re.compile(r"/(docs?|api|reference|developers?|guides?)(/|$)")


def is_docs_url(url: str) -> bool:
    p = urlsplit(url)
    return bool(_DOCS_HOST.match(p.hostname or "") or _DOCS_PATH.search(p.path))


def _trafilatura(html: str, url: str | None, recall: bool) -> str:
    try:
        import trafilatura

        return trafilatura.extract(html, url=url, include_comments=False, include_tables=True,
                                   favor_recall=recall) or ""
    except Exception as e:
        log.debug("trafilatura failed: %s", type(e).__name__)
        return ""


def visible_text(html: str) -> str:
    """All visible text (scripts, styles, templates removed). Noisy, but keeps JS-shell pages non-empty."""
    try:
        import lxml.html

        doc = lxml.html.fromstring(html)
        for bad in doc.xpath("//script|//style|//noscript|//template|//svg"):
            bad.drop_tree()
        text = doc.text_content()
    except Exception:
        text = re.sub(r"(?is)<(script|style|noscript|template).*?</\1>", " ", html)
        text = re.sub(r"(?s)<[^>]+>", " ", text)
    return re.sub(r"[ \t\r\f\v]+", " ", re.sub(r"\n\s*\n+", "\n", text)).strip()
