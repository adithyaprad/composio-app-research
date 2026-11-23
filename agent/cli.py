"""CLI: python -m agent run [--apps a,b] [--limit N] [--run-id ID] [--concurrency 3] [--max-pages 8]
       python -m agent review --from-run ID [--run-id ID2] [--apps a,b] [--limit N] [--concurrency 3]"""

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
    r = sub.add_parser("run", help="research apps from data/apps.csv into data/runs/<run_id>/")
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
    v.add_argument("--apps", help="comma-separated slugs (default: every row in the input run)")
    v.add_argument("--limit", type=int, help="review at most N rows (after --apps filtering)")
    v.add_argument("--run-id", help="output folder name; reuse it to resume (default: review-<UTC timestamp>)")
    v.add_argument("--concurrency", type=int, default=3, help="rows reviewed in parallel (default 3)")
    v.add_argument("--max-pages", type=int, default=6,
                   help="pages fetched per re-researched row before gap-fill (default 6)")
    v.add_argument("--dry-run", action="store_true", help="offline: fake LLM + fake search/fetch, no keys needed")
    v.add_argument("--out-dir", type=Path, default=RUNS_DIR, help="parent folder for runs (default data/runs)")
    v.add_argument("-v", "--verbose", action="store_true")
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


def cmd_run(args: argparse.Namespace) -> int:
    from agent.research import Researcher, load_apps, load_coverage, run

    load_env()
    settings = Settings.from_env()
    apps = select_apps(load_apps(APPS_CSV), args.apps, args.limit)
    run_id = args.run_id or ("dry-run-" if args.dry_run else "") + datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    run_dir = args.out_dir / run_id

    from agent.tools import make_budget, make_exa_budget

    # One spend cap each for Instant and Exa, shared by search and fetch for the whole run.
    budget, exa_budget = make_budget(settings), make_exa_budget(settings)
    if args.dry_run:
        from agent.fakes import FakeFetch, FakeLLM, FakeSearch

        llm, search, fetch = FakeLLM(), FakeSearch(), FakeFetch()
    else:
        from agent.llm import get_llm
        from agent.tools import get_fetch, get_search, make_exa

        llm = get_llm(settings)
        exa = make_exa(settings, exa_budget)  # None without EXA_API_KEY; one URL text cache for the run
        search = get_search(settings, budget=budget, exa=exa)
        fetch = get_fetch(settings, budget=budget, exa=exa)

    researcher = Researcher(settings, llm, search, fetch, run_dir, max_pages=args.max_pages,
                            follow_up=not args.no_follow_up, coverage=load_coverage(COVERAGE_CSV), budget=budget,
                            exa_budget=exa_budget)
    print(f"run {run_id}: {len(apps)} app(s), llm={llm.provider}:{llm.model}, "
          f"search={settings.search_provider if not args.dry_run else 'fake'}, "
          f"fetch={settings.fetch_provider if not args.dry_run else 'fake'}, "
          f"exa_budget=${settings.exa_budget_usd:.2f}, "
          f"instant_budget=${settings.instant_budget_usd:.2f}", file=sys.stderr)
    summary = run(apps, researcher, run_dir, concurrency=args.concurrency)
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


def cmd_review(args: argparse.Namespace) -> int:
    from agent.research import load_apps
    from agent.review import Reviewer, load_rows, review_run

    load_env()
    settings = Settings.from_env()
    from_dir = args.from_dir if args.from_dir else args.out_dir / args.from_run
    if not (from_dir / "rows.jsonl").is_file():
        raise ConfigError(f"no rows.jsonl in {from_dir}")
    run_id = args.run_id or ("dry-run-review-" if args.dry_run else "review-") + datetime.now(UTC).strftime(
        "%Y%m%dT%H%M%SZ")
    run_dir = args.out_dir / run_id
    if run_dir.resolve() == from_dir.resolve():
        raise ConfigError("--run-id must differ from the input run (the review writes a new run folder)")

    apps = {a["slug"]: a for a in load_apps(APPS_CSV)}
    rows = load_rows(from_dir)
    unknown = sorted({r.get("slug") for r in rows} - set(apps), key=str)
    if unknown:
        print(f"skipping rows not in data/apps.csv: {', '.join(map(str, unknown))}", file=sys.stderr)
    rows = [r for r in rows if r.get("slug") in apps]
    if args.apps:
        wanted = [s.strip() for s in args.apps.split(",") if s.strip()]
        missing = [s for s in wanted if s not in {r["slug"] for r in rows}]
        if missing:
            raise ConfigError(f"slug(s) not in {from_dir / 'rows.jsonl'}: {', '.join(missing)}")
        rows = [r for r in rows if r["slug"] in wanted]
    rows = rows[:args.limit] if args.limit else rows

    llm, search, fetch, budget, exa_budget = _tools(settings, args.dry_run)
    reviewer = Reviewer(settings, llm, search, fetch, run_dir, max_pages=args.max_pages, budget=budget,
                        exa_budget=exa_budget)
    print(f"review {run_id}: {len(rows)} row(s) from {from_dir}, llm={llm.provider}:{llm.model}, "
          f"search={settings.search_provider if not args.dry_run else 'fake'}, "
          f"fetch={settings.fetch_provider if not args.dry_run else 'fake'}, "
          f"exa_budget=${settings.exa_budget_usd:.2f}, "
          f"instant_budget=${settings.instant_budget_usd:.2f}", file=sys.stderr)
    summary = review_run([(apps[r["slug"]], r) for r in rows], reviewer, from_dir, run_dir,
                         concurrency=args.concurrency)
    print(json.dumps(summary, indent=2))
    return 0 if not summary["failed"] and not summary["crashed"] else 1


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    for noisy in ("httpx", "httpcore", "primp", "trafilatura", "urllib3", "botocore"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    try:
        return cmd_review(args) if args.command == "review" else cmd_run(args)
    except ConfigError as e:
        print(f"config error: {e}", file=sys.stderr)
        return 2
