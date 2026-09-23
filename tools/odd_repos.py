#!/usr/bin/env python3
"""Replay Electric Capital's open-dev-data migrations to map protocols -> repos.

    python3 tools/odd_repos.py --clone          # fetch/refresh the taxonomy
    python3 tools/odd_repos.py --eco Uniswap    # what repos does one ecosystem hold?

open-dev-data is not TOML or JSON: `migrations/<timestamp>_mutations` files hold a small
DSL that is replayed in timestamp order to build the taxonomy. Keywords actually used:

    ecoadd <eco>                    create an ecosystem
    repadd <eco> <url> [#tags...]   add a repo to it
    ecocon <parent> <child>         connect a child ecosystem
    ecodis <parent> <child>         disconnect it
    ecomov <old> <new>              rename an ecosystem
    ecorem <eco>                    remove an ecosystem
    repmov <old-url> <new-url>      the repo moved
    reprem <eco> <url>              drop the repo from that ecosystem

Lines starting with `--` are comments. Names may be quoted, so shlex does the splitting.
Data is CC BY 4.0: credit Electric Capital in anything published from it.
"""

import argparse
import csv
import json
import shlex
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ODD = ROOT / "repos" / ".open-dev-data"
URL = "https://github.com/electric-capital/open-dev-data"


class Taxonomy:
    def __init__(self):
        self.repos = {}      # eco -> set(url)
        self.children = {}   # eco -> set(eco)
        self.tags = {}       # (eco, url) -> tuple(tags)

    def _eco(self, name):
        self.repos.setdefault(name, set())
        self.children.setdefault(name, set())

    def apply(self, op, args):
        try:
            if op == "ecoadd":
                self._eco(args[0])
            elif op == "repadd":
                eco, url = args[0], args[1]
                self._eco(eco)
                self.repos[eco].add(url)
                if len(args) > 2:
                    self.tags[(eco, url)] = tuple(args[2:])
            elif op == "ecocon":
                self._eco(args[0]); self._eco(args[1])
                self.children[args[0]].add(args[1])
            elif op == "ecodis":
                self.children.get(args[0], set()).discard(args[1])
            elif op == "ecomov":
                old, new = args[0], args[1]
                self._eco(new)
                self.repos[new] |= self.repos.pop(old, set())
                self.children[new] |= self.children.pop(old, set())
                for s in self.children.values():
                    if old in s:
                        s.discard(old); s.add(new)
            elif op == "ecorem":
                self.repos.pop(args[0], None); self.children.pop(args[0], None)
                for s in self.children.values():
                    s.discard(args[0])
            elif op == "repmov":
                old, new = args[0], args[1]
                for urls in self.repos.values():
                    if old in urls:
                        urls.discard(old); urls.add(new)
            elif op == "reprem":
                self.repos.get(args[0], set()).discard(args[1])
        except IndexError:
            pass   # malformed line: skip it rather than abort the replay

    def descendants(self, eco, seen=None):
        seen = set() if seen is None else seen
        if eco in seen:
            return seen
        seen.add(eco)
        for c in self.children.get(eco, ()):
            self.descendants(c, seen)
        return seen

    def repos_for(self, eco, include_children=True):
        ecos = self.descendants(eco) if include_children else {eco}
        out = {}
        for e in ecos:
            for u in self.repos.get(e, ()):
                out.setdefault(u, []).append(e)
        return out


def load(root=ODD):
    t = Taxonomy()
    files = sorted(p for p in (root / "migrations").rglob("*") if p.is_file())
    for f in files:
        for line in f.read_text(errors="ignore").splitlines():
            line = line.strip()
            if not line or line.startswith("--"):
                continue
            try:
                parts = shlex.split(line)
            except ValueError:
                parts = line.split()
            if len(parts) >= 2:
                t.apply(parts[0], parts[1:])
    return t, len(files)


def clone():
    ODD.parent.mkdir(parents=True, exist_ok=True)
    if (ODD / ".git").exists():
        subprocess.run(["git", "-C", str(ODD), "pull", "--quiet", "--depth", "1"], timeout=600)
    else:
        subprocess.run(["git", "clone", "--depth", "1", "--quiet", URL, str(ODD)], timeout=1800)
    sha = subprocess.run(["git", "-C", str(ODD), "rev-parse", "HEAD"],
                         capture_output=True, text=True).stdout.strip()
    return sha


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--clone", action="store_true")
    ap.add_argument("--eco", help="print the repos of one ecosystem")
    ap.add_argument("--find", help="list ecosystem names containing this text")
    ap.add_argument("--dump", help="write eco -> repo pairs for these ecosystems (comma separated) to a CSV")
    a = ap.parse_args()

    sha = clone() if a.clone else ""
    if not (ODD / "migrations").exists():
        sys.exit(f"{ODD} not found -- run with --clone first")
    t, n = load()
    print(f"replayed {n} migration files: {len(t.repos)} ecosystems, "
          f"{len(set().union(*t.repos.values()) if t.repos else set())} distinct repos"
          + (f", taxonomy @ {sha[:10]}" if sha else ""), file=sys.stderr)

    if a.find:
        q = a.find.lower()
        hits = sorted((e for e in t.repos if q in e.lower()), key=lambda e: -len(t.repos_for(e)))
        for e in hits[:30]:
            print(f"{len(t.repos_for(e)):5}  {e}")
    if a.eco:
        for u, ecos in sorted(t.repos_for(a.eco).items()):
            print(f"{u}\t{';'.join(ecos)}")
    if a.dump:
        rows = []
        for eco in a.dump.split(","):
            for u, ecos in sorted(t.repos_for(eco.strip()).items()):
                rows.append({"ecosystem": eco.strip(), "repo_url": u, "via": ";".join(ecos)})
        w = csv.DictWriter(sys.stdout, fieldnames=["ecosystem", "repo_url", "via"])
        w.writeheader(); w.writerows(rows)


if __name__ == "__main__":
    main()
