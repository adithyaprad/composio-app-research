"""Pluggable search/fetch providers.

SEARCH_PROVIDER = auto | composio_exa | composio_tavily | ddgs
FETCH_PROVIDER  = auto | composio_firecrawl | http

`auto` tries Composio first. On the first ProviderUnavailable (no connection,
410 retired toolkit, 400/401/403) it logs once and uses the keyless fallback for
the rest of the run instead of retrying Composio on every call.
"""

from __future__ import annotations

import logging
import os
import threading
from typing import Any

from agent.config import Settings
from agent.tools.base import FetchProvider, Page, ProviderUnavailable, SearchProvider, SearchResult
from agent.tools.composio_tools import ComposioFetch, ComposioSearch
from agent.tools.keyless import DDGSSearch, HttpFetch

log = logging.getLogger(__name__)


class Fallback:
    """Use `primary` until it raises ProviderUnavailable once, then `fallback` for good."""

    def __init__(self, primary: Any, fallback: Any, kind: str):
        self.primary = primary
        self.fallback = fallback
        self.kind = kind
        self.disabled_reason: str | None = None
        self._lock = threading.Lock()

    def _disable(self, reason: str) -> None:
        with self._lock:
            if self.disabled_reason is None:
                self.disabled_reason = reason
                log.warning(
                    "%s: %s unavailable, falling back to %s for this run (%s)",
                    self.kind,
                    getattr(self.primary, "name", "primary"),
                    getattr(self.fallback, "name", "fallback"),
                    reason,
                )

    def _call(self, method: str, *args, **kwargs):
        if self.disabled_reason is None:
            try:
                return getattr(self.primary, method)(*args, **kwargs)
            except ProviderUnavailable as e:
                self._disable(str(e)[:200])
        return getattr(self.fallback, method)(*args, **kwargs)

    def search(self, query: str, max_results: int = 5) -> tuple[str, list[SearchResult]]:
        return self._call("search", query, max_results)

    def fetch(self, url: str) -> Page:
        return self._call("fetch", url)


def _has_composio_key() -> bool:
    return bool(os.environ.get("COMPOSIO_API_KEY", "").strip())


def get_search(settings: Settings, composio_client: Any = None) -> SearchProvider:
    ddgs = DDGSSearch(settings.ddgs_backends)
    choice = settings.search_provider
    if choice == "ddgs":
        return ddgs
    if choice == "composio_exa":
        return ComposioSearch("EXA_SEARCH", settings.composio_user_id, composio_client)
    if choice == "composio_tavily":
        return ComposioSearch("TAVILY_SEARCH", settings.composio_user_id, composio_client)
    if composio_client is None and not _has_composio_key():
        log.info("search: COMPOSIO_API_KEY not set, using ddgs")
        return ddgs
    return Fallback(ComposioSearch("EXA_SEARCH", settings.composio_user_id, composio_client), ddgs, "search")


def get_fetch(settings: Settings, composio_client: Any = None) -> FetchProvider:
    http = HttpFetch(settings.http_timeout, settings.max_page_chars)
    choice = settings.fetch_provider
    if choice == "http":
        return http
    firecrawl = ComposioFetch(settings.composio_user_id, settings.max_page_chars, composio_client)
    if choice == "composio_firecrawl":
        return firecrawl
    if composio_client is None and not _has_composio_key():
        log.info("fetch: COMPOSIO_API_KEY not set, using http")
        return http
    return Fallback(firecrawl, http, "fetch")


__all__ = [
    "Fallback",
    "Page",
    "ProviderUnavailable",
    "SearchResult",
    "get_fetch",
    "get_search",
]
