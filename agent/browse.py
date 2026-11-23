"""Pass-3 browser verification: open the vendor's real pages for rows that are still unsure.

For each row of a pass-2 run:
  browse_flags.flag_row -> no flags: the row is copied through unchanged, no browser.
  Flagged: up to MAX_URLS pages (cited access/auth/MCP/verdict pages, start_url, docs_url, mcp_url) are opened
  in a local headless Chromium driven by Browser Use (open source). Only a page that comes back empty, shows
  a captcha, or is blocked is retried on a Browser Use Cloud browser (needs BROWSER_USE_API_KEY; one cloud
  session per row at most, started on the first fallback). Rendered text + a JPEG screenshot are kept.
  -> one LLM call (the run's own provider) that may confirm or correct only the in-scope fields, quoting the
  rendered text -> evidence check + validation (one retry). If that fails, the pass-2 values are kept with a
  note. Every row gets a record in browse.jsonl and flags.jsonl.
"""

from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import logging
import os
import re
import threading
import time
from collections import Counter
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

from agent import prompts
from agent.browse_flags import DECIDING_CLAIMS, Flag, flag_claims, flag_row, flag_urls
from agent.config import Settings
from agent.llm import LLM
from agent.research import IDENTITY_KEYS, ROW_ORDER, Stats, _read_done, is_pricing_url
from agent.review import Reviewer, _changes
from agent.review_rules import COUPLED, claim_fields
from agent.tools.base import Page, now_iso
from agent.validate import normalize_url, validate_row

log = logging.getLogger(__name__)

MAX_URLS = 4
LOCAL_CHROME = "/usr/bin/google-chrome"
CLOUD_KEY_ENV = "BROWSER_USE_API_KEY"
CLOUD_TIMEOUT_MIN = 5  # a cloud browser is stopped by the service after this, even if we crash
LOAD_TIMEOUT_S = 20
SETTLE_TIMEOUT_S = 8
EMPTY_CHARS = 200  # rendered text shorter than this counts as empty
CHALLENGE_MAX_CHARS = 2500  # captcha/blocked wording only counts on a short page (docs may mention captchas)
SCREENSHOT_TIMEOUT_S = 8

# ---------- empty / captcha / blocked ----------

CAPTCHA_HTML = re.compile(r"g-recaptcha|recaptcha/api|h-captcha|hcaptcha\.com|cf-turnstile|challenge-platform"
                          r"|cf-challenge|cf_chl_|captcha-delivery|px-captcha|arkoselabs", re.I)
CAPTCHA_TEXT = re.compile(r"\b(?:recaptcha|hcaptcha|captcha)\b|verify\s+(?:that\s+)?you\s+are\s+(?:a\s+)?human"
                          r"|are\s+you\s+a\s+robot|i'?m\s+not\s+a\s+robot|press\s+(?:&|and)\s+hold"
                          r"|checking\s+(?:if\s+the\s+site\s+connection\s+is\s+secure|your\s+browser)"
                          r"|verifying\s+you\s+are\s+human", re.I)
BLOCKED_TEXT = re.compile(r"\baccess\s+denied\b|\bunusual\s+traffic\b|\b(?:request|you\s+have\s+been|you\s+are)"
                          r"\s+blocked\b|\b403\s+forbidden\b|\bforbidden\b|attention\s+required"
                          r"|pardon\s+our\s+interruption|\bbot\s+(?:detection|protection)\b"
                          r"|enable\s+javascript\s+and\s+cookies|please\s+enable\s+(?:javascript|cookies)"
                          r"|too\s+many\s+requests|rate\s+limited", re.I)
BLOCKED_STATUS = {401, 403, 429, 503}


def render_problem(text: str, title: str = "", html: str = "", status: int | None = None) -> str | None:
    """'captcha' | 'blocked' | 'empty' for a rendered page that cannot be used, else None. General rules only."""
    body = (text or "").strip()
    head = f"{title or ''}\n{body}" if len(body) <= CHALLENGE_MAX_CHARS else (title or "")
    if CAPTCHA_HTML.search(html or "") and len(body) <= CHALLENGE_MAX_CHARS or CAPTCHA_TEXT.search(head):
        return "captcha"
    if status in BLOCKED_STATUS or BLOCKED_TEXT.search(head):
        return "blocked"
    if len(body) < EMPTY_CHARS:
        return "empty"
    return None


# ---------- browsers ----------

@dataclass
class Render:
    url: str
    final_url: str = ""
    title: str = ""
    text: str = ""
    html: str = ""
    status: int | None = None
    screenshot: bytes | None = None
    error: str | None = None

    @property
    def problem(self) -> str | None:
        return render_problem(self.text, self.title, self.html, self.status)


_PAGE_JS = """JSON.stringify({
  title: document.title || "",
  href: location.href,
  text: document.body ? document.body.innerText : "",
  html: document.documentElement ? document.documentElement.outerHTML.slice(0, 60000) : "",
  status: (performance.getEntriesByType("navigation")[0] || {}).responseStatus || null
})"""


class BrowserUseDriver:
    """One Browser Use browser (local headless Chromium, or a cloud browser) driven through its CDP helpers."""

    def __init__(self, kind: str):
        self.kind = kind  # local | cloud
        self._browser = None
        self._loop: asyncio.AbstractEventLoop | None = None

    def start(self) -> None:
        from browser_use import Browser

        self._loop = asyncio.new_event_loop()
        if self.kind == "local":
            # Our own headless Chromium. Never Browser('chrome'): that attaches to the desktop Chrome.
            self._browser = Browser("chromium", headless=True, executable_path=LOCAL_CHROME)
        else:  # reads BROWSER_USE_API_KEY from the process environment; the key is never logged
            self._browser = Browser("cloud", live_view=False, cloud_timeout=CLOUD_TIMEOUT_MIN)
        self._loop.run_until_complete(self._browser.start())

    def _eval(self, cdp, expression: str):
        return cdp("Runtime.evaluate", expression=expression, returnByValue=True).get("result", {}).get("value")

    def open(self, url: str) -> Render:
        cdp = self._browser._cdp()
        nav = cdp("Page.navigate", url=url)
        if nav.get("errorText"):
            return Render(url, url, error=f"navigate: {nav['errorText']}")
        deadline = time.monotonic() + LOAD_TIMEOUT_S
        while time.monotonic() < deadline and self._eval(cdp, "document.readyState") != "complete":
            time.sleep(0.25)
        # Wait for client-side rendering: text length stable across two polls.
        last, deadline = -1, time.monotonic() + SETTLE_TIMEOUT_S
        while time.monotonic() < deadline:
            time.sleep(0.75)
            n = self._eval(cdp, "document.body ? document.body.innerText.length : 0") or 0
            if n == last and n > 0:
                break
            last = n
        info = json.loads(self._eval(cdp, _PAGE_JS) or "{}")
        shot = self._browser._screenshot(timeout=SCREENSHOT_TIMEOUT_S)
        return Render(url, info.get("href") or url, info.get("title") or "", info.get("text") or "",
                      info.get("html") or "", info.get("status"), shot)

    def stop(self) -> None:
        try:
            if self._browser is not None and self._loop is not None:
                self._loop.run_until_complete(self._browser.stop())
        finally:
            if self._loop is not None:
                self._loop.close()
            self._browser, self._loop = None, None


def cloud_key_present() -> bool:
    return bool(os.environ.get(CLOUD_KEY_ENV, "").strip())


class BrowserPool:
    """Per-row browsers: local first for every URL, a cloud browser only for a URL the local one could not read.

    Each browser is started lazily on first use and at most once per row (a failed start is not retried).
    """

    def __init__(self, make_driver: Callable[[str], object] = BrowserUseDriver,
                 has_cloud_key: Callable[[], bool] = cloud_key_present):
        self.make_driver = make_driver
        self.has_cloud_key = has_cloud_key
        self.drivers: dict[str, object] = {}
        self.start_errors: dict[str, str] = {}
        self.cloud_sessions = 0

    def _driver(self, kind: str):
        if kind in self.start_errors:
            return None
        if kind not in self.drivers:
            d = self.make_driver(kind)
            self.drivers[kind] = d  # kept even if start fails, so shutdown() can clean a half-started browser
            try:
                d.start()
            except Exception as e:
                self.start_errors[kind] = f"{type(e).__name__}: {str(e)[:200]}"
                log.warning("%s browser failed to start: %s", kind, self.start_errors[kind])
                return None
            if kind == "cloud":
                self.cloud_sessions += 1
        return self.drivers[kind]

    def _open(self, kind: str, url: str) -> Render:
        d = self._driver(kind)
        if d is None:
            return Render(url, url, error=f"{kind} browser did not start: {self.start_errors[kind]}")
        try:
            return d.open(url)
        except Exception as e:
            return Render(url, url, error=f"{type(e).__name__}: {str(e)[:200]}")

    def open(self, url: str) -> tuple[Render, dict]:
        """(render that is kept, log record without the screenshot path)."""
        local = self._open("local", url)
        problem = local.problem
        rec = {"url": url, "browser": "local", "why_cloud": None, "local_problem": problem,
               "local_error": local.error, "cloud_skipped": None, "cloud_problem": None, "cloud_error": None}
        if problem is None:
            return local, rec
        if not self.has_cloud_key():
            rec["cloud_skipped"] = "cloud_skipped_no_key"
            return local, rec
        cloud = self._open("cloud", url)
        rec["why_cloud"] = problem
        rec["cloud_problem"], rec["cloud_error"] = cloud.problem, cloud.error
        if cloud.problem is None:
            rec["browser"] = "cloud"
            return cloud, rec
        return local, rec  # cloud did not help either: keep the local result

    def shutdown(self) -> None:
        for kind, d in list(self.drivers.items()):
            try:
                d.stop()
            except Exception as e:
                log.warning("%s browser failed to stop cleanly: %s", kind, type(e).__name__)
        self.drivers.clear()


# ---------- which pages to open ----------

LOGIN_PATH = re.compile(r"(?:^|/)(?:log-?in|sign-?in|signin|sso|session/new|users/sign_in|auth/login)(?:/|$)", re.I)
LOGIN_HOST = re.compile(r"^(?:login|signin|sso|accounts?|auth)\.", re.I)


def is_login_wall(url: str) -> bool:
    p = urlsplit(url)
    return bool(LOGIN_PATH.search(p.path) or LOGIN_HOST.match(p.hostname or ""))


def choose_urls(app: dict, row: dict, flags: list[Flag], claims: list[str], cap: int = MAX_URLS) -> list[str]:
    """Up to `cap` pages: pages flagged thin/blocked first, then one per claim in turn (access prefers
    pricing/signup and start_url, auth docs_url, mcp mcp_url). Login walls are skipped."""
    ev = row.get("evidence") if isinstance(row.get("evidence"), dict) else {}

    def cited(claim: str) -> list[str]:
        return [i.get("url") for i in ev.get(claim) or [] if isinstance(i, dict)]

    groups: list[list[str]] = [flag_urls(flags)]
    if "access" in claims:
        access = cited("access")
        groups.append([u for u in access if isinstance(u, str) and is_pricing_url(u)] + [app.get("start_url")]
                      + access)
    if "auth" in claims:
        groups.append(cited("auth") + [row.get("docs_url")])
    if "mcp" in claims:
        groups.append([row.get("mcp_url")] + cited("mcp"))
    if "verdict" in claims:
        groups.append(cited("verdict"))

    out: list[str] = []
    seen: set[str] = set()
    queues = [[u for u in g if isinstance(u, str) and u.startswith(("http://", "https://"))] for g in groups]
    while len(out) < cap and any(queues):
        for q in queues:
            while q:
                u = q.pop(0)
                key = normalize_url(u)
                if key in seen or is_login_wall(u):
                    continue
                seen.add(key)
                out.append(u)
                break
            if len(out) >= cap:
                break
    return out


# ---------- per-row verification ----------

@dataclass
class BrowseOutcome:
    slug: str
    row: dict | None
    errors: list[str]
    status: str  # copied | confirmed | changed | kept | failed
    flags: list[Flag]
    fields_in_scope: list[str]
    claims: list[str]
    pages: list[dict]
    retried: bool
    cloud_sessions: int
    evidence: dict
    stats: Stats
    wall_s: float
    fields_changed: list[str] = field(default_factory=list)
    fields_confirmed: list[str] = field(default_factory=list)
    before: dict = field(default_factory=dict)
    after: dict = field(default_factory=dict)


def browse_scope(claims: list[str]) -> list[str]:
    """Fields the browser pass may change for these claims: their fields, coupled fields, and confidence."""
    fields: list[str] = []
    for name in claim_fields(claims):
        for x in (name, *COUPLED.get(name, ())):
            if x not in fields:
                fields.append(x)
    return [*fields, "confidence"]


class Verifier(Reviewer):
    """Reuses the reviewer's LLM / fill-evaluation plumbing; no search or fetch tools."""

    def __init__(self, settings: Settings, llm: LLM, run_dir: Path,
                 make_pool: Callable[[], BrowserPool] = BrowserPool, per_page_chars: int = 6000):
        super().__init__(settings, llm, None, None, run_dir, per_page_chars=per_page_chars)
        self.make_pool = make_pool

    def _render_pages(self, slug: str, urls: list[str]) -> tuple[list[Page], list[dict], int]:
        pool = self.make_pool()
        pages, records = [], []
        shots = self.run_dir / "screenshots" / slug
        try:
            for i, url in enumerate(urls, 1):
                t0 = time.monotonic()
                r, rec = pool.open(url)
                path = None
                if r.screenshot:
                    shots.mkdir(parents=True, exist_ok=True)
                    name = f"{i:02d}-{hashlib.sha1(url.encode()).hexdigest()[:10]}.jpg"
                    (shots / name).write_bytes(r.screenshot)
                    path = f"screenshots/{slug}/{name}"
                usable = r.problem is None and not r.error
                rec.update(final_url=r.final_url or url, title=r.title[:200], text_len=len(r.text.strip()),
                           http_status=r.status, screenshot=path, usable=usable,
                           wall_s=round(time.monotonic() - t0, 2))
                records.append(rec)
                log.info("%s: browser=%s url=%s why_cloud=%s%s text_len=%d screenshot=%s", slug, rec["browser"],
                         url, rec["why_cloud"], f" ({rec['cloud_skipped']})" if rec["cloud_skipped"] else "",
                         rec["text_len"], path)
                if usable:
                    pages.append(Page(url, r.final_url or url, r.status or 200, f"browser_{rec['browser']}",
                                      text=r.text, method=f"browser_{rec['browser']}"))
        finally:
            pool.shutdown()
        return pages, records, pool.cloud_sessions

    @staticmethod
    def _annotate(row: dict, claims: list[str], records: list[dict]) -> None:
        """Point in-scope evidence items at the screenshot of the rendered page they cite."""
        shots = {}
        for r in records:
            if r["usable"] and r["screenshot"]:
                shots[normalize_url(r["url"])] = shots[normalize_url(r["final_url"])] = r["screenshot"]
        for c in claims:
            for item in row["evidence"].get(c) or []:
                path = shots.get(normalize_url(item.get("url") or ""))
                if path:
                    note = (item.get("note") or "").strip()
                    item["note"] = f"{note} (rendered in browser; screenshot {path})".strip()

    def verify(self, app: dict, row: dict, evidence: dict | None = None, review: dict | None = None
               ) -> BrowseOutcome:
        t0 = time.monotonic()
        slug = app["slug"]
        stats = Stats()
        identity = {k: app[k] for k in IDENTITY_KEYS}
        original = copy.deepcopy(row)
        base = {**copy.deepcopy(row), **identity}
        flags = flag_row(base, evidence, review)
        prior = copy.deepcopy(evidence or {"slug": slug})
        if not flags:
            final = {k: base.get(k) for k in ROW_ORDER}
            prior["browse"] = {"flagged": False, "flags": []}
            return BrowseOutcome(slug, final, validate_row(final, identity), "copied", [], [], [], [], False, 0,
                                 prior, stats, round(time.monotonic() - t0, 2))

        claims = flag_claims(flags) or list(DECIDING_CLAIMS)
        fields = browse_scope(claims)
        urls = choose_urls(app, base, flags, claims)
        attempts: list[dict] = []
        snippets: dict = {}
        checks: dict = {}
        retried = False
        try:
            pages, records, cloud_sessions = self._render_pages(slug, urls)
            fixed, errors = None, ["no rendered page was usable"]
            if pages:
                messages = [{"role": "user", "content": prompts.browse_prompt(
                    app, base, [f.to_dict() for f in flags], fields, claims, pages, self.per_page_chars)}]
                res = self._llm(messages, stats, attempts, "browse", slug)
                fixed, snippets, checks, errors = self._evaluate_fill(app, base, res, pages, fields, claims)
                if errors:
                    retried = True
                    messages += [{"role": "assistant", "content": res.text.strip() or "(empty response)"},
                                 {"role": "user", "content": prompts.review_retry_prompt(errors, checks)}]
                    res = self._llm(messages, stats, attempts, "browse_retry", slug)
                    fixed, snippets, checks, errors = self._evaluate_fill(app, base, res, pages, fields, claims)
            if errors or fixed is None:
                # Never invent support: keep the pass-2 values and say the browser could not settle them.
                final = copy.deepcopy(base)
                why = "no rendered page was usable" if not pages else "the rendered pages did not support a value"
                note = (f"Pass-3 browser check ({', '.join(f.reason for f in flags)}): {why}; pass-2 values kept.")
                final["notes"] = f"{final.get('notes') or ''} {note}".strip()
                checks["fill_errors"] = errors
                snippets = {}
                errors = validate_row(final, identity)
                status = "failed" if errors else "kept"
            else:
                final = fixed
                self._annotate(final, claims, records)
                moved = [f for f in fields if final.get(f) != base.get(f)]
                status = "changed" if moved else "confirmed"
        finally:
            if attempts:
                self._save_attempts(slug, attempts)

        final = {k: final.get(k) for k in ROW_ORDER}
        changed, before, after = _changes(original, final)
        confirmed = [f for f in fields if f not in changed] if status in ("confirmed", "changed") else []
        for s_items in snippets.values():
            for s in s_items if isinstance(s_items, list) else []:
                rec = next((r for r in records if normalize_url(r["url"]) == normalize_url(s.get("url") or "")
                            or normalize_url(r["final_url"]) == normalize_url(s.get("url") or "")), None)
                if rec:
                    s.update(browser=rec["browser"], screenshot=rec["screenshot"])
        checks.update(retried=retried, validation_errors=errors)
        prior["pages"] = (prior.get("pages") or []) + [p.meta() for p in pages]
        prior["snippets"] = {**(prior.get("snippets") or {}), **snippets}
        prior["checks"] = checks
        prior["browse"] = {"flagged": True, "flags": [f.to_dict() for f in flags], "fields_in_scope": fields,
                           "claims": claims, "pages": records, "cloud_sessions": cloud_sessions}
        return BrowseOutcome(slug, final, errors, status, flags, fields, claims, records, retried, cloud_sessions,
                             prior, stats, round(time.monotonic() - t0, 2), changed, confirmed, before, after)


# ---------- run driver ----------

def load_review_log(from_dir: Path) -> dict[str, dict]:
    path = from_dir / "review.jsonl"
    out: dict[str, dict] = {}
    if path.exists():
        for line in path.read_text().splitlines():
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(rec, dict) and rec.get("slug"):
                out[rec["slug"]] = rec
    return out


def load_evidence(from_dir: Path, slug: str) -> dict | None:
    path = from_dir / "evidence" / f"{slug}.json"
    try:
        return json.loads(path.read_text()) if path.exists() else None
    except json.JSONDecodeError:
        return None


def browse_run(items: list[tuple[dict, dict]], verifier: Verifier, from_dir: Path, run_dir: Path,
               concurrency: int = 1) -> dict:
    """Verify (app, row) pairs from `from_dir` into `run_dir`, skipping slugs already in its rows.jsonl."""
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "evidence").mkdir(exist_ok=True)
    paths = {k: run_dir / f"{k}.jsonl" for k in ("rows", "failures", "log", "browse", "flags")}
    done = _read_done(paths["rows"])
    todo = [(a, r) for a, r in items if a["slug"] not in done]
    reviews = load_review_log(from_dir)
    lock = threading.Lock()
    summary = Counter(skipped=len(items) - len(todo))
    reasons = Counter()
    totals = Counter()
    settings = verifier.settings

    def append(path: Path, obj: dict) -> None:
        with path.open("a") as f:
            f.write(json.dumps(obj, ensure_ascii=False) + "\n")

    def one(app: dict, row: dict) -> None:
        t0 = time.monotonic()
        slug = app["slug"]
        try:
            out = verifier.verify(app, row, load_evidence(from_dir, slug), reviews.get(slug))
        except Exception as e:  # keep going; record the crash
            log.exception("browse crashed for %s", slug)
            rec = {"slug": slug, "status": "crash", "error": f"{type(e).__name__}: {e}",
                   "wall_s": round(time.monotonic() - t0, 2), "at": now_iso()}
            with lock:
                append(paths["failures"], {"slug": slug, "errors": [f"crash: {rec['error']}"], "row": None,
                                           "at": now_iso()})
                append(paths["log"], rec)
                append(paths["browse"], rec)
                summary["crashed"] += 1
            return
        s = out.stats
        usage = {"llm_calls": s.llm_calls, "input_tokens": s.input_tokens, "output_tokens": s.output_tokens,
                 "est_cost_usd": settings.est_cost(s.input_tokens, s.output_tokens),
                 "exa_calls": 0, "exa_cost_usd": 0.0}  # the browser pass never searches or fetches through Exa
        flagged = bool(out.flags)
        record = {
            "slug": slug, "status": out.status, "flagged": flagged,
            "flag_reasons": [f.reason for f in out.flags], "flags": [f.to_dict() for f in out.flags],
            "fields_in_scope": out.fields_in_scope, "claims": out.claims, "pages": out.pages,
            "pages_local": sum(p["browser"] == "local" for p in out.pages),
            "pages_cloud": sum(p["browser"] == "cloud" for p in out.pages),
            "cloud_used": out.cloud_sessions > 0, "cloud_sessions": out.cloud_sessions,
            "retried": out.retried, "changed": bool(out.fields_changed), "fields_changed": out.fields_changed,
            "fields_confirmed": out.fields_confirmed, "before": out.before, "after": out.after,
            "validation_errors": out.errors, **usage, "wall_s": out.wall_s,
            "llm_provider": verifier.llm.provider, "llm_model": verifier.llm.model, "at": now_iso(),
        }
        (run_dir / "evidence" / f"{slug}.json").write_text(json.dumps(out.evidence, indent=2, ensure_ascii=False))
        with lock:
            if out.errors:
                append(paths["failures"], {"slug": slug, "errors": out.errors, "row": out.row, "at": now_iso()})
            else:
                append(paths["rows"], out.row)
            append(paths["flags"], {"slug": slug, "flagged": flagged, "reasons": record["flag_reasons"],
                                    "flags": record["flags"]})
            append(paths["log"], {"slug": slug, "status": "failed" if out.errors else "ok",
                                  "browse_status": out.status, **usage, "wall_s": out.wall_s,
                                  "cloud_sessions": out.cloud_sessions, "at": now_iso()})
            append(paths["browse"], record)
            summary[out.status] += 1
            summary["flagged"] += flagged
            reasons.update(record["flag_reasons"])
            totals.update(input_tokens=s.input_tokens, output_tokens=s.output_tokens,
                          cloud_sessions=out.cloud_sessions, pages_local=record["pages_local"],
                          pages_cloud=record["pages_cloud"])
        log.info("%s: %s, flags=%s, changed=%s, cloud_sessions=%d in %.1fs", slug, out.status,
                 record["flag_reasons"], out.fields_changed, out.cloud_sessions, out.wall_s)

    with ThreadPoolExecutor(max_workers=max(1, concurrency)) as pool:
        for fut in as_completed([pool.submit(one, a, r) for a, r in todo]):
            fut.result()

    return {
        "run_dir": str(run_dir),
        "from_dir": str(from_dir),
        "requested": len(items),
        "skipped_done": summary["skipped"],
        "flagged": summary["flagged"],
        **{k: summary[k] for k in ("copied", "confirmed", "changed", "kept", "failed", "crashed")},
        "flag_reasons": dict(reasons.most_common()),
        "pages_local": totals["pages_local"],
        "pages_cloud": totals["pages_cloud"],
        "cloud_sessions": totals["cloud_sessions"],
        "input_tokens": totals["input_tokens"],
        "output_tokens": totals["output_tokens"],
        "est_cost_usd": settings.est_cost(totals["input_tokens"], totals["output_tokens"]),
        "exa_cost_usd": 0.0,
    }
