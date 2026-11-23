from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Protocol


class ProviderUnavailable(RuntimeError):
    """The provider cannot serve this run (no connection, retired toolkit, bad key).

    `auto` providers fall back permanently when they see this.
    """


@dataclass
class SearchResult:
    url: str
    title: str = ""
    snippet: str = ""


@dataclass
class Page:
    requested_url: str
    final_url: str
    status: int
    provider: str
    text: str = ""
    fetched_at: str = field(default_factory=lambda: now_iso())
    error: str | None = None
    # How text was extracted; always set. HttpFetch: trafilatura, trafilatura_recall, md_twin, visible_text,
    # raw, http_error. Firecrawl: firecrawl_markdown. Researcher: fetch_error. Fakes: fake.
    method: str = "unknown"
    text_source_url: str | None = None  # set when the text came from another URL (e.g. the .md twin)

    @property
    def ok(self) -> bool:
        return self.error is None and 200 <= self.status < 400 and bool(self.text.strip())

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.text.encode()).hexdigest()

    def meta(self) -> dict:
        d = asdict(self)
        d.pop("text")
        d["sha256"] = self.sha256
        d["text_len"] = len(self.text)
        return d


class SearchProvider(Protocol):
    def search(self, query: str, max_results: int = 5) -> tuple[str, list[SearchResult]]:
        """Return (provider_name_that_served_it, results)."""
        ...


class FetchProvider(Protocol):
    def fetch(self, url: str) -> Page: ...


def now_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
