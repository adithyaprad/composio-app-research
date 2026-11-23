#!/usr/bin/env python3
"""Check the generated case-study page: every data-driven number and every link.

Numbers: every element with `data-src="<repo file>:<expression>"` is recomputed
from that file, independently of scripts/build_site.py, and compared with the
text shown (at the precision shown). `data-fmt` says how the value is displayed:
`int`, `pct` (a 0-1 ratio shown as a percent), `usd`, `min` (seconds shown as
minutes), `s` or `date`.

Expressions:
    a.b.c                         JSON value at a dotted path
    count() / count(<filter>)     records in a .jsonl / .csv file (or a JSON list)
    sum(<field>)                  sum of a field over records, or of a JSON object's values
    distinct(<list field>)        distinct values across a list-valued field
    add(<path>,<path>)            sum of two JSON values
    count_pages([<rec filter>;]<page filter>)   pages[] entries across browse.jsonl records
    count_values(<path>=<value>)  values of the JSON object at <path> equal to <value>
    len(<path>)                   length of the JSON list / object at <path> (list indices are numbers)
    max_per(<field>)              largest group size when records are grouped by a field
    regressions(<score file>)     apps whole-row correct here but not in the other score file
    apps_with_critical_miss()     apps in a score file with at least one critical miss
    changed(<rows file>;<f1,f2>;exclude_scored)   rows whose fields differ, outside gold and holdout
    corrected_apps()              apps whose scored label changed in GOLD_AUDIT.md
    as_of()                       latest scored_at / spot-check date (no file part)

Filters: clauses joined by `&`. `f=a|b` (any of), `f!=a`, `f@=a` (list contains),
`f==a,b` (list equals, any order). Dotted fields reach into nested objects.

Links: `#id` anchors must exist on the page, relative links must exist in site/,
and external links are listed (`--online` also requests each one).

    .venv/bin/python scripts/check_site.py [--site site] [--online]
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import urllib.error
import urllib.request
from collections import Counter
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
CRITICAL_UNITS = ("auth", "access", "api", "mcp_status", "verdict", "blocker", "evidence")
SCORED_LABELS = {"auth_methods", "primary_auth", "access", "api_types", "has_public_docs", "mcp_status", "verdict",
                 "blocker"}
NUM = re.compile(r"-?\d[\d,]*(?:\.\d+)?")
VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"}


# ---------------------------------------------------------------- html parsing


# Page text that may contain digits without a data-src: labels such as "Pass 2", product words such as "OAuth2",
# and the arrow-separated pass sequence. Everything else with a digit must be sourced or marked data-text.
ALLOWED_DIGITS = re.compile(r"\b(?:[Pp]ass[ -]?[123]|P[123]|OAuth ?2(?:\.0)?|pass1\.\.3)\b")
NO_SCAN = {"script", "style", "code", "pre", "head", "title"}


class PageParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.ids: set[str] = set()
        self.links: list[str] = []
        self.resources: list[tuple[str, str]] = []
        self.numbers: list[dict] = []
        self.unsourced: list[str] = []
        self._open: list[dict | None] = []
        self._skip: list[bool] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = {k: v or "" for k, v in attrs}
        if "id" in a:
            self.ids.add(a["id"])
        if tag == "a" and "href" in a:
            self.links.append(a["href"])
        if tag in ("script", "img", "iframe", "source") and a.get("src"):
            self.resources.append((tag, a["src"]))
        if tag == "link" and a.get("href") and a.get("rel") in ("stylesheet", "preload", "icon", "modulepreload"):
            self.resources.append((tag, a["href"]))
        if tag in VOID:
            return
        rec = {"src": a["data-src"], "fmt": a.get("data-fmt", "int"), "text": ""} if "data-src" in a else None
        self._open.append(rec)
        self._skip.append(tag in NO_SCAN or "data-src" in a or "data-text" in a)
        if rec is not None:
            self.numbers.append(rec)

    def handle_endtag(self, tag: str) -> None:
        if tag not in VOID and self._open:
            self._open.pop()
            self._skip.pop()

    def handle_data(self, data: str) -> None:
        for rec in self._open:
            if rec is not None:
                rec["text"] += data
        if not any(self._skip) and re.search(r"\d", ALLOWED_DIGITS.sub("", data)):
            self.unsourced.append(" ".join(data.split()))


# ---------------------------------------------------------------- sources


def load(rel: str) -> Any:
    path = ROOT / rel
    if rel.endswith(".jsonl"):
        return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]
    if rel.endswith(".json"):
        return json.loads(path.read_text(encoding="utf-8"))
    if rel.endswith(".csv"):
        with path.open(newline="", encoding="utf-8") as fh:
            return list(csv.DictReader(fh))
    return path.read_text(encoding="utf-8")


def dig(obj: Any, path: str) -> Any:
    for part in path.split("."):
        obj = obj[int(part)] if isinstance(obj, list) else obj[part]
    return obj


def as_text(value: Any) -> str:
    if value is True:
        return "true"
    if value is False:
        return "false"
    if value is None:
        return "null"
    return str(value)


def matches(record: dict, filt: str) -> bool:
    if not filt:
        return True
    for clause in filt.split("&"):
        for op in ("==", "!=", "@=", "="):
            if op in clause:
                field, want = clause.split(op, 1)
                break
        else:
            raise ValueError(f"bad filter clause {clause!r}")
        try:
            value = dig(record, field)
        except (KeyError, TypeError):
            value = None
        if op == "==" and sorted(as_text(v) for v in value or []) != sorted(want.split(",")):
            return False
        if op == "!=" and as_text(value) == want:
            return False
        if op == "@=" and want not in [as_text(v) for v in value or []]:
            return False
        if op == "=" and as_text(value) not in want.split("|"):
            return False
    return True


def score_apps_correct(score: dict) -> set[str]:
    return {a["slug"] for a in score["apps"] if a.get("row_correct")}


def audit_corrections(text: str) -> int:
    section = text.split("## Fields changed", 1)[-1].split("\n## ", 1)[0]
    apps = set()
    for line in section.splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) > 1 and cells[0] not in ("App", "") and not set(cells[0]) <= {"-"}:
            if SCORED_LABELS & set(re.findall(r"`([^`]+)`", cells[1])):
                apps.add(cells[0])
    return len(apps)


def as_of() -> str:
    dates = []
    for f in sorted((ROOT / "data/scores").glob("*pass[123].json")):
        dates.append(json.loads(f.read_text())["scored_at"][:10])
    dates.append(str(load("data/runs/pass3/spot_check_10.json")["checked_at"])[:10])
    return max(dates)


def evaluate(key: str) -> Any:
    if key == "as_of()":
        return as_of()
    rel, expr = key.split(":", 1)
    data = load(rel)
    m = re.fullmatch(r"([a-z_]+)\((.*)\)", expr)
    if not m:
        return dig(data, expr)
    op, arg = m.groups()
    if op == "count":
        return sum(1 for r in data if matches(r, arg))
    if op == "sum":
        field, _, filt = arg.partition("|")
        if isinstance(data, dict):
            return sum(dig(data, field).values())
        return sum(dig(r, field) or 0 for r in data if matches(r, filt))
    if op == "distinct":
        return len({v for r in data for v in r.get(arg) or []})
    if op == "add":
        return sum(dig(data, p) for p in arg.split(","))
    if op == "count_pages":
        rec_filter, page_filter = arg.split(";") if ";" in arg else ("", arg)
        return sum(1 for r in data if matches(r, rec_filter) for p in r.get("pages") or [] if matches(p, page_filter))
    if op == "count_values":
        path, want = arg.split("=", 1)
        return sum(1 for v in dig(data, path).values() if as_text(v) == want)
    if op == "len":
        return len(dig(data, arg))
    if op == "max_per":
        return max(Counter(r[arg] for r in data).values())
    if op == "regressions":
        return len(score_apps_correct(data) - score_apps_correct(load(arg)))
    if op == "apps_with_critical_miss":
        return sum(1 for a in data["apps"]
                   if any(not (a.get("units") or {}).get(u, {}).get("match", True) for u in CRITICAL_UNITS))
    if op == "changed":
        other, fields, flag = arg.split(";")
        scored = set()
        if flag == "exclude_scored":
            scored = {r["slug"] for r in load("schema/gold_sample.jsonl") + load("schema/holdout_sample.jsonl")}
        before = {r["slug"]: r for r in data}
        return sum(1 for r in load(other) if r["slug"] in before and r["slug"] not in scored
                   and any(before[r["slug"]].get(f) != r.get(f) for f in fields.split(",")))
    if op == "corrected_apps":
        return audit_corrections(data)
    raise ValueError(f"unknown expression {expr!r}")


def compare(rec: dict) -> str | None:
    """None if the shown text matches the recomputed value, else a message."""
    try:
        value = evaluate(rec["src"])
    except Exception as exc:  # noqa: BLE001 - every failure is reported, not raised
        return f"cannot evaluate: {exc!r}"
    text = rec["text"].strip()
    if rec["fmt"] == "date":
        return None if text == str(value) else f"shown {text!r}, data {value!r}"
    found = NUM.search(text)
    if not found:
        return f"no number in {text!r}"
    shown = float(found.group(0).replace(",", ""))
    dp = len(found.group(0).split(".")[1]) if "." in found.group(0) else 0
    scale = {"pct": 100.0, "min": 1 / 60}.get(rec["fmt"], 1.0)
    expected = float(value) * scale
    if abs(expected - shown) > 0.5 * 10 ** -dp + 1e-9:
        return f"shown {text!r}, data {expected:.4g}"
    return None


# ---------------------------------------------------------------- links


def check_links(page: PageParser, site: Path, online: bool) -> dict[str, Any]:
    out = {"anchors_ok": 0, "anchors_bad": [], "local_ok": 0, "local_bad": [], "external": [], "external_bad": []}
    for href in page.links:
        if href.startswith("#"):
            target = href[1:]
            if target in page.ids:
                out["anchors_ok"] += 1
            else:
                out["anchors_bad"].append(href)
        elif re.match(r"^[a-z]+:", href):
            out["external"].append(href)
        else:
            rel = href.split("#", 1)[0].split("?", 1)[0]
            if (site / rel).is_file():
                out["local_ok"] += 1
            else:
                out["local_bad"].append(href)
    if online:
        for url in sorted(set(out["external"])):
            if not url.startswith(("http://", "https://")):
                continue
            req = urllib.request.Request(url, method="GET", headers={"User-Agent": "check_site/1.0"})
            try:
                with urllib.request.urlopen(req, timeout=15) as resp:  # noqa: S310 - links come from our own page
                    status = resp.status
            except urllib.error.HTTPError as exc:
                status = exc.code
            except Exception as exc:  # noqa: BLE001
                status = repr(exc)
            if not isinstance(status, int) or status >= 400:
                out["external_bad"].append((url, status))
    return out


def run(site: Path, online: bool = False, verbose: bool = True) -> dict[str, Any]:
    page = PageParser()
    page.feed((site / "index.html").read_text(encoding="utf-8"))
    mismatches = []
    for rec in page.numbers:
        problem = compare(rec)
        if problem:
            mismatches.append((rec["src"], problem))
    links = check_links(page, site, online)
    remote = [(t, s) for t, s in page.resources if re.match(r"^(https?:)?//", s)]
    report = {"numbers": len(page.numbers), "mismatches": mismatches, "remote_resources": remote,
              "unsourced": page.unsourced, **links}
    if verbose:
        for src, problem in mismatches:
            print(f"MISMATCH {src}: {problem}")
        for text in page.unsourced:
            print(f"UNSOURCED DIGITS {text[:120]!r}")
        for href in links["anchors_bad"]:
            print(f"BAD ANCHOR {href}")
        for href in links["local_bad"]:
            print(f"MISSING FILE {href}")
        for tag, url in remote:
            print(f"REMOTE RESOURCE <{tag}> {url}")
        for url, status in links["external_bad"]:
            print(f"EXTERNAL {status} {url}")
        bad_a, bad_l = len(links["anchors_bad"]), len(links["local_bad"])
        print(f"numbers checked: {len(page.numbers)}, mismatches: {len(mismatches)}, "
              f"unsourced digits: {len(page.unsourced)}; "
              f"anchors: {links['anchors_ok']}/{bad_a}; local files: {links['local_ok']}/{bad_l}; "
              f"external links: {len(links['external'])} ({len(set(links['external']))} unique"
              f"{', ' + str(len(links['external_bad'])) + ' failing' if online else ', not requested'})")
    return report


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--site", type=Path, default=ROOT / "site", help="site folder (default site/)")
    ap.add_argument("--online", action="store_true", help="also request every external link")
    args = ap.parse_args(argv)
    report = run(args.site, args.online)
    bad = (report["mismatches"] or report["unsourced"] or report["anchors_bad"] or report["local_bad"]
           or report["remote_resources"])
    return 1 if bad or not report["numbers"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
