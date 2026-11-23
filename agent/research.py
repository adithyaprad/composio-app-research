"""Per-app research loop and the run driver.

For each app: a few targeted searches + start_url -> fetch the best N pages ->
LLM fills the row from page text only -> optional single follow-up round ->
llms.txt MCP check while mcp_status is none (re-ask only if it found MCP pages) ->
evidence post-check -> validation (one retry with errors fed back) -> write.
"""

from __future__ import annotations

import csv
import hashlib
import json
import logging
import re
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urljoin, urlsplit

from agent import prompts
from agent.config import Settings
from agent.llm import LLM, LLMResult, extract_json
from agent.tools.base import Page, now_iso
from agent.tools.budget import InstantBudget, current_app
from agent.tools.keyless import THIN_CHARS, is_docs_url
from agent.validate import check_evidence, normalize_url, validate_row

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
MCP_LLMS_TXT_MAX = 2  # docs origin + start origin
MCP_LINKS_MAX = 2  # MCP pages followed from llms.txt
MCP_TEXT_RE = re.compile(r"\bmcp\b|model context protocol", re.I)
_LINK_RE = re.compile(r"\]\(\s*([^)\s]+)|(https?://[^\s)\]>\"'<]+)")


def mcp_links(page: Page) -> list[str]:
    """Absolute URLs with "mcp" in them linked from a page (markdown links or bare URLs), e.g. from llms.txt."""
    out: list[str] = []
    for md, bare in _LINK_RE.findall(page.text or ""):
        url = urljoin(page.final_url, md or bare)
        if url.startswith("http") and "mcp" in url.lower() and url not in out:
            out.append(url)
    return out[:MCP_LINKS_MAX]


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

HOST_RE = re.compile(r"(?<![@\w.-])((?:[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.)+[a-z]{2,})(?=[/:\s),]|$)", re.I)
# Words in a source_hint / category label that add nothing to a search query.
QUERY_STOPWORDS = {"a", "an", "and", "the", "of", "for", "by", "with", "powered", "based", "via", "api"}


def hint_host(hint: str) -> str:
    """First host-like token in a free-text hint ("acmepay.io", "github.com/foo/bar", "x.io (MCP)"), or ""."""
    m = HOST_RE.search(hint or "")
    return m.group(1).lower().removeprefix("www.") if m else ""


def official_host(app: dict) -> str:
    """Host of start_url, else a host named in source_hint."""
    start = (app.get("start_url") or "").strip()
    if start:
        return (urlsplit(start if "//" in start else f"https://{start}").hostname or "").lower().removeprefix("www.")
    return hint_host(app.get("source_hint") or "")


def official_domain(app: dict) -> str:
    return base_domain(official_host(app))


def query_context(app: dict) -> str:
    """With no start_url, the name alone can hit an unrelated product: add the hint and category words.

    "acme (processor-powered)" + "Finance and Fintech" -> "processor Finance Fintech".
    Words already in the name are dropped.
    """
    if (app.get("start_url") or "").strip():
        return ""
    name_words = set(re.findall(r"[a-z0-9]+", app.get("name", "").lower()))
    label = app.get("category_label") or (app.get("category") or "").replace("_", " ")
    words: list[str] = []
    for w in re.findall(r"[A-Za-z0-9][A-Za-z0-9.]*[A-Za-z0-9]|[A-Za-z0-9]", f"{app.get('source_hint') or ''} {label}"):
        lw = w.lower()
        if lw in name_words or lw in QUERY_STOPWORDS or lw in (x.lower() for x in words):
            continue
        words.append(w)
    return " ".join(words)


def search_queries(app: dict) -> list[str]:
    """3 general queries, a generic MCP query, and site: MCP queries on the official host (and its parent)."""
    context = query_context(app)
    n = f'"{app["name"]}" {context}' if context else app["name"]
    queries = [
        f"{n} API authentication",
        f"{n} developer API access documentation",
        f"{n} pricing plans free trial signup",
        f"{n} official MCP server",
    ]
    host = official_host(app)
    if host and host not in DOC_HOSTS:  # site:github.com MCP would only return noise
        queries.append(f"site:{host} MCP")
        parent = base_domain(host)
        if parent != host and parent not in DOC_HOSTS:  # docs./developers./marketplace. siblings
            queries.append(f"site:{parent} MCP")
    return queries


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
    if "pricing" in query.lower() and is_pricing_url(u):
        score += 4
    if any(bad in host for bad in ("youtube.com", "facebook.com/groups", "pinterest.")):
        score -= 5
    return score


PRICING_RE = re.compile(r"/(pricing|plans?|signup|sign-up|register|billing|free-trial|trial|get-started)(?=[/.?#]|$)")


def is_pricing_url(url: str) -> bool:
    return bool(PRICING_RE.search(urlsplit(url.lower()).path))


# Code/doc hosts where a third-party-looking domain can still be the vendor's own page.
DOC_HOSTS = ("github.com", "gitlab.com", "npmjs.com", "pypi.org", "readme.io", "stoplight.io", "postman.com",
             "mintlify.app", "mintlify.dev", "gitbook.io", "readthedocs.io")
# Slug parts too generic to identify a vendor by themselves.
GENERIC_TOKENS = {"ads", "api", "app", "apps", "business", "cloud", "commerce", "crm", "data", "partner", "selling",
                  "the", "io", "ai", "hq", "chat", "mail", "connect"}
# Affixes vendors commonly glue to their name in a domain label (getacme, useacme, acmehq, ...).
HOST_AFFIXES = ("get", "use", "try", "with", "go", "app", "hq", "api", "docs", "dev", "ai", "io", "cloud")
SHORT_TOKEN = 6  # tokens this short never match inside another word
MAX_THIRD_PARTY = 2

OFFICIAL, DOC_HOST, THIRD_PARTY = 0, 1, 2


def app_tokens(app: dict) -> set[str]:
    """Strings that identify the app in a host, title or URL: compact name/slug plus distinctive slug parts."""
    name = re.sub(r"[^a-z0-9]", "", app.get("name", "").lower())
    slug = app.get("slug", "")
    tokens = {name, slug.replace("-", "")}
    tokens |= {p for p in slug.split("-") if len(p) >= 4 and p not in GENERIC_TOKENS}
    return {t for t in tokens if len(t) >= 3}


def token_in_label(token: str, label: str) -> bool:
    """Does a host label (e.g. "dev-docs", "getclay") name the app?

    Exact label, a hyphen segment, or the token plus a known affix. Only tokens
    longer than SHORT_TOKEN may also prefix/suffix a label, so "slate" never
    matches "slateenglish" and "front" never matches "storefront".
    """
    if label == token or token in label.split("-"):
        return True
    if any(label in (a + token, token + a) for a in HOST_AFFIXES):
        return True
    return len(token) > SHORT_TOKEN and (label.startswith(token) or label.endswith(token))


def mentions(token: str, text: str) -> bool:
    """Short tokens need a whole-word match; longer ones may appear inside compacted text ("FanBasis")."""
    lowered = text.lower()
    if len(token) <= SHORT_TOKEN:
        return re.search(rf"\b{re.escape(token)}\b", re.sub(r"[^a-z0-9]+", " ", lowered)) is not None
    return token in re.sub(r"[^a-z0-9]", "", lowered)


def _host_matches(host: str, domain: str) -> bool:
    return bool(domain) and (host == domain or host.endswith("." + domain))


def classify_result(app: dict, result: dict) -> int | None:
    """OFFICIAL / DOC_HOST / THIRD_PARTY tier, or None when the result looks unrelated to the app."""
    url = result["url"].lower()
    parts = urlsplit(url)
    host = (parts.hostname or "").removeprefix("www.")
    tokens = app_tokens(app)
    if _host_matches(host, official_domain(app)):
        return OFFICIAL
    labels = host.split(".")[:-1]  # drop the TLD so "fan" in ".fan" does not count
    if any(token_in_label(t, label) for t in tokens for label in labels):
        return OFFICIAL
    title = result.get("title") or ""
    if any(_host_matches(host, d) for d in DOC_HOSTS):
        return DOC_HOST if any(mentions(t, parts.path) or mentions(t, title) for t in tokens) else None
    text = f"{title} {result.get('snippet') or ''}"
    return THIRD_PARTY if any(mentions(t, text) for t in tokens) else None


def origin(url: str) -> str:
    p = urlsplit(url)
    return f"{p.scheme}://{p.netloc}"


def pick_urls(app: dict, searches: list[dict], max_pages: int, already: set[str] = frozenset(),
              include_start: bool = True) -> list[str]:
    """start_url first; then relevant results, official domain first, best of each query before the rest.

    - Results that don't look like the app are dropped; at most MAX_THIRD_PARTY
      third-party pages (directories, blogs) are kept, only when they name the app.
    - URLs are deduped by normalize_url, including against `already` (normalized keys).
    - With include_start, at least one official pricing/plans/signup page is
      included: the best one found (replacing the lowest-ranked pick if full), or a
      synthesized <origin>/pricing from start_url as one extra page.
    """
    official = official_domain(app)
    picked: list[str] = []
    keys: set[str] = set(already)
    third_party = 0

    def add(url: str, tier: int, force: bool = False) -> bool:
        nonlocal third_party
        key = normalize_url(url) if url else ""
        if not url or key in keys or (len(picked) >= max_pages and not force):
            return False
        if tier == THIRD_PARTY:
            if third_party >= MAX_THIRD_PARTY:
                return False
            third_party += 1
        picked.append(url)
        keys.add(key)
        return True

    start = app.get("start_url") if include_start else None
    if start:
        add(start, OFFICIAL)
    ranked = []
    for s in searches:
        scored = []
        for r in s["results"]:
            tier = classify_result(app, r)
            if tier is not None:
                scored.append((tier, -score_url(r["url"], official, s["query"]), r["url"]))
        scored.sort()
        if scored:
            add(scored[0][2], scored[0][0])
        ranked += scored[1:]
    ranked.sort()
    for tier, _, url in ranked:
        add(url, tier)

    if include_start and max_pages > 0 and not any(is_pricing_url(u) for u in picked):
        pricing = next((u for tier, _, u in ranked if tier == OFFICIAL and is_pricing_url(u)
                        and normalize_url(u) not in keys), None)
        if pricing:
            if len(picked) >= max_pages and len(picked) > 1:
                keys.discard(normalize_url(picked.pop()))
            add(pricing, OFFICIAL, force=True)
        elif start:
            add(f"{origin(start)}/pricing", OFFICIAL, force=True)  # may be max_pages + 1
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
                 coverage: dict[str, dict] | None = None, budget: InstantBudget | None = None):
        self.settings = settings
        self.budget = budget
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
        return {"query": query, "provider": provider,
                "results": [{"url": r.url, "title": r.title, "snippet": r.snippet[:300]} for r in results]}

    def _fetch(self, slug: str, url: str, stats: Stats) -> Page:
        stats.fetch_calls += 1
        try:
            page = self.fetch.fetch(url)
        except Exception as e:
            page = Page(url, url, 0, "error", error=f"{type(e).__name__}: {str(e)[:200]}", method="fetch_error")
        stats.providers[f"fetch:{page.provider}"] += 1
        if page.text:
            cache = self.run_dir / "cache" / slug
            cache.mkdir(parents=True, exist_ok=True)
            (cache / f"{hashlib.sha1(url.encode()).hexdigest()[:12]}.txt").write_text(page.text)
        return page

    def _fetch_new(self, slug: str, urls: list[str], stats: Stats, seen: set[str]) -> list[Page]:
        """Fetch URLs not already fetched in this app run. `seen` holds normalized requested + final URLs,
        so a page reached through a redirect (firecrawl.dev -> www.firecrawl.dev/) is not fetched twice."""
        pages = []
        for url in urls:
            if normalize_url(url) in seen:
                continue
            page = self._fetch(slug, url, stats)
            seen.update({normalize_url(page.requested_url), normalize_url(page.final_url)})
            pages.append(page)
        return pages

    def _llm(self, messages: list[dict], stats: Stats, attempts: list[dict], label: str, slug: str) -> LLMResult:
        stats.llm_calls += 1
        res = self.llm.complete(prompts.SYSTEM, messages, json_mode=True)
        stats.input_tokens += res.input_tokens
        stats.output_tokens += res.output_tokens
        log.info("%s: llm %s stop_reason=%s out_tokens=%d%s", slug, label, res.stop_reason, res.output_tokens,
                 " TRUNCATED" if res.truncated else "")
        attempts.append({"label": label, "at": now_iso(), "stop_reason": res.stop_reason, "truncated": res.truncated,
                         "input_tokens": res.input_tokens, "output_tokens": res.output_tokens, "text": res.text})
        return res

    @staticmethod
    def _llms_txt_url(pages: list[Page]) -> str | None:
        """<origin>/llms.txt for the first thin docs page, once per app (fetched as its own page)."""
        for p in pages:
            if p.ok and len(p.text) < THIN_CHARS and is_docs_url(p.final_url):
                return f"{origin(p.final_url)}/llms.txt"
        return None

    @staticmethod
    def mcp_llms_txt_urls(app: dict, pages: list[Page], row: dict | None) -> list[str]:
        """llms.txt on the official docs and start origins while MCP is unresolved, thin pages or not.

        Unresolved = no row or mcp_status none, and no fetched page has "mcp" in its URL. Each origin is
        fetched at most once per app (_fetch_new dedupes against the thin-page llms.txt too).
        """
        if row and row.get("mcp_status") not in (None, "none"):
            return []
        if any(p.ok and "mcp" in f"{p.requested_url} {p.final_url}".lower() for p in pages):
            return []
        candidates = [(row or {}).get("docs_url"), app.get("start_url")]
        candidates += [p.final_url for p in pages if p.ok and is_docs_url(p.final_url)][:1]
        origins: list[str] = []
        for u in candidates:
            if isinstance(u, str) and u.startswith("http") and classify_result(app, {"url": u}) == OFFICIAL:
                o = origin(u)
                if o not in origins:
                    origins.append(o)
        return [f"{o}/llms.txt" for o in origins[:MCP_LLMS_TXT_MAX]]

    def _save_attempts(self, slug: str, attempts: list[dict]) -> None:
        cache = self.run_dir / "cache"
        cache.mkdir(parents=True, exist_ok=True)
        (cache / f"{slug}.llm.json").write_text(json.dumps({"slug": slug, "attempts": attempts}, indent=2,
                                                           ensure_ascii=False))

    # row --------------------------------------------------------------
    @staticmethod
    def _unwrap(out: dict) -> dict | None:
        """The row object, whether under "row", at top level, or under some other single wrapper key."""
        def looks_like_row(d: object) -> bool:
            return isinstance(d, dict) and sum(k in d for k in RESEARCHED_KEYS) >= 3

        if looks_like_row(out.get("row")):
            return out["row"]
        if looks_like_row(out):
            return out
        for v in out.values():
            if looks_like_row(v):
                return v
            if isinstance(v, dict) and looks_like_row(v.get("row")):
                return v["row"]
        return None

    def _assemble(self, app: dict, llm_row: dict) -> dict:
        row = {k: app[k] for k in IDENTITY_KEYS}
        row.update({k: llm_row.get(k) for k in RESEARCHED_KEYS})
        row["notes"] = llm_row.get("notes") or ""
        row["composio_toolkit"] = dict(self.coverage.get(app["slug"], UNKNOWN_COVERAGE))
        return {k: row[k] for k in ROW_ORDER}

    def _evaluate(self, app: dict, res: LLMResult, pages: list[Page]) -> tuple[dict | None, dict, dict, list[str]]:
        """Parse, assemble, evidence-check and validate one LLM answer.

        Output-level problems (truncation, unparseable JSON, no row fields) come back
        as a single explicit "output:" error, not as a pile of "None is not of type" errors.
        """
        try:
            out = extract_json(res.text)
        except ValueError as e:
            if res.truncated:
                return None, {}, {}, [prompts.TRUNCATED_ERROR.format(stop=res.stop_reason)]
            return None, {}, {}, [prompts.UNPARSEABLE_ERROR.format(detail=e)]
        llm_row = self._unwrap(out)
        if llm_row is None:
            return None, {}, {}, [prompts.NO_ROW_ERROR.format(keys=", ".join(list(out)[:10]))]
        row = self._assemble(app, llm_row)
        snippets = next((s for s in (out.get("snippets"), llm_row.get("snippets")) if isinstance(s, dict)), {})
        row, checks = check_evidence(row, snippets, pages)
        identity = {k: app[k] for k in IDENTITY_KEYS}
        return row, snippets, checks | {"follow_up": out.get("follow_up") or {}}, validate_row(row, identity)

    def research(self, app: dict) -> Outcome:
        t0 = time.monotonic()
        slug = app["slug"]
        stats = Stats()
        token = current_app.set(slug)  # attributes Instant spend to this app
        attempts: list[dict] = []
        seen: set[str] = set()
        try:
            searches = [self._search(q, stats) for q in search_queries(app)]
            pages = self._fetch_new(slug, pick_urls(app, searches, self.max_pages), stats, seen)
            llms_txt = self._llms_txt_url(pages)
            if llms_txt:
                pages += self._fetch_new(slug, [llms_txt], stats, seen)

            messages = [{"role": "user", "content": prompts.research_prompt(app, pages, self.per_page_chars,
                                                                             self.follow_up)}]
            res = self._llm(messages, stats, attempts, "initial", slug)
            row, snippets, checks, errors = self._evaluate(app, res, pages)

            follow = checks.get("follow_up") or {}
            if self.follow_up and (follow.get("queries") or follow.get("urls")):
                queries = list(follow.get("queries") or [])[:FOLLOW_UP_MAX_QUERIES]
                extra_searches = [self._search(q, stats) for q in queries]
                searches += extra_searches
                wanted = [u for u in list(follow.get("urls") or []) if isinstance(u, str) and u.startswith("http")]
                urls = list({normalize_url(u): u for u in wanted if normalize_url(u) not in seen}.values())
                urls = urls[:FOLLOW_UP_MAX_PAGES]
                urls += pick_urls(app, extra_searches, FOLLOW_UP_MAX_PAGES - len(urls),
                                  seen | {normalize_url(u) for u in urls}, include_start=False)
                pages += self._fetch_new(slug, urls, stats, seen)
                messages = [{"role": "user",
                             "content": prompts.research_prompt(app, pages, self.per_page_chars, False)}]
                res = self._llm(messages, stats, attempts, "follow_up", slug)
                row, snippets, checks, errors = self._evaluate(app, res, pages)

            # Before settling on mcp_status none: llms.txt often lists an MCP docs page the search missed.
            new = self._fetch_new(slug, self.mcp_llms_txt_urls(app, pages, row), stats, seen)
            new += self._fetch_new(slug, [u for p in new if p.ok for u in mcp_links(p)], stats, seen)
            pages += new
            if any(p.ok and MCP_TEXT_RE.search(p.text) for p in new):
                messages = [{"role": "user",
                             "content": prompts.research_prompt(app, pages, self.per_page_chars, False)}]
                res = self._llm(messages, stats, attempts, "mcp_check", slug)
                row, snippets, checks, errors = self._evaluate(app, res, pages)

            retried = False
            if errors:
                retried = True
                messages += [{"role": "assistant", "content": res.text.strip() or "(empty response)"},
                             {"role": "user", "content": prompts.retry_prompt(errors, checks)}]
                res = self._llm(messages, stats, attempts, "retry", slug)
                row, snippets, checks, errors = self._evaluate(app, res, pages)
        finally:
            current_app.reset(token)
            self._save_attempts(slug, attempts)

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
    budget = researcher.budget or InstantBudget(0)

    def instant_for(slug: str) -> dict:
        return budget.app_stats(slug)

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
            **instant_for(out.slug),
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
        **budget.snapshot(),
    }
