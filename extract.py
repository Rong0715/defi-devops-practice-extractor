#!/usr/bin/env python3
"""
DeFi DevOps practice extractor
------------------------------
Reads data/protocols.csv + data/repos.csv, shallow-clones each protocol's repos,
runs every variable in probes.py, merges per-repo results into one value per
protocol, and writes out/. Every cell carries the evidence that produced it.

    python3 extract.py                    # all protocols
    python3 extract.py aave-v3 morpho-blue
    python3 extract.py --resume           # skip protocols already in out/
    python3 extract.py --no-clone         # only use what is already in repos/
    python3 extract.py --jobs 8           # parallel clones (network-bound)

Each protocol's JSON is written as soon as it is done, so a long run that dies
part-way keeps its work; --resume picks it up. The combined tables (all.json,
matrix.csv, matrix_long.csv) and out/run.json are rebuilt at the end from every
record, including ones loaded from a previous run.
"""

import argparse
import csv
import datetime
import json
import re
import subprocess
import sys
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from aggregate import run_protocol
from outputs import build_record, write_record, write_run_report, write_tables
from repo import Repo

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
REPO_DIR = ROOT / "repos"
OUT_DIR = ROOT / "out"


def load_csv(path):
    """CSV with '#' comment lines allowed."""
    with open(path, newline="") as fh:
        lines = [l for l in fh if l.strip() and not l.lstrip().startswith("#")]
    return list(csv.DictReader(lines))


def git(path, *args):
    try:
        return subprocess.run(["git", "-C", str(path), *args], capture_output=True,
                              text=True, timeout=60).stdout.strip()
    except Exception:
        return ""


def clone_dir(row):
    """Where a repo URL is checked out. Keyed by URL, so protocols that share a
    repo (a monorepo behind several subpaths) clone it once."""
    legacy = REPO_DIR / row["repo_id"]
    if (legacy / ".git").exists():
        return legacy
    slug = re.sub(r"^https?://[^/]+/|\.git$", "", row["url"].rstrip("/"))
    return REPO_DIR / re.sub(r"[^\w.-]+", "__", slug)


def clone(url, dest, retries=1):
    if (dest / ".git").exists():
        return True, ""
    for attempt in range(retries + 1):
        p = subprocess.run(["git", "clone", "--depth", "1", "--quiet", url, str(dest)],
                           capture_output=True, text=True, timeout=1800)
        if p.returncode == 0:
            return True, ""
        err = (p.stderr or "").strip()[:200]
        if attempt == retries:
            return False, err
    return False, "unreachable"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("protocols", nargs="*")
    ap.add_argument("--no-clone", action="store_true")
    ap.add_argument("--resume", action="store_true",
                    help="skip protocols that already have out/<id>.json")
    ap.add_argument("--jobs", type=int, default=6, help="parallel clones (default 6)")
    args = ap.parse_args()

    protocols = OrderedDict((p["protocol_id"], p) for p in load_csv(DATA_DIR / "protocols.csv"))
    repo_rows = {}
    for r in load_csv(DATA_DIR / "repos.csv"):
        if (r.get("include") or "1").strip() != "0":
            repo_rows.setdefault(r["protocol_id"], []).append(r)
    wanted = args.protocols or list(protocols)
    unknown = [w for w in wanted if w not in protocols]
    if unknown:
        sys.exit(f"unknown protocol(s): {', '.join(unknown)}")

    REPO_DIR.mkdir(exist_ok=True)
    OUT_DIR.mkdir(exist_ok=True)
    now = datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0).isoformat()

    done = set()
    if args.resume:
        done = {p for p in wanted if (OUT_DIR / f"{p}.json").exists()}
        if done:
            print(f"[resume] {len(done)} protocol(s) already in out/, skipping")
    todo = [p for p in wanted if p not in done]

    # phase 1: clone everything up front, in parallel (network-bound, one dir per URL)
    clone_failures = {}
    if not args.no_clone:
        targets = {}
        for pid in todo:
            for row in repo_rows.get(pid, []):
                targets.setdefault(row["url"], clone_dir(row))
        pending = {u: d for u, d in targets.items() if not (d / ".git").exists()}
        if pending:
            print(f"[clone] {len(pending)} repo(s), {args.jobs} at a time")
            with ThreadPoolExecutor(max_workers=max(1, args.jobs)) as ex:
                for url, (ok, err) in zip(pending, ex.map(
                        lambda kv: clone(*kv), pending.items())):
                    if ok:
                        print(f"  [ok]   {url}")
                    else:
                        clone_failures[url] = err
                        print(f"  [FAIL] {url}: {err}")

    # phase 2: index, probe, and write each protocol as it finishes
    records, skipped = [], []
    for pid in done:
        try:
            records.append(json.loads((OUT_DIR / f"{pid}.json").read_text()))
        except Exception as e:
            print(f"  !! could not reload {pid}: {e}")
    for pid in todo:
        proto = dict(protocols[pid])
        proto["gap_tags"] = {t.strip() for t in proto.get("scope_gaps", "").split(";") if t.strip()}
        print(f"[{pid}]")
        repos, metas = [], []
        for row in repo_rows.get(pid, []):
            dest = clone_dir(row)
            ok = (dest / ".git").exists()
            meta = {"id": row["repo_id"], "url": row["url"], "role": row["role"],
                    "subpath": row.get("subpath", ""), "clone": "ok" if ok else "fail"}
            if ok:
                meta.update(commit=git(dest, "rev-parse", "HEAD"),
                            branch=git(dest, "rev-parse", "--abbrev-ref", "HEAD"),
                            commit_date=git(dest, "log", "-1", "--format=%cI"))
                repo = Repo(row["repo_id"], row["url"], dest, row["role"], row.get("subpath", ""))
                meta["file_count"] = len(repo.scoped)
                meta["first_party_submodules"] = repo.first_party_submodules
                repos.append(repo)
                print(f"  [scan]  {row['repo_id']} ({row['role']}, {len(repo.scoped)} files)")
            metas.append(meta)
        if not repo_rows.get(pid):
            skipped.append({"protocol": pid, "reason": "no repos listed in data/repos.csv"})
        elif not any(r.role == "core" for r in repos):
            skipped.append({"protocol": pid, "reason": "core repo unavailable (clone failed?)"})
        if skipped and skipped[-1]["protocol"] == pid:
            print(f"  !! skipped: {skipped[-1]['reason']}")
            continue
        rec = build_record(proto, metas, run_protocol(proto, repos), now)
        write_record(rec, OUT_DIR)
        records.append(rec)

    # phase 3: combined tables + run report
    order = {p: i for i, p in enumerate(wanted)}
    records.sort(key=lambda r: order.get(r["protocol"]["protocol_id"], 1e9))
    write_tables(records, OUT_DIR)
    rep = write_run_report(OUT_DIR, now, records, skipped, clone_failures)
    c = rep["status_counts"]
    print(f"\nDone: {len(records)} protocols -> {OUT_DIR}/")
    print(f"  cells: {c['ok']} ok, {c['na']} n/a, {c['unknown']} unknown, {c['error']} error")
    if skipped:
        print(f"  skipped: {len(skipped)} (see out/run.json)")


if __name__ == "__main__":
    main()
