"""CLI: python -m agent run [--input CSV] [--apps a,b] [--limit N] [--run-id ID] [--concurrency 3] [--max-pages 8]
       python -m agent review --from-run ID [--input CSV] [--run-id ID2] [--apps a,b] [--limit N] [--concurrency 3]
       python -m agent browse --from-run ID [--input CSV] [--run-id ID3] [--apps a,b] [--limit N] [--concurrency 1]
       python -m agent all [--input CSV] --run-id ID [...]   (run -> review -> browse -> final + needs_human)"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import UTC, datetime
from pathlib import Path

from agent.config import APPS_CSV, COVERAGE_CSV, RUNS_DIR, ConfigError, Settings, load_env


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m agent", description="Research apps into schema-valid rows.")
    sub = p.add_subparsers(dest="command", required=True)
    r = sub.add_parser("run", help="research apps from data/apps.csv (or --input) into data/runs/<run_id>/")
    r.add_argument("--input", type=Path, help="app list CSV (default data/apps.csv); a normalized copy is "
                                              "saved as <run>/input.csv")
    r.add_argument("--apps", help="comma-separated slugs (default: all apps)")
    r.add_argument("--limit", type=int, help="research at most N apps (after --apps filtering)")
    r.add_argument("--run-id", help="output folder name; reuse it to resume (default: UTC timestamp)")
    r.add_argument("--concurrency", type=int, default=3, help="apps researched in parallel (default 3)")
    r.add_argument("--max-pages", type=int, default=8, help="pages fetched per app before follow-up (default 8)")
    r.add_argument("--no-follow-up", action="store_true", help="skip the optional LLM follow-up fetch round")
    r.add_argument("--dry-run", action="store_true", help="offline: fake LLM + fake search/fetch, no keys needed")
    r.add_argument("--out-dir", type=Path, default=RUNS_DIR, help="parent folder for runs (default data/runs)")
    r.add_argument("-v", "--verbose", action="store_true")

    v = sub.add_parser("review", help="pass 2: recheck rows of an earlier run against the rule checklist and "
                                      "re-research only the fields that fail")
    src = v.add_mutually_exclusive_group(required=True)
    src.add_argument("--from-run", help="run id under --out-dir whose rows.jsonl (and evidence/) is reviewed")
    src.add_argument("--from-dir", type=Path, help="path to a run folder with rows.jsonl (and evidence/)")
    v.add_argument("--input", type=Path, help="app list CSV (default: the input run's input.csv, else "
                                              "data/apps.csv)")
    v.add_argument("--apps", help="comma-separated slugs (default: every row in the input run)")
    v.add_argument("--limit", type=int, help="review at most N rows (after --apps filtering)")
    v.add_argument("--run-id", help="output folder name; reuse it to resume (default: review-<UTC timestamp>)")
    v.add_argument("--concurrency", type=int, default=3, help="rows reviewed in parallel (default 3)")
    v.add_argument("--max-pages", type=int, default=6,
                   help="pages fetched per re-researched row before gap-fill (default 6)")
    v.add_argument("--dry-run", action="store_true", help="offline: fake LLM + fake search/fetch, no keys needed")
    v.add_argument("--out-dir", type=Path, default=RUNS_DIR, help="parent folder for runs (default data/runs)")
    v.add_argument("-v", "--verbose", action="store_true")

    b = sub.add_parser("browse", help="pass 3: open the real pages of still-unsure rows in a browser (local "
                                      "Chromium, Browser Use Cloud only for blocked pages) and confirm or "
                                      "correct only the flagged fields")
    src = b.add_mutually_exclusive_group(required=True)
    src.add_argument("--from-run", help="run id under --out-dir whose rows.jsonl (and evidence/) is checked")
    src.add_argument("--from-dir", type=Path, help="path to a run folder with rows.jsonl (and evidence/)")
    b.add_argument("--input", type=Path, help="app list CSV (default: the input run's input.csv, else "
                                              "data/apps.csv)")
    b.add_argument("--apps", help="comma-separated slugs (default: every row in the input run)")
    b.add_argument("--limit", type=int, help="check at most N rows (after --apps filtering)")
    b.add_argument("--run-id", help="output folder name; reuse it to resume (default: browse-<UTC timestamp>)")
    b.add_argument("--concurrency", type=int, default=1, help="rows checked in parallel (default 1; browsers "
                                                                "are heavy)")
    b.add_argument("--dry-run", action="store_true", help="offline: fake LLM + fake browser, no Chrome or keys")
    b.add_argument("--out-dir", type=Path, default=RUNS_DIR, help="parent folder for runs (default data/runs)")
    b.add_argument("-v", "--verbose", action="store_true")

    a = sub.add_parser("all", help="end to end: run -> review -> browse into <out-dir>/<run-id>/pass1..3, then "
                                   "rows.jsonl, final.json, final.csv, needs_human.jsonl and summary.json")
    a.add_argument("--input", type=Path, default=APPS_CSV, help="app list CSV (default data/apps.csv)")
    a.add_argument("--run-id", required=True, help="output folder name; reuse it to resume every stage")
    a.add_argument("--apps", help="comma-separated slugs (default: all apps in --input)")
    a.add_argument("--limit", type=int, help="process at most N apps (after --apps filtering)")
    a.add_argument("--concurrency", type=int, default=3, help="apps in parallel for run and review (default 3)")
    a.add_argument("--browse-concurrency", type=int, default=1, help="rows in parallel for browse (default 1)")
    a.add_argument("--max-pages", type=int, default=8, help="pages fetched per app in run (default 8)")
    a.add_argument("--no-follow-up", action="store_true", help="skip the optional LLM follow-up fetch round")
    a.add_argument("--dry-run", action="store_true", help="offline: fake LLM, tools and browser, no keys")
    a.add_argument("--out-dir", type=Path, default=RUNS_DIR, help="parent folder for runs (default data/runs)")
    a.add_argument("-v", "--verbose", action="store_true")
    return p


def select_apps(apps: list[dict], slugs: str | None, limit: int | None) -> list[dict]:
    if slugs:
        wanted = [s.strip() for s in slugs.split(",") if s.strip()]
        known = {a["slug"] for a in apps}
        unknown = [s for s in wanted if s not in known]
        if unknown:
            raise ConfigError(f"unknown app slug(s): {', '.join(unknown)}")
        apps = [a for a in apps if a["slug"] in wanted]
    return apps[:limit] if limit else apps


def run_stage(args: argparse.Namespace) -> dict:
    """Pass 1. Returns the run summary (printing is left to the caller)."""
    from agent.research import Researcher, load_coverage, run, validate_apps_csv, write_apps_csv

    load_env()
    settings = Settings.from_env()
    all_apps = validate_apps_csv(args.input or APPS_CSV)
    apps = select_apps(all_apps, args.apps, args.limit)
    run_id = args.run_id or ("dry-run-" if args.dry_run else "") + datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    run_dir = args.out_dir / run_id
    write_apps_csv(all_apps, run_dir / "input.csv")  # review / browse of this run pick it up automatically

    llm, search, fetch, budget, exa_budget = _tools(settings, args.dry_run)
    researcher = Researcher(settings, llm, search, fetch, run_dir, max_pages=args.max_pages,
                            follow_up=not args.no_follow_up, coverage=load_coverage(COVERAGE_CSV), budget=budget,
                            exa_budget=exa_budget)
    print(f"run {run_id}: {len(apps)} app(s), llm={llm.provider}:{llm.model}, "
          f"search={settings.search_provider if not args.dry_run else 'fake'}, "
          f"fetch={settings.fetch_provider if not args.dry_run else 'fake'}, "
          f"exa_budget=${settings.exa_budget_usd:.2f}, "
          f"instant_budget=${settings.instant_budget_usd:.2f}", file=sys.stderr)
    return run(apps, researcher, run_dir, concurrency=args.concurrency)


def cmd_run(args: argparse.Namespace) -> int:
    summary = run_stage(args)
    print(json.dumps(summary, indent=2))
    return 0 if not summary["failed_validation"] and not summary["crashed"] else 1


def _tools(settings: Settings, dry_run: bool):
    """(llm, search, fetch, instant budget, exa budget), shared by run and review."""
    from agent.tools import make_budget, make_exa_budget

    # One spend cap each for Instant and Exa, shared by search and fetch for the whole run.
    budget, exa_budget = make_budget(settings), make_exa_budget(settings)
    if dry_run:
        from agent.fakes import FakeFetch, FakeLLM, FakeSearch

        return FakeLLM(), FakeSearch(), FakeFetch(), budget, exa_budget
    from agent.llm import get_llm
    from agent.tools import get_fetch, get_search, make_exa

    llm = get_llm(settings)
    exa = make_exa(settings, exa_budget)  # None without EXA_API_KEY; one URL text cache for the run
    return (llm, get_search(settings, budget=budget, exa=exa), get_fetch(settings, budget=budget, exa=exa),
            budget, exa_budget)


def input_csv_for(explicit: Path | None, from_dir: Path) -> Path:
    """--input if given, else the input run's input.csv, else data/apps.csv."""
    if explicit:
        return explicit
    saved = from_dir / "input.csv"
    return saved if saved.is_file() else APPS_CSV


def _input_rows(args: argparse.Namespace, prefix: str) -> tuple[Path, Path, str, dict, list[dict]]:
    """(from_dir, run_dir, run_id, apps by slug, selected rows) for review / browse."""
    from agent.research import validate_apps_csv, write_apps_csv
    from agent.review import load_rows

    from_dir = args.from_dir if args.from_dir else args.out_dir / args.from_run
    if not (from_dir / "rows.jsonl").is_file():
        raise ConfigError(f"no rows.jsonl in {from_dir}")
    run_id = args.run_id or (f"dry-run-{prefix}-" if args.dry_run else f"{prefix}-") + datetime.now(UTC).strftime(
        "%Y%m%dT%H%M%SZ")
    run_dir = args.out_dir / run_id
    if run_dir.resolve() == from_dir.resolve():
        raise ConfigError(f"--run-id must differ from the input run (the {prefix} writes a new run folder)")

    input_csv = input_csv_for(args.input, from_dir)
    all_apps = validate_apps_csv(input_csv)
    write_apps_csv(all_apps, run_dir / "input.csv")  # so the next pass finds the same app list
    apps = {a["slug"]: a for a in all_apps}
    rows = load_rows(from_dir)
    unknown = sorted({r.get("slug") for r in rows} - set(apps), key=str)
    if unknown:
        print(f"skipping rows not in {input_csv}: {', '.join(map(str, unknown))}", file=sys.stderr)
    rows = [r for r in rows if r.get("slug") in apps]
    if args.apps:
        wanted = [s.strip() for s in args.apps.split(",") if s.strip()]
        missing = [s for s in wanted if s not in {r["slug"] for r in rows}]
        if missing:
            raise ConfigError(f"slug(s) not in {from_dir / 'rows.jsonl'}: {', '.join(missing)}")
        rows = [r for r in rows if r["slug"] in wanted]
    rows = rows[:args.limit] if args.limit else rows
    return from_dir, run_dir, run_id, apps, rows


def review_stage(args: argparse.Namespace) -> dict:
    """Pass 2. Returns the review summary."""
    from agent.review import Reviewer, review_run

    load_env()
    settings = Settings.from_env()
    from_dir, run_dir, run_id, apps, rows = _input_rows(args, "review")
    llm, search, fetch, budget, exa_budget = _tools(settings, args.dry_run)
    reviewer = Reviewer(settings, llm, search, fetch, run_dir, max_pages=args.max_pages, budget=budget,
                        exa_budget=exa_budget)
    print(f"review {run_id}: {len(rows)} row(s) from {from_dir}, llm={llm.provider}:{llm.model}, "
          f"search={settings.search_provider if not args.dry_run else 'fake'}, "
          f"fetch={settings.fetch_provider if not args.dry_run else 'fake'}, "
          f"exa_budget=${settings.exa_budget_usd:.2f}, "
          f"instant_budget=${settings.instant_budget_usd:.2f}", file=sys.stderr)
    return review_run([(apps[r["slug"]], r) for r in rows], reviewer, from_dir, run_dir,
                      concurrency=args.concurrency)


def cmd_review(args: argparse.Namespace) -> int:
    summary = review_stage(args)
    print(json.dumps(summary, indent=2))
    return 0 if not summary["failed"] and not summary["crashed"] else 1


def browse_stage(args: argparse.Namespace) -> dict:
    """Pass 3. Returns the browse summary."""
    from agent.browse import BrowserPool, Verifier, browse_run, cloud_key_present

    load_env()
    settings = Settings.from_env()
    from_dir, run_dir, run_id, apps, rows = _input_rows(args, "browse")
    if args.dry_run:
        from agent.fakes import FakeBrowserDriver, FakeLLM

        llm = FakeLLM()

        def make_pool() -> BrowserPool:
            return BrowserPool(FakeBrowserDriver, has_cloud_key=lambda: False)
    else:
        from agent.llm import get_llm

        llm = get_llm(settings)
        make_pool = BrowserPool
    verifier = Verifier(settings, llm, run_dir, make_pool=make_pool)
    cloud = "off (dry run)" if args.dry_run else ("on for blocked pages" if cloud_key_present()
                                                  else "off (no BROWSER_USE_API_KEY)")
    print(f"browse {run_id}: {len(rows)} row(s) from {from_dir}, llm={llm.provider}:{llm.model}, "
          f"browser={'fake' if args.dry_run else 'local chromium'}, cloud fallback={cloud}", file=sys.stderr)
    return browse_run([(apps[r["slug"]], r) for r in rows], verifier, from_dir, run_dir,
                      concurrency=args.concurrency)


def cmd_browse(args: argparse.Namespace) -> int:
    summary = browse_stage(args)
    print(json.dumps(summary, indent=2))
    return 0 if not summary["failed"] and not summary["crashed"] else 1


def cmd_all(args: argparse.Namespace) -> int:
    from agent.pipeline import run_all

    summary = run_all(args)
    print(json.dumps(summary, indent=2))
    return 0 if summary["ok"] else 1


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    for noisy in ("httpx", "httpcore", "primp", "trafilatura", "urllib3", "botocore", "browser_use",
                  "browser_harness"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    try:
        return {"review": cmd_review, "browse": cmd_browse, "all": cmd_all}.get(args.command, cmd_run)(args)
    except ConfigError as e:
        print(f"config error: {e}", file=sys.stderr)
        return 2
