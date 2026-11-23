"""Pass-2 reviewer: recheck existing rows against a general rule checklist and re-research only what fails.

For each row of an earlier run:
  deterministic rule checks + validate_row -> one LLM reviewer call that lists which rule ids apply
  (it does not rewrite the row) -> union = rules fired.
  Nothing fired: the row is written through unchanged.
  Something fired: field-specific searches + fetches (official domain first, cited pages re-read, gap-fill
  for MCP/auth) -> one LLM fill that may change only the affected fields, their evidence and notes ->
  evidence check + validation (one retry). If it still fails, the pass-1 row is kept with a note.
Every row gets a structured record in review.jsonl.
"""

from __future__ import annotations

import copy
import hashlib
import json
import logging
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path

from agent import prompts
from agent.config import Settings
from agent.llm import LLM, LLMResult, extract_json
from agent.research import (
    DOC_HOSTS,
    IDENTITY_KEYS,
    ROW_ORDER,
    Researcher,
    Stats,
    _read_done,
    base_domain,
    gap_fill_urls,
    mcp_links,
    official_host,
    pick_urls,
    query_context,
)
from agent.review_rules import (
    CRITICAL_CLAIMS,
    RULES,
    Finding,
    applies,
    claim_fields,
    deterministic_findings,
    merge_findings,
    rules_fired,
    scope,
    validator_findings,
)
from agent.tools.base import Page, now_iso
from agent.tools.budget import ExaBudget, InstantBudget, current_app
from agent.validate import check_evidence, normalize_url, validate_row

log = logging.getLogger(__name__)

REVIEW_MAX_PAGES = 6  # pages per re-researched row before gap-fill / llms.txt (same rules as run)
REVIEW_MAX_QUERIES = 6
CACHED_PAGES_MAX = 6  # pass-1 page texts shown to the LLM reviewer
CACHED_PAGE_CHARS = 3000


# ---------- inputs ----------

def load_rows(from_dir: Path) -> list[dict]:
    path = from_dir / "rows.jsonl"
    if not path.exists():
        raise FileNotFoundError(f"{path} does not exist")
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def load_prior(from_dir: Path, slug: str) -> tuple[dict | None, list[Page]]:
    """The pass-1 evidence file for a slug, plus any pass-1 page text still in its cache/ folder."""
    path = from_dir / "evidence" / f"{slug}.json"
    if not path.exists():
        return None, []
    try:
        evidence = json.loads(path.read_text())
    except json.JSONDecodeError:
        return None, []
    pages = []
    for meta in evidence.get("pages") or []:
        url = meta.get("requested_url") or ""
        cached = from_dir / "cache" / slug / f"{hashlib.sha1(url.encode()).hexdigest()[:12]}.txt"
        if url and cached.exists():
            pages.append(Page(url, meta.get("final_url") or url, meta.get("status") or 200,
                              meta.get("provider") or "cache", text=cached.read_text(), method="pass1_cache"))
    return evidence, pages


# ---------- targeted search ----------

def review_queries(app: dict, claims: list[str], rules: list[str]) -> list[str]:
    """Field-specific queries, one primary query per claim first, then rule-specific extras."""
    context = query_context(app)
    n = f'"{app["name"]}" {context}' if context else app["name"]
    host = official_host(app)
    sites: list[str] = []
    if host and host not in DOC_HOSTS:
        sites.append(host)
        parent = base_domain(host)
        if parent != host and parent not in DOC_HOSTS:
            sites.append(parent)
    primary, extra = [], []
    if "auth" in claims:
        primary.append(f"{n} API authentication OAuth API key")
        extra += [f"site:{h} API authentication" for h in sites[:1]]
    if "access" in claims:
        primary.append(f"{n} API access pricing plans signup")
        if "sales_or_review_gate" in rules:
            extra.append(f"{n} API access partner program app review approval")
    if "api_surface" in claims:
        graphql = "official_graphql_omitted" in rules
        primary.append(f"{n} GraphQL API" if graphql else f"{n} API reference documentation")
        if "official_graphql_omitted" in rules or "public_docs_marked_false" in rules:
            extra.append(f"{n} API reference documentation")
    if "mcp" in claims:
        primary.append(f"{n} official MCP server")
        extra += [f"site:{h} MCP" for h in sites]
    if "verdict" in claims:
        primary.append(f"{n} API beta preview general availability" if "preview_not_buildable_now" in rules
                       else f"{n} developer API availability")
    return list(dict.fromkeys(primary + extra))[:REVIEW_MAX_QUERIES]


# ---------- merging a targeted fill ----------

def merge_fill(base: dict, llm_row: dict, fields: list[str], claims: list[str]) -> dict:
    """Copy of `base` where only `fields`, the evidence lists of `claims`, and notes come from the LLM."""
    row = copy.deepcopy(base)
    for f in fields:
        if f in llm_row:
            row[f] = llm_row[f]
    evidence = llm_row.get("evidence") if isinstance(llm_row.get("evidence"), dict) else {}
    for c in claims:
        if c in evidence:
            row["evidence"][c] = evidence[c]
    if isinstance(llm_row.get("notes"), str):
        row["notes"] = llm_row["notes"]
    return row


def _changes(before: dict, after: dict) -> tuple[list[str], dict, dict]:
    """Changed top-level fields, with before/after values (evidence: only the changed claims)."""
    changed = [k for k in ROW_ORDER if before.get(k) != after.get(k)]
    b, a = {}, {}
    for k in changed:
        if k == "evidence":
            eb, ea = before.get(k) or {}, after.get(k) or {}
            keys = [c for c in dict.fromkeys([*eb, *ea]) if eb.get(c) != ea.get(c)]
            b[k], a[k] = {c: eb.get(c) for c in keys}, {c: ea.get(c) for c in keys}
        else:
            b[k], a[k] = before.get(k), after.get(k)
    return changed, b, a


# ---------- per-row review ----------

@dataclass
class ReviewOutcome:
    slug: str
    row: dict | None
    errors: list[str]
    status: str  # unchanged | confirmed | changed | kept | failed
    findings: list[Finding]
    ignored_llm: list[dict]
    reviewer_error: str | None
    fields_in_scope: list[str]
    claims: list[str]
    re_researched: bool
    retried: bool
    evidence: dict
    stats: Stats
    wall_s: float
    fields_changed: list[str] = field(default_factory=list)
    before: dict = field(default_factory=dict)
    after: dict = field(default_factory=dict)


class Reviewer(Researcher):
    """Reuses the Researcher's search / fetch / LLM / budget plumbing; follow-up rounds are off."""

    def __init__(self, settings: Settings, llm: LLM, search, fetch, run_dir: Path,
                 max_pages: int = REVIEW_MAX_PAGES, per_page_chars: int = 6000,
                 budget: InstantBudget | None = None, exa_budget: ExaBudget | None = None):
        super().__init__(settings, llm, search, fetch, run_dir, max_pages=max_pages, follow_up=False,
                         per_page_chars=per_page_chars, budget=budget, exa_budget=exa_budget)
        self.review_system = prompts.review_system({rid: text for rid, (text, _) in RULES.items()})

    # reviewer call -------------------------------------------------------
    def _llm_findings(self, app: dict, row: dict, evidence: dict | None, cached: list[Page], stats: Stats,
                      attempts: list[dict]) -> tuple[list[Finding], list[dict], str | None]:
        """(findings, ignored hits, error). Hits whose rule cannot apply to this row are ignored."""
        cited = {normalize_url(i.get("url") or "") for items in (row.get("evidence") or {}).values()
                 if isinstance(items, list) for i in items if isinstance(i, dict)}
        pages = [p for p in cached if normalize_url(p.requested_url) in cited or normalize_url(p.final_url) in cited]
        snippets = (evidence or {}).get("snippets") or {}
        messages = [{"role": "user", "content": prompts.review_prompt(app, row, snippets, pages[:CACHED_PAGES_MAX],
                                                                       CACHED_PAGE_CHARS)}]
        res = self._llm(messages, stats, attempts, "review", app["slug"], system=self.review_system)
        try:
            out = extract_json(res.text)
        except ValueError as e:
            return [], [], f"reviewer output unparseable: {e}"
        items = out.get("rules")
        if not isinstance(items, list):
            return [], [], "reviewer output had no rules list"
        found, ignored = [], []
        for it in items:
            rid = it.get("id") if isinstance(it, dict) else None
            if rid not in RULES or not applies(rid, row):
                ignored.append({"id": rid, "reason": "unknown rule id" if rid not in RULES else
                                "rule cannot apply to this row"})
                continue
            fields = RULES[rid][1]
            if rid == "unsupported_evidence":
                claims = [c for c in it.get("claims") or [] if c in CRITICAL_CLAIMS] or list(CRITICAL_CLAIMS)
                fields = claim_fields(claims)
            quotes = [q for q in it.get("quotes") or [] if isinstance(q, dict)][:3]
            found.append(Finding(rid, "llm", str(it.get("reason") or "")[:500], fields, quotes))
        return found, ignored, None

    # targeted fill -------------------------------------------------------
    def _evaluate_fill(self, app: dict, base: dict, res: LLMResult, pages: list[Page], fields: list[str],
                       claims: list[str]) -> tuple[dict | None, dict, dict, list[str]]:
        try:
            out = extract_json(res.text)
        except ValueError as e:
            if res.truncated:
                return None, {}, {}, [prompts.TRUNCATED_ERROR.format(stop=res.stop_reason)]
            return None, {}, {}, [prompts.UNPARSEABLE_ERROR.format(detail=e)]
        llm_row = out.get("row") if isinstance(out.get("row"), dict) else out
        if not any(k in llm_row for k in (*fields, "evidence")):
            return None, {}, {}, [prompts.NO_ROW_ERROR.format(keys=", ".join(list(out)[:10]))]
        row = merge_fill(base, llm_row, fields, claims)
        raw = out.get("snippets") if isinstance(out.get("snippets"), dict) else {}
        snippets = {c: v for c, v in raw.items() if c in claims}
        # Only the replaced claims are checked against pages fetched in this review; frozen claims keep the
        # evidence that was checked against the pages of the run that produced them.
        sub = {"evidence": {c: row["evidence"].get(c) or [] for c in claims}}
        sub.update({f: row.get(f) for f in ("docs_url", "mcp_url") if f in fields})
        sub, checks = check_evidence(sub, snippets, pages)
        row["evidence"].update(sub["evidence"])
        checks["warnings"] = [w for w in checks["warnings"] if w.rsplit(" ", 1)[-1] in claims]
        return row, snippets, checks, validate_row(row, {k: app[k] for k in IDENTITY_KEYS})

    def _reresearch(self, app: dict, base: dict, findings: list[Finding], fields: list[str], claims: list[str],
                    stats: Stats, attempts: list[dict]) -> tuple[dict | None, dict, dict, list[str], bool, list, list]:
        slug = app["slug"]
        seen: set[str] = set()
        searches = [self._search(q, stats) for q in review_queries(app, claims, rules_fired(findings))]
        # Re-read the pages the row already cites for these claims (and docs_url), then new official pages.
        ev = base.get("evidence") or {}
        cited = [base.get("docs_url")] + [i.get("url") for c in claims for i in ev.get(c) or [] if isinstance(i, dict)]
        cited = list({normalize_url(u): u for u in cited if isinstance(u, str) and u.startswith("http")}.values())
        cited = cited[:max(1, self.max_pages // 2)]
        urls = cited + pick_urls(app, searches, self.max_pages - len(cited), {normalize_url(u) for u in cited},
                                 include_start="access" in claims)
        pages = self._fetch_new(slug, urls, stats, seen)
        if "auth" in claims or "mcp" in claims:
            # MCP hits only when MCP is being rechecked ({"mcp_status": "frozen"} turns them off).
            mcp_view = None if "mcp" in claims else {"mcp_status": "frozen"}
            pages += self._fetch_new(slug, gap_fill_urls(app, searches, seen, mcp_view, want_auth="auth" in claims),
                                     stats, seen)
        if "mcp" in claims:
            probe = {"mcp_status": "none", "docs_url": base.get("docs_url")}
            new = self._fetch_new(slug, self.mcp_llms_txt_urls(app, pages, probe), stats, seen)
            new += self._fetch_new(slug, [u for p in new if p.ok for u in mcp_links(p)], stats, seen)
            pages += new

        messages = [{"role": "user", "content": prompts.review_fill_prompt(
            app, base, [f.to_dict() for f in findings], fields, claims, pages, self.per_page_chars)}]
        res = self._llm(messages, stats, attempts, "review_fill", slug)
        row, snippets, checks, errors = self._evaluate_fill(app, base, res, pages, fields, claims)
        retried = False
        if errors:
            retried = True
            messages += [{"role": "assistant", "content": res.text.strip() or "(empty response)"},
                         {"role": "user", "content": prompts.review_retry_prompt(errors, checks)}]
            res = self._llm(messages, stats, attempts, "review_retry", slug)
            row, snippets, checks, errors = self._evaluate_fill(app, base, res, pages, fields, claims)
        return row, snippets, checks, errors, retried, searches, pages

    def review(self, app: dict, row: dict, evidence: dict | None = None, cached: list[Page] | None = None
               ) -> ReviewOutcome:
        t0 = time.monotonic()
        slug = app["slug"]
        stats = Stats()
        token = current_app.set(slug)  # attributes Instant and Exa spend to this app
        attempts: list[dict] = []
        original = copy.deepcopy(row)
        base = {**copy.deepcopy(row), **{k: app[k] for k in IDENTITY_KEYS}}  # identity always from the CSV
        identity = {k: app[k] for k in IDENTITY_KEYS}
        searches: list[dict] = []
        pages: list[Page] = []
        snippets: dict = {}
        checks: dict = {}
        retried = False
        try:
            code = deterministic_findings(app, base, evidence) + validator_findings(base, identity)
            llm, ignored, reviewer_error = self._llm_findings(app, base, evidence, cached or [], stats, attempts)
            findings = merge_findings(code, llm)
            fields, claims = scope(findings)
            if not findings:
                final, errors, status = base, validate_row(base, identity), "unchanged"
            else:
                fixed, snippets, checks, errors, retried, searches, pages = self._reresearch(
                    app, base, findings, fields, claims, stats, attempts)
                if errors or fixed is None:
                    # Never invent support: keep the pass-1 values and say the review could not confirm a fix.
                    final = copy.deepcopy(base)
                    note = (f"Pass-2 review flagged {', '.join(rules_fired(findings))} but could not support a "
                            "corrected value from fetched official pages; pass-1 values kept.")
                    final["notes"] = f"{final.get('notes') or ''} {note}".strip()
                    checks["fill_errors"] = errors
                    snippets = {}
                    errors = validate_row(final, identity)
                    status = "failed" if errors else "kept"
                else:
                    final = fixed
                    status = "changed" if fixed != original else "confirmed"
        finally:
            current_app.reset(token)
            self._save_attempts(slug, attempts)

        final = {k: final.get(k) for k in ROW_ORDER}
        changed, before, after = _changes(original, final)
        prior = evidence or {}
        merged_snippets = {**(prior.get("snippets") or {}), **snippets}
        checks.update(retried=retried, validation_errors=errors)
        out_evidence = {
            "slug": slug,
            "searches": (prior.get("searches") or []) + searches,
            "pages": (prior.get("pages") or []) + [p.meta() for p in pages],
            "snippets": merged_snippets,
            "checks": checks,
            "review": {"rules_fired": rules_fired(findings), "findings": [f.to_dict() for f in findings],
                       "fields_in_scope": fields, "claims": claims, "review_searches": searches,
                       "review_pages": [p.meta() for p in pages]},
        }
        return ReviewOutcome(slug, final, errors, status, findings, ignored, reviewer_error, fields, claims,
                             bool(findings), retried, out_evidence, stats, round(time.monotonic() - t0, 2),
                             changed, before, after)


# ---------- run driver ----------

def review_run(items: list[tuple[dict, dict]], reviewer: Reviewer, from_dir: Path, run_dir: Path,
               concurrency: int = 3) -> dict:
    """Review (app, row) pairs from `from_dir` into `run_dir`, skipping slugs already in its rows.jsonl."""
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "evidence").mkdir(exist_ok=True)
    paths = {k: run_dir / f"{k}.jsonl" for k in ("rows", "failures", "log", "review")}
    done = _read_done(paths["rows"])
    todo = [(a, r) for a, r in items if a["slug"] not in done]
    lock = threading.Lock()
    summary = Counter(skipped=len(items) - len(todo))
    fired = Counter()
    totals = Counter()
    settings = reviewer.settings
    budget = reviewer.budget or InstantBudget(0)
    exa_budget = reviewer.exa_budget or ExaBudget(0)

    def append(path: Path, obj: dict) -> None:
        with path.open("a") as f:
            f.write(json.dumps(obj, ensure_ascii=False) + "\n")

    def one(app: dict, row: dict) -> None:
        t0 = time.monotonic()
        slug = app["slug"]
        try:
            evidence, cached = load_prior(from_dir, slug)
            out = reviewer.review(app, row, evidence, cached)
        except Exception as e:  # keep going; record the crash
            log.exception("review crashed for %s", slug)
            rec = {"slug": slug, "status": "crash", "error": f"{type(e).__name__}: {e}",
                   "wall_s": round(time.monotonic() - t0, 2), "at": now_iso()}
            with lock:
                append(paths["failures"], {"slug": slug, "errors": [f"crash: {rec['error']}"], "row": None,
                                           "at": now_iso()})
                append(paths["log"], rec)
                append(paths["review"], rec)
                summary["crashed"] += 1
            return
        s = out.stats
        spend = {**budget.app_stats(slug), **exa_budget.app_stats(slug)}
        cost = settings.est_cost(s.input_tokens, s.output_tokens)
        usage = {"search_calls": s.search_calls, "fetch_calls": s.fetch_calls, "llm_calls": s.llm_calls,
                 "input_tokens": s.input_tokens, "output_tokens": s.output_tokens, "est_cost_usd": cost}
        record = {
            "slug": slug, "status": out.status,
            "rules_fired": rules_fired(out.findings),
            "findings": [f.to_dict() for f in out.findings],
            "ignored_llm_rules": out.ignored_llm, "reviewer_error": out.reviewer_error,
            "fields_in_scope": out.fields_in_scope, "claims_rechecked": out.claims,
            "re_researched": out.re_researched, "retried": out.retried,
            "changed": bool(out.fields_changed), "fields_changed": out.fields_changed,
            "before": out.before, "after": out.after,
            "validation_errors": out.errors,
            **usage, "wall_s": out.wall_s, **spend,
            "llm_provider": reviewer.llm.provider, "llm_model": reviewer.llm.model, "at": now_iso(),
        }
        (run_dir / "evidence" / f"{slug}.json").write_text(json.dumps(out.evidence, indent=2, ensure_ascii=False))
        with lock:
            if out.errors:
                append(paths["failures"], {"slug": slug, "errors": out.errors, "row": out.row, "at": now_iso()})
            else:
                append(paths["rows"], out.row)
            append(paths["log"], {"slug": slug, "status": "failed" if out.errors else "ok",
                                  "review_status": out.status, **usage, "wall_s": out.wall_s,
                                  "llm_provider": reviewer.llm.provider, "llm_model": reviewer.llm.model,
                                  "providers": dict(s.providers), **spend, "at": now_iso()})
            append(paths["review"], record)
            summary[out.status] += 1
            summary["re_researched"] += out.re_researched
            fired.update(record["rules_fired"])
            totals.update(input_tokens=s.input_tokens, output_tokens=s.output_tokens)
        log.info("%s: %s, rules=%s, changed=%s in %.1fs", slug, out.status, record["rules_fired"],
                 out.fields_changed, out.wall_s)

    with ThreadPoolExecutor(max_workers=max(1, concurrency)) as pool:
        for fut in as_completed([pool.submit(one, a, r) for a, r in todo]):
            fut.result()

    return {
        "run_dir": str(run_dir),
        "from_dir": str(from_dir),
        "requested": len(items),
        "skipped_done": summary["skipped"],
        **{k: summary[k] for k in ("unchanged", "confirmed", "changed", "kept", "failed", "crashed",
                                   "re_researched")},
        "rules_fired": dict(fired.most_common()),
        "input_tokens": totals["input_tokens"],
        "output_tokens": totals["output_tokens"],
        "est_cost_usd": settings.est_cost(totals["input_tokens"], totals["output_tokens"]),
        **budget.snapshot(),
        **exa_budget.snapshot(),
    }
