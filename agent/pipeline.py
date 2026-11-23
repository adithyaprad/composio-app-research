"""`python -m agent all`: run -> review -> browse into one folder, then the final export and needs_human flags.

<out-dir>/<run-id>/
  pass1/ pass2/ pass3/      each stage's usual run folder (re-running the same --run-id resumes every stage)
  rows.jsonl                copy of pass3/rows.jsonl
  final.json, final.csv     export_final
  needs_human.jsonl/.json   write_needs_human over pass3 (browse.jsonl and evidence/ live there)
  summary.json              per-stage summaries, wall_s, token / cost totals, output paths
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
from pathlib import Path

from agent.human import export_final, read_jsonl, write_needs_human
from agent.research import validate_apps_csv


def _stage_args(args: argparse.Namespace, run_id: str, out_dir: Path, **kw) -> argparse.Namespace:
    base = {"input": args.input, "apps": args.apps, "limit": args.limit, "run_id": run_id, "out_dir": out_dir,
            "dry_run": args.dry_run, "verbose": args.verbose}
    return argparse.Namespace(**{**base, **kw})


def run_all(args: argparse.Namespace) -> dict:
    from agent.cli import browse_stage, review_stage, run_stage, select_apps

    apps = validate_apps_csv(args.input)  # fail fast, before any stage starts
    selected = select_apps(apps, args.apps, args.limit)
    run_dir = args.out_dir / args.run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    stages: dict[str, dict] = {}
    t_all = time.monotonic()

    def stage(name: str, fn, ns: argparse.Namespace) -> None:
        print(f"== {name} -> {run_dir / ns.run_id}", file=sys.stderr)
        t0 = time.monotonic()
        stages[name] = {**fn(ns), "wall_s": round(time.monotonic() - t0, 2)}

    stage("run", run_stage, _stage_args(args, "pass1", run_dir, concurrency=args.concurrency,
                                        max_pages=args.max_pages, no_follow_up=args.no_follow_up))
    # Later stages read every row the previous one wrote (already narrowed by --apps / --limit).
    stage("review", review_stage, _stage_args(args, "pass2", run_dir, apps=None, limit=None, from_run="pass1",
                                              from_dir=None, concurrency=args.concurrency,
                                              max_pages=_review_max_pages()))
    stage("browse", browse_stage, _stage_args(args, "pass3", run_dir, apps=None, limit=None, from_run="pass2",
                                              from_dir=None, concurrency=args.browse_concurrency))

    final_rows = run_dir / "rows.jsonl"
    shutil.copyfile(run_dir / "pass3" / "rows.jsonl", final_rows)
    rows = read_jsonl(final_rows)
    final_json, final_csv = export_final(rows, run_dir)
    nh = write_needs_human(run_dir / "pass3")
    for name in ("needs_human.jsonl", "needs_human.json"):
        shutil.copyfile(run_dir / "pass3" / name, run_dir / name)

    failed = {k: s.get("failed_validation", s.get("failed", 0)) + s.get("crashed", 0) for k, s in stages.items()}
    summary = {
        "run_dir": str(run_dir),
        "input": str(args.input),
        "dry_run": bool(args.dry_run),
        "apps_requested": len(selected),
        "rows_final": len(rows),
        "ok": not any(failed.values()) and len(rows) == len(selected),
        "failed_or_crashed": failed,
        "needs_human_count": nh["needs_human_count"],
        "needs_human_by_reason": nh["by_reason"],
        "stages": stages,
        "totals": {
            "input_tokens": sum(s.get("input_tokens", 0) for s in stages.values()),
            "output_tokens": sum(s.get("output_tokens", 0) for s in stages.values()),
            "est_cost_usd": _sum_or_none(s.get("est_cost_usd") for s in stages.values()),
            "exa_cost_usd": _total(stages, "exa_cost_usd"),
            "instant_est_cost_usd": _total(stages, "instant_est_cost_usd"),
            "wall_s": round(time.monotonic() - t_all, 2),
        },
        "outputs": {k: str(v) for k, v in {
            "rows": final_rows, "final_json": final_json, "final_csv": final_csv,
            "needs_human_jsonl": run_dir / "needs_human.jsonl", "needs_human_json": run_dir / "needs_human.json",
            "summary": run_dir / "summary.json"}.items()},
    }
    (run_dir / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    return summary


def _review_max_pages() -> int:
    from agent.review import REVIEW_MAX_PAGES

    return REVIEW_MAX_PAGES


def _total(stages: dict[str, dict], key: str) -> float:
    return round(sum((s.get(key) or 0.0 for s in stages.values()), 0.0), 4)


def _sum_or_none(values) -> float | None:
    vals = [v for v in values if v is not None]
    return round(sum(vals), 4) if vals else None
