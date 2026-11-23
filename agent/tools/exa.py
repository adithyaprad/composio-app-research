"""Direct Exa API search and page text (https://api.exa.ai), shared by search and fetch.

Composio's Instant Exa/Firecrawl toolkits return "No active connection" in this project,
so the agent calls Exa itself with EXA_API_KEY.

- `/search` asks for page text in the same call (included in the search price). Every result's
  text is cached, so a later fetch of that URL costs nothing and makes no request.
- `/contents` fetches a URL that was not in any search result.
- Each call reserves its listed price in the run's ExaBudget first (BudgetExceeded = not sent).
  The reported `costDollars.total` is recorded when present, else the listed price.

Errors: 401/402/403 (bad key, no credits) or MAX_FAILURES 5xx / network errors in a row raise
ProviderUnavailable, so Fallback switches to the free tools for the rest of the run. A single
transient failure, or no text for one URL, raises UseFallback: the free tool serves that call only.
Any other 4xx for one URL returns a failed Page.
"""

from __future__ import annotations

import logging
import os
import threading
from dataclasses import replace
from typing import Any

from agent.tools.base import Page, ProviderUnavailable, SearchResult, UseFallback
from agent.tools.budget import ExaBudget
from agent.validate import normalize_url

log = logging.getLogger(__name__)

BASE_URL = "https://api.exa.ai"
AUTH_STATUSES = {401, 402, 403}
BLOCKED_PAGE_STATUSES = {401, 403, 429}  # the site blocked Exa's crawler; plain http may still work
MAX_FAILURES = 3  # consecutive 5xx / network errors before Exa is switched off for the run


def has_exa_key() -> bool:
    return bool(os.environ.get("EXA_API_KEY", "").strip())


def _cost(data: dict) -> float | None:
    total = (data.get("costDollars") or {}).get("total") if isinstance(data, dict) else None
    try:
        return float(total) if total is not None else None
    except (TypeError, ValueError):
        return None


class ExaClient:
    """One per run: HTTP client, spend cap, and URL -> Page text cache shared by ExaSearch and ExaFetch."""

    def __init__(self, api_key: str, budget: ExaBudget, timeout: float = 30.0, max_chars: int = 20000,
                 transport: Any = None):
        import httpx

        self.budget = budget
        self.max_chars = max_chars
        self._http = httpx.Client(base_url=BASE_URL, timeout=timeout, transport=transport,
                                  headers={"x-api-key": api_key, "Content-Type": "application/json"})
        self._cache: dict[str, Page] = {}
        self._lock = threading.Lock()
        self._failures = 0
        self.disabled_reason: str | None = None  # set once; search and fetch then stop sending requests

    # cache ------------------------------------------------------------------
    def cached(self, url: str) -> Page | None:
        with self._lock:
            return self._cache.get(normalize_url(url))

    def remember(self, page: Page, *urls: str) -> None:
        if not page.ok:
            return
        with self._lock:
            for u in {page.final_url, *urls}:
                self._cache.setdefault(normalize_url(u), page)

    # http -------------------------------------------------------------------
    def _unavailable(self, why: str) -> ProviderUnavailable:
        with self._lock:
            self.disabled_reason = self.disabled_reason or why
        return ProviderUnavailable(why)

    def _failed(self, why: str) -> Exception:
        with self._lock:
            self._failures += 1
            n = self._failures
        if n >= MAX_FAILURES:
            return self._unavailable(f"exa: {n} failures in a row, last: {why}")
        return UseFallback(f"exa: {why}")

    def post(self, endpoint: str, body: dict) -> tuple[int, dict]:
        """POST /<endpoint>. Returns (status, json) for 2xx and non-auth 4xx; raises otherwise.

        The listed price is reserved first and settled only for a 2xx answer.
        """
        import httpx

        if self.disabled_reason:
            raise ProviderUnavailable(self.disabled_reason)
        self.budget.reserve(endpoint)  # raises BudgetExceeded; nothing is sent
        try:
            resp = self._http.post(f"/{endpoint}", json=body)
        except httpx.HTTPError as e:
            self.budget.release(endpoint)
            raise self._failed(type(e).__name__) from e
        status = resp.status_code
        try:
            data = resp.json()
        except ValueError:
            data = {}
        if status < 300:
            with self._lock:
                self._failures = 0
            self.budget.record(endpoint, _cost(data))
            return status, data if isinstance(data, dict) else {}
        self.budget.release(endpoint)
        msg = str(data.get("error") or data.get("message") or "")[:200] if isinstance(data, dict) else ""
        if status in AUTH_STATUSES:
            raise self._unavailable(f"exa /{endpoint}: HTTP {status} {msg}".strip())
        if status >= 500 or status == 429:
            raise self._failed(f"/{endpoint} HTTP {status}")
        return status, {"error": msg or f"HTTP {status}"}


class ExaSearch:
    name = "exa"

    def __init__(self, client: ExaClient):
        self.client = client

    def search(self, query: str, max_results: int = 5) -> tuple[str, list[SearchResult]]:
        body = {"query": query, "numResults": max_results, "type": "auto",
                "contents": {"text": {"maxCharacters": self.client.max_chars}}}
        status, data = self.client.post("search", body)
        if status >= 400:
            raise UseFallback(f"exa /search HTTP {status}: {data.get('error', '')}")
        results = []
        for r in data.get("results") or []:
            url = r.get("url") if isinstance(r, dict) else None
            if not url:
                continue
            text = str(r.get("text") or "")
            highlights = r.get("highlights") or []
            snippet = " ".join(str(h) for h in highlights) or str(r.get("summary") or "") or text
            title = str(r.get("title") or "")
            results.append(SearchResult(url=url, title=title, snippet=snippet[:500]))
            if text.strip():
                self.client.remember(Page(url, url, 200, "exa", text=text[: self.client.max_chars],
                                          method="exa_search"))
        return self.name, results[:max_results]


class ExaFetch:
    name = "exa"

    def __init__(self, client: ExaClient):
        self.client = client

    def fetch(self, url: str) -> Page:
        hit = self.client.cached(url)
        if hit is not None:  # text came with a search result or an earlier fetch: no new request, no cost
            method = hit.method if hit.method == "exa_search" else "exa_cache"
            return replace(hit, requested_url=url, method=method)
        status, data = self.client.post("contents", {"urls": [url], "text": {"maxCharacters": self.client.max_chars}})
        if status >= 400:
            return Page(url, url, status, "exa", error=f"exa /contents: {data.get('error', '')}"[:300],
                        method="exa_error")
        result = next((r for r in data.get("results") or [] if isinstance(r, dict) and r.get("text")), None)
        if result is None:
            page_status = self._page_status(data)
            if page_status and 400 <= page_status < 500 and page_status not in BLOCKED_PAGE_STATUSES:
                return Page(url, url, page_status, "exa", error=f"exa /contents: page HTTP {page_status}",
                            method="exa_error")
            raise UseFallback(f"exa /contents: no text for {url}")
        final = str(result.get("url") or url)
        page = Page(url, final, 200, "exa", text=str(result["text"])[: self.client.max_chars], method="exa_text")
        self.client.remember(page, url)
        return page

    @staticmethod
    def _page_status(data: dict) -> int | None:
        """httpStatusCode from `statuses[]` for a URL Exa could not read, if reported."""
        for s in data.get("statuses") or []:
            if isinstance(s, dict) and s.get("status") == "error":
                code = (s.get("error") or {}).get("httpStatusCode")
                if isinstance(code, int):
                    return code
        return None


def make_exa(api_key: str | None, budget: ExaBudget, timeout: float, max_chars: int,
             transport: Any = None) -> ExaClient | None:
    """An ExaClient, or None when there is no key (the caller then uses the free tools)."""
    key = (api_key if api_key is not None else os.environ.get("EXA_API_KEY", "")).strip()
    if not key:
        return None
    return ExaClient(key, budget, timeout=timeout, max_chars=max_chars, transport=transport)
