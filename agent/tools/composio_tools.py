"""Search/fetch through a Composio session (Exa, Tavily, Firecrawl toolkits).

These need either Composio Instant Toolkits enabled for the project (prepaid
balance) or a provider key connected in the Composio dashboard. Without that,
`session.execute` fails with "No active connection found for toolkit(s) ...",
which we surface as ProviderUnavailable so `auto` mode can fall back.
"""

from __future__ import annotations

import logging
import re
import threading
from typing import Any

from agent.config import require_env
from agent.tools.base import Page, ProviderUnavailable, SearchResult

log = logging.getLogger(__name__)

# Errors that mean "this provider will not work for the whole run".
_UNAVAILABLE = re.compile(
    r"no active connection|\b(400|401|403|410)\b|gone|unauthori[sz]ed|forbidden|instant|retired|deprecated",
    re.IGNORECASE,
)

# tool slug -> (toolkit, {logical arg: candidate arg names, first is the default}).
# Names were seen in the Composio catalog; `_resolve_args` re-checks them against the
# live tool schema when it can, so a renamed arg does not silently break calls.
TOOLS: dict[str, tuple[str, dict[str, tuple[str, ...]]]] = {
    "EXA_SEARCH": ("exa", {"query": ("query",), "limit": ("numResults", "num_results")}),
    "TAVILY_SEARCH": ("tavily", {"query": ("query",), "limit": ("max_results", "maxResults")}),
    "FIRECRAWL_SCRAPE": (
        "firecrawl",
        {"url": ("url",), "formats": ("formats",), "main": ("onlyMainContent", "only_main_content")},
    ),
}


class ComposioTool:
    """One Composio tool slug, executed through a lazily created session."""

    def __init__(self, slug: str, user_id: str, client: Any = None):
        self.slug = slug
        self.toolkit, self._arg_names = TOOLS[slug]
        self.user_id = user_id
        self._client = client
        self._session = None
        self._resolved: dict[str, str] | None = None
        self._lock = threading.Lock()

    def _ensure_session(self):
        with self._lock:
            if self._session is not None:
                return self._session
            if self._client is None:
                key = require_env("COMPOSIO_API_KEY", f"Composio {self.toolkit} provider")
                from composio import Composio

                self._client = Composio(api_key=key)
            try:
                self._session = self._client.sessions.create(user_id=self.user_id, toolkits=[self.toolkit])
            except Exception as e:  # SDK raises several types; classify by message
                raise _classify(e) from e
            self._resolved = self._resolve_args()
            return self._session

    def _resolve_args(self) -> dict[str, str]:
        props: dict = {}
        try:
            tool = self._client.tools.get_raw_composio_tool_by_slug(self.slug)
            props = (getattr(tool, "input_parameters", None) or {}).get("properties", {}) or {}
        except Exception as e:
            log.debug("could not read schema for %s: %s", self.slug, type(e).__name__)
        return {
            logical: next((n for n in names if n in props), names[0])
            for logical, names in self._arg_names.items()
        }

    def execute(self, **logical_args: Any) -> Any:
        session = self._ensure_session()
        assert self._resolved is not None
        arguments = {self._resolved[k]: v for k, v in logical_args.items()}
        try:
            resp = session.execute(self.slug, arguments=arguments)
        except Exception as e:
            raise _classify(e) from e
        result_type = getattr(resp, "result_type", "completed")
        if result_type != "completed":
            err = str(getattr(resp, "error", "") or result_type)
            if _UNAVAILABLE.search(err):
                raise ProviderUnavailable(f"{self.slug}: {err[:300]}")
            raise RuntimeError(f"{self.slug} failed: {err[:300]}")
        return getattr(resp, "data", resp)


def _classify(e: Exception) -> Exception:
    msg = str(e)
    if _UNAVAILABLE.search(msg) or isinstance(e, ProviderUnavailable):
        return ProviderUnavailable(msg[:300])
    return e


def _find_results(data: Any) -> list[dict]:
    """Find the first list of dicts carrying a 'url' key anywhere in a tool payload."""
    if isinstance(data, list):
        if data and all(isinstance(x, dict) for x in data) and any("url" in x for x in data):
            return data
        for x in data:
            found = _find_results(x)
            if found:
                return found
    elif isinstance(data, dict):
        for v in data.values():
            found = _find_results(v)
            if found:
                return found
    return []


class ComposioSearch:
    def __init__(self, slug: str, user_id: str, client: Any = None):
        self.tool = ComposioTool(slug, user_id, client)
        self.name = f"composio_{self.tool.toolkit}"

    def search(self, query: str, max_results: int = 5) -> tuple[str, list[SearchResult]]:
        data = self.tool.execute(query=query, limit=max_results)
        results = [
            SearchResult(
                url=r["url"],
                title=str(r.get("title") or ""),
                snippet=str(r.get("content") or r.get("text") or r.get("snippet") or "")[:500],
            )
            for r in _find_results(data)
            if r.get("url")
        ]
        return self.name, results[:max_results]


def _dig(data: Any, *keys: str) -> Any:
    """First non-empty value for any of `keys`, searching nested dicts breadth-first."""
    queue = [data]
    while queue:
        cur = queue.pop(0)
        if isinstance(cur, dict):
            for k in keys:
                if cur.get(k):
                    return cur[k]
            queue.extend(v for v in cur.values() if isinstance(v, dict))
    return None


class ComposioFetch:
    name = "composio_firecrawl"

    def __init__(self, user_id: str, max_chars: int, client: Any = None):
        self.tool = ComposioTool("FIRECRAWL_SCRAPE", user_id, client)
        self.max_chars = max_chars

    def fetch(self, url: str) -> Page:
        data = self.tool.execute(url=url, formats=["markdown"], main=True)
        text = _dig(data, "markdown", "content") or ""
        final = _dig(data, "sourceURL", "url") or url
        status = _dig(data, "statusCode", "status_code") or 200
        return Page(
            requested_url=url,
            final_url=str(final),
            status=int(status),
            provider=self.name,
            text=str(text)[: self.max_chars],
        )
