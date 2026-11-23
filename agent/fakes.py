"""Offline fakes for `--dry-run` and tests: no network, no keys, deterministic."""

from __future__ import annotations

import json
import re

from agent.llm.base import LLMResult
from agent.tools.base import Page, SearchResult

_PAGE_RE = re.compile(r"URL: (\S+)\n\n(.*?)(?=\n\n=== PAGE|\n\nYou may request|\n\nNo more fetching|\Z)", re.S)


def _domain(query: str) -> str:
    name = query.split(" API")[0].split(" developer")[0].split(" MCP")[0]
    return re.sub(r"[^a-z0-9]+", "", name.lower()) + ".example.com"


class FakeSearch:
    name = "fake"

    def search(self, query: str, max_results: int = 5) -> tuple[str, list[SearchResult]]:
        d = _domain(query)
        topic = "mcp" if "MCP" in query else "authentication" if "authentication" in query else "reference"
        return self.name, [SearchResult(f"https://docs.{d}/api/{topic}", f"{topic} docs")][:max_results]


class FakeFetch:
    name = "fake"

    def fetch(self, url: str) -> Page:
        text = (
            f"Developer documentation page at {url}. "
            "Authenticate requests with an API key sent in the Authorization header. "
            "Sign up for a free account to create API keys from the dashboard. "
            "The REST API covers contacts, deals and webhooks."
        )
        return Page(requested_url=url, final_url=url, status=200, provider=self.name, text=text)


class FakeLLM:
    """Builds a valid row that cites the first fetched page, quoting its real text."""

    provider = "fake"
    model = "fake-1"

    def complete(self, system, messages, json_mode=True):
        prompt = messages[-1]["content"] if "APP (" in messages[-1]["content"] else messages[0]["content"]
        app = json.loads(prompt.split("\n", 2)[1])
        pages = _PAGE_RE.findall(prompt)
        url, text = pages[0] if pages else ("https://example.com", "")
        quote = " ".join(text.split()[:10])
        ev = [{"url": url, "source_type": "official_docs", "note": "dry-run"}]
        row = {
            "one_liner": f"{app['name']} is a product used here only to exercise the pipeline offline.",
            "auth_methods": ["api_key"], "primary_auth": "api_key", "access": "self_serve",
            "api_types": ["rest"], "has_public_docs": True, "api_breadth": "moderate", "docs_url": url,
            "mcp_status": "none", "mcp_url": None, "verdict": "buildable_now", "blocker": "none",
            "evidence": {k: ev for k in ("auth", "access", "api_surface", "mcp", "verdict")},
            "confidence": "low", "requires_end_user_admin": False, "notes": "Dry-run row from fake tools.",
        }
        snippets = {k: [{"url": url, "quote": quote}] for k in ("auth", "access", "api_surface", "mcp", "verdict")}
        out = {"row": row, "snippets": snippets, "follow_up": {"queries": [], "urls": []}}
        return LLMResult(json.dumps(out), input_tokens=len(prompt) // 4, output_tokens=300, stop_reason="end_turn")
