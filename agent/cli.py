"""CLI: python -m agent run [--apps a,b] [--limit N] [--run-id ID] [--concurrency 3] [--max-pages 8]"""

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

    from agent.tools import make_budget

    budget = make_budget(settings)  # one Instant spend cap shared by search and fetch for the whole run
    if args.dry_run:
        from agent.fakes import FakeFetch, FakeLLM, FakeSearch

        llm, search, fetch = FakeLLM(), FakeSearch(), FakeFetch()
    else:
        from agent.llm import get_llm
        from agent.tools import get_fetch, get_search

        llm = get_llm(settings)
        search, fetch = get_search(settings, budget=budget), get_fetch(settings, budget=budget)

    researcher = Researcher(settings, llm, search, fetch, run_dir, max_pages=args.max_pages,
                            follow_up=not args.no_follow_up, coverage=load_coverage(COVERAGE_CSV), budget=budget)
    print(f"run {run_id}: {len(apps)} app(s), llm={llm.provider}:{llm.model}, "
          f"search={settings.search_provider if not args.dry_run else 'fake'}, "
          f"fetch={settings.fetch_provider if not args.dry_run else 'fake'}, "
          f"instant_budget=${settings.instant_budget_usd:.2f}", file=sys.stderr)
    summary = run(apps, researcher, run_dir, concurrency=args.concurrency)
    print(json.dumps(summary, indent=2))
    return 0 if not summary["failed_validation"] and not summary["crashed"] else 1


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    for noisy in ("httpx", "httpcore", "primp", "trafilatura", "urllib3", "botocore"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    try:
        return cmd_run(args)
    except ConfigError as e:
        print(f"config error: {e}", file=sys.stderr)
        return 2
