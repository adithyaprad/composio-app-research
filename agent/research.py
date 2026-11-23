"""Per-app research loop and the run driver.

For each app: a few targeted searches + start_url -> fetch the best N pages ->
LLM fills the row from page text only -> optional single follow-up round ->
evidence post-check -> validation (one retry with errors fed back) -> write.
"""

from __future__ import annotations

import csv
import hashlib
import json
import logging
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

from agent import prompts
from agent.config import Settings
from agent.llm import LLM, extract_json
from agent.tools.base import Page, now_iso
from agent.validate import check_evidence, validate_row

log = logging.getLogger(__name__)

IDENTITY_KEYS = ("id", "slug", "name", "category")
RESEARCHED_KEYS = (
    "one_liner", "auth_methods", "primary_auth", "access", "api_types", "has_public_docs",
    "api_breadth", "docs_url", "mcp_status", "mcp_url", "verdict", "blocker", "evidence",
    "confidence", "requires_end_user_admin",
)
ROW_ORDER = (*IDENTITY_KEYS, *RESEARCHED_KEYS, "composio_toolkit", "notes")
URL_HINTS = ("docs", "developer", "api", "reference", "auth", "oauth", "pricing", "mcp")
FOLLOW_UP_MAX_QUERIES = 3
FOLLOW_UP_MAX_PAGES = 3


# ---------- inputs ----------

def load_apps(path: Path) -> list[dict]:
    with path.open(newline="") as f:
        return [{**r, "id": int(r["id"])} for r in csv.DictReader(f)]


def load_coverage(path: Path) -> dict[str, dict]:
    """slug -> composio_toolkit object, mapping CSV empty strings to null / []."""
    out = {}
    with path.open(newline="") as f:
        for r in csv.DictReader(f):
            out[r["slug"]] = {
                "status": r["status"] or "unknown",
                "toolkit_slug": r["toolkit_slug"] or None,
                "sibling_slugs": [s.strip() for s in r["sibling_slugs"].split(",") if s.strip()],
                "checked_at": r["checked_at"] or None,
                "evidence_url": r["evidence_url"] or None,
            }
    return out


UNKNOWN_COVERAGE = {"status": "unknown", "toolkit_slug": None, "sibling_slugs": [], "checked_at": None,
                    "evidence_url": None}


# ---------- search planning ----------

def search_queries(app: dict) -> list[str]:
    n = app["name"]
    return [
        f"{n} API authentication",
        f"{n} developer API access pricing",
        f"{n} API documentation reference",
        f"{n} MCP server",
    ]


def base_domain(url: str) -> str:
    host = (urlsplit(url if "//" in url else f"https://{url}").hostname or "").lower()
    return ".".join(host.split(".")[-2:])


def score_url(url: str, official: str, query: str) -> int:
    u = url.lower()
    host = (urlsplit(u).hostname or "")
    score = 0
    if official and (host == official or host.endswith("." + official)):
        score += 5
    score += sum(2 for h in URL_HINTS if h in u)
    if "mcp" in query.lower() and ("github.com" in host or "mcp" in u):
        score += 3
    if any(bad in host for bad in ("youtube.com", "facebook.com/groups", "pinterest.")):
        score -= 5
    return score


def pick_urls(app: dict, searches: list[dict], max_pages: int, already: set[str] = frozenset()) -> list[str]:
    """start_url first, then the best result of each query, then the best of the rest."""
    official = base_domain(app.get("start_url") or app.get("source_hint") or "")
    picked: list[str] = []

    def add(url: str) -> None:
        if url and url not in picked and url not in already and len(picked) < max_pages:
            picked.append(url)

    if app.get("start_url") and app["start_url"] not in already:
        add(app["start_url"])
    ranked = []
    for s in searches:
        res = sorted(s["results"], key=lambda r: -score_url(r["url"], official, s["query"]))
        if res:
            add(res[0]["url"])
        ranked += [(score_url(r["url"], official, s["query"]), r["url"]) for r in res[1:]]
    for _, url in sorted(ranked, key=lambda x: -x[0]):
        add(url)
    return picked


# ---------- per-app research ----------

@dataclass
class Stats:
    search_calls: int = 0
    fetch_calls: int = 0
    llm_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    providers: Counter = field(default_factory=Counter)


@dataclass
class Outcome:
    slug: str
    row: dict | None
    errors: list[str]
    evidence: dict
    stats: Stats
    wall_s: float


class Researcher:
    def __init__(self, settings: Settings, llm: LLM, search, fetch, run_dir: Path,
                 max_pages: int = 8, follow_up: bool = True, per_page_chars: int = 6000,
                 coverage: dict[str, dict] | None = None):
        self.settings = settings
        self.llm = llm
        self.search = search
        self.fetch = fetch
        self.run_dir = run_dir
        self.max_pages = max_pages
        self.follow_up = follow_up
        self.per_page_chars = per_page_chars
        self.coverage = coverage or {}

    # tools ------------------------------------------------------------
    def _search(self, query: str, stats: Stats) -> dict:
        stats.search_calls += 1
        try:
            provider, results = self.search.search(query, 5)
        except Exception as e:
            log.warning("search failed for %r: %s", query, type(e).__name__)
            provider, results = "error", []
        stats.providers[f"search:{provider}"] += 1
        return {"query": query, "provider": provider, "results": [{"url": r.url, "title": r.title} for r in results]}

    def _fetch(self, slug: str, url: str, stats: Stats) -> Page:
        stats.fetch_calls += 1
        try:
            page = self.fetch.fetch(url)
        except Exception as e:
            page = Page(url, url, 0, "error", error=f"{type(e).__name__}: {str(e)[:200]}")
        stats.providers[f"fetch:{page.provider}"] += 1
        if page.text:
            cache = self.run_dir / "cache" / slug
            cache.mkdir(parents=True, exist_ok=True)
            (cache / f"{hashlib.sha1(url.encode()).hexdigest()[:12]}.txt").write_text(page.text)
        return page

    def _llm(self, messages: list[dict], stats: Stats) -> str:
        stats.llm_calls += 1
        res = self.llm.complete(prompts.SYSTEM, messages, json_mode=True)
        stats.input_tokens += res.input_tokens
        stats.output_tokens += res.output_tokens
        return res.text

    # row --------------------------------------------------------------
    def _assemble(self, app: dict, out: dict) -> dict:
        llm_row = out.get("row") if isinstance(out.get("row"), dict) else {}
        row = {k: app[k] for k in IDENTITY_KEYS}
        row.update({k: llm_row.get(k) for k in RESEARCHED_KEYS})
        row["notes"] = llm_row.get("notes") or ""
        row["composio_toolkit"] = dict(self.coverage.get(app["slug"], UNKNOWN_COVERAGE))
        return {k: row[k] for k in ROW_ORDER}

    def _evaluate(self, app: dict, text: str, pages: list[Page]) -> tuple[dict | None, dict, dict, list[str]]:
        try:
            out = extract_json(text)
        except ValueError as e:
            return None, {}, {}, [f"output: {e}"]
        row = self._assemble(app, out)
        snippets = out.get("snippets") if isinstance(out.get("snippets"), dict) else {}
        row, checks = check_evidence(row, snippets, pages)
        identity = {k: app[k] for k in IDENTITY_KEYS}
        return row, snippets, checks | {"follow_up": out.get("follow_up") or {}}, validate_row(row, identity)

    def research(self, app: dict) -> Outcome:
        t0 = time.monotonic()
        slug = app["slug"]
        stats = Stats()
        searches = [self._search(q, stats) for q in search_queries(app)]
        pages = [self._fetch(slug, u, stats) for u in pick_urls(app, searches, self.max_pages)]

        messages = [{"role": "user", "content": prompts.research_prompt(app, pages, self.per_page_chars,
                                                                         self.follow_up)}]
        text = self._llm(messages, stats)
        row, snippets, checks, errors = self._evaluate(app, text, pages)

        follow = checks.get("follow_up") or {}
        if self.follow_up and (follow.get("queries") or follow.get("urls")):
            extra_searches = [self._search(q, stats) for q in list(follow.get("queries") or [])[:FOLLOW_UP_MAX_QUERIES]]
            searches += extra_searches
            seen = {p.requested_url for p in pages}
            wanted = [u for u in list(follow.get("urls") or []) if isinstance(u, str) and u.startswith("http")]
            urls = [u for u in wanted if u not in seen][:FOLLOW_UP_MAX_PAGES]
            urls += pick_urls({}, extra_searches, FOLLOW_UP_MAX_PAGES - len(urls), seen | set(urls))
            pages += [self._fetch(slug, u, stats) for u in urls]
            messages = [{"role": "user", "content": prompts.research_prompt(app, pages, self.per_page_chars, False)}]
            text = self._llm(messages, stats)
            row, snippets, checks, errors = self._evaluate(app, text, pages)

        retried = False
        if errors:
            retried = True
            messages += [{"role": "assistant", "content": text},
                         {"role": "user", "content": prompts.retry_prompt(errors, checks)}]
            text = self._llm(messages, stats)
            row, snippets, checks, errors = self._evaluate(app, text, pages)

        checks.pop("follow_up", None)
        checks["retried"] = retried
        checks["validation_errors"] = errors
        evidence = {
            "slug": slug,
            "searches": searches,
            "pages": [p.meta() for p in pages],
            "snippets": snippets,
            "checks": checks,
        }
        return Outcome(slug, row, errors, evidence, stats, round(time.monotonic() - t0, 2))


# ---------- run driver ----------

def _read_done(path: Path) -> set[str]:
    if not path.exists():
        return set()
    done = set()
    for line in path.read_text().splitlines():
        if line.strip():
            try:
                done.add(json.loads(line)["slug"])
            except (json.JSONDecodeError, KeyError):
                continue
    return done


def run(apps: list[dict], researcher: Researcher, run_dir: Path, concurrency: int = 3) -> dict:
    """Research `apps` into run_dir, skipping slugs already in rows.jsonl. Returns a summary."""
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "evidence").mkdir(exist_ok=True)
    rows_path, fail_path, log_path = run_dir / "rows.jsonl", run_dir / "failures.jsonl", run_dir / "log.jsonl"
    done = _read_done(rows_path)
    todo = [a for a in apps if a["slug"] not in done]
    lock = threading.Lock()
    summary = Counter(skipped=len(apps) - len(todo))
    totals = Counter()
    settings = researcher.settings

    def append(path: Path, obj: dict) -> None:
        with path.open("a") as f:
            f.write(json.dumps(obj, ensure_ascii=False) + "\n")

    def one(app: dict) -> None:
        t0 = time.monotonic()
        try:
            out = researcher.research(app)
        except Exception as e:  # keep going; record the crash
            log.exception("research crashed for %s", app["slug"])
            with lock:
                append(fail_path, {"slug": app["slug"], "errors": [f"crash: {type(e).__name__}: {e}"],
                                   "row": None, "at": now_iso()})
                append(log_path, {"slug": app["slug"], "status": "crash", "wall_s": round(time.monotonic() - t0, 2),
                                  "at": now_iso()})
                summary["crashed"] += 1
            return
        s = out.stats
        status = "failed" if out.errors else "ok"
        record = {
            "slug": out.slug, "status": status, "wall_s": out.wall_s,
            "search_calls": s.search_calls, "fetch_calls": s.fetch_calls, "llm_calls": s.llm_calls,
            "input_tokens": s.input_tokens, "output_tokens": s.output_tokens,
            "est_cost_usd": settings.est_cost(s.input_tokens, s.output_tokens),
            "llm_provider": researcher.llm.provider, "llm_model": researcher.llm.model,
            "providers": dict(s.providers),
            "dropped_evidence": len(out.evidence["checks"].get("dropped_evidence", [])),
            "at": now_iso(),
        }
        (run_dir / "evidence" / f"{out.slug}.json").write_text(json.dumps(out.evidence, indent=2, ensure_ascii=False))
        with lock:
            if out.errors:
                append(fail_path, {"slug": out.slug, "errors": out.errors, "row": out.row, "at": now_iso()})
            else:
                append(rows_path, out.row)
            append(log_path, record)
            summary[status] += 1
            totals.update(input_tokens=s.input_tokens, output_tokens=s.output_tokens, wall_s=out.wall_s)
        log.info("%s: %s in %.1fs (%d pages, %d llm calls)", out.slug, status, out.wall_s, s.fetch_calls, s.llm_calls)

    with ThreadPoolExecutor(max_workers=max(1, concurrency)) as pool:
        for fut in as_completed([pool.submit(one, a) for a in todo]):
            fut.result()

    return {
        "run_dir": str(run_dir),
        "requested": len(apps),
        "skipped_done": summary["skipped"],
        "ok": summary["ok"],
        "failed_validation": summary["failed"],
        "crashed": summary["crashed"],
        "input_tokens": totals["input_tokens"],
        "output_tokens": totals["output_tokens"],
        "est_cost_usd": settings.est_cost(totals["input_tokens"], totals["output_tokens"]),
    }
