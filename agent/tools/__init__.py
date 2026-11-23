"""Pluggable search/fetch providers.

SEARCH_PROVIDER = ddgs (default) | hybrid | auto | composio_exa | composio_tavily
FETCH_PROVIDER  = hybrid (default) | auto | composio_firecrawl | http

- ddgs / http: free only.
- hybrid: free first. Search escalates to Composio Exa only when ddgs returns
  nothing. Fetch escalates a URL to Composio Firecrawl only when the http page is
  thin or blocked.
- auto / composio_*: Composio first, free fallback.

Every Composio call is gated by the per-run InstantBudget (INSTANT_BUDGET_USD).
On the first ProviderUnavailable (no connection, 410 retired toolkit,
400/401/403), Composio is switched off for the rest of the run, logged once.
"""

from __future__ import annotations

import logging
import os
import threading
from typing import Any

from agent.config import ConfigError, Settings
from agent.tools.base import FetchProvider, Page, ProviderUnavailable, SearchProvider, SearchResult
from agent.tools.budget import BudgetExceeded, InstantBudget
from agent.tools.composio_tools import ComposioFetch, ComposioSearch
from agent.tools.keyless import THIN_CHARS, DDGSSearch, HttpFetch

log = logging.getLogger(__name__)

BLOCKED_STATUSES = {0, 401, 403, 429, 503}


class _Switch:
    """Composio on/off for the run: off for good after the first ProviderUnavailable."""

    def __init__(self, kind: str, name: str):
        self.kind, self.name = kind, name
        self.disabled_reason: str | None = None
        self._lock = threading.Lock()

    def disable(self, reason: str, fallback_name: str) -> None:
        with self._lock:
            if self.disabled_reason is None:
                self.disabled_reason = reason
                log.warning("%s: %s unavailable, using %s for the rest of this run (%s)",
                            self.kind, self.name, fallback_name, reason)


class Fallback:
    """Use `primary` until it raises ProviderUnavailable once, then `fallback` for good.

    BudgetExceeded uses the fallback for that call only (the budget logs once itself).
    """

    def __init__(self, primary: Any, fallback: Any, kind: str):
        self.primary = primary
        self.fallback = fallback
        self.kind = kind
        self._switch = _Switch(kind, getattr(primary, "name", "primary"))

    @property
    def disabled_reason(self) -> str | None:
        return self._switch.disabled_reason

    def _call(self, method: str, *args, **kwargs):
        if self._switch.disabled_reason is None:
            try:
                return getattr(self.primary, method)(*args, **kwargs)
            except ProviderUnavailable as e:
                self._switch.disable(str(e)[:200], getattr(self.fallback, "name", "fallback"))
            except BudgetExceeded:
                pass
        return getattr(self.fallback, method)(*args, **kwargs)

    def search(self, query: str, max_results: int = 5) -> tuple[str, list[SearchResult]]:
        return self._call("search", query, max_results)

    def fetch(self, url: str) -> Page:
        return self._call("fetch", url)


class HybridSearch:
    """Free ddgs first; Composio search only when ddgs finds nothing."""

    name = "hybrid"

    def __init__(self, free: Any, paid: Any | None):
        self.free = free
        self.paid = paid
        self._switch = _Switch("search", getattr(paid, "name", "composio"))

    def search(self, query: str, max_results: int = 5) -> tuple[str, list[SearchResult]]:
        name, results = self.free.search(query, max_results)
        if results or self.paid is None or self._switch.disabled_reason:
            return name, results
        try:
            return self.paid.search(query, max_results)
        except ProviderUnavailable as e:
            self._switch.disable(str(e)[:200], "ddgs")
        except BudgetExceeded:
            pass
        except Exception as e:
            log.debug("composio search failed: %s", type(e).__name__)
        return name, results


class HybridFetch:
    """Free http first; escalate a URL to Composio Firecrawl only when the page is thin or blocked."""

    name = "hybrid"

    def __init__(self, free: Any, paid: Any | None):
        self.free = free
        self.paid = paid
        self._switch = _Switch("fetch", getattr(paid, "name", "composio_firecrawl"))

    @staticmethod
    def needs_escalation(page: Page) -> bool:
        if page.status in BLOCKED_STATUSES:  # 0 = request failed (timeout, DNS, TLS, ...)
            return True
        if page.error:  # other HTTP errors (404, 410, 500): a real answer, not worth paying to re-fetch
            return False
        return page.status < 400 and len(page.text) < THIN_CHARS

    def fetch(self, url: str) -> Page:
        page = self.free.fetch(url)
        if self.paid is None or self._switch.disabled_reason or not self.needs_escalation(page):
            return page
        try:
            better = self.paid.fetch(url)
        except ProviderUnavailable as e:
            self._switch.disable(str(e)[:200], "http")
            return page
        except BudgetExceeded:
            return page
        except Exception as e:
            log.debug("firecrawl escalation failed for %s: %s", url, type(e).__name__)
            return page
        return better if better.ok and len(better.text) > len(page.text) else page


def _has_composio_key() -> bool:
    return bool(os.environ.get("COMPOSIO_API_KEY", "").strip())


def _composio_ready(choice: str, composio_client: Any, explicit: bool) -> bool:
    if composio_client is not None or _has_composio_key():
        return True
    if explicit:
        raise ConfigError(f"{choice} needs COMPOSIO_API_KEY to be set (in the environment or .env)")
    return False


def make_budget(settings: Settings) -> InstantBudget:
    return InstantBudget(settings.instant_budget_usd, dict(settings.instant_prices))


def get_search(settings: Settings, composio_client: Any = None, budget: InstantBudget | None = None) -> SearchProvider:
    budget = budget or make_budget(settings)
    ddgs = DDGSSearch(settings.ddgs_backends)
    choice = settings.search_provider
    if choice == "ddgs":
        return ddgs
    user = settings.composio_user_id
    if choice == "hybrid":
        ready = _composio_ready(choice, composio_client, explicit=False)
        return HybridSearch(ddgs, ComposioSearch("EXA_SEARCH", user, composio_client, budget) if ready else None)
    if not _composio_ready(f"SEARCH_PROVIDER={choice}", composio_client, explicit=choice != "auto"):
        log.info("search: COMPOSIO_API_KEY not set, using ddgs")
        return ddgs
    slug = "TAVILY_SEARCH" if choice == "composio_tavily" else "EXA_SEARCH"
    return Fallback(ComposioSearch(slug, user, composio_client, budget), ddgs, "search")


def get_fetch(settings: Settings, composio_client: Any = None, budget: InstantBudget | None = None) -> FetchProvider:
    budget = budget or make_budget(settings)
    http = HttpFetch(settings.http_timeout, settings.max_page_chars)
    choice = settings.fetch_provider
    if choice == "http":
        return http
    if choice == "hybrid":
        ready = _composio_ready(choice, composio_client, explicit=False)
        firecrawl = ComposioFetch(settings.composio_user_id, settings.max_page_chars, composio_client, budget)
        return HybridFetch(http, firecrawl if ready else None)
    if not _composio_ready(f"FETCH_PROVIDER={choice}", composio_client, explicit=choice != "auto"):
        log.info("fetch: COMPOSIO_API_KEY not set, using http")
        return http
    firecrawl = ComposioFetch(settings.composio_user_id, settings.max_page_chars, composio_client, budget)
    return Fallback(firecrawl, http, "fetch")


__all__ = [
    "BudgetExceeded",
    "Fallback",
    "HybridFetch",
    "HybridSearch",
    "InstantBudget",
    "Page",
    "ProviderUnavailable",
    "SearchResult",
    "get_fetch",
    "get_search",
    "make_budget",
]
