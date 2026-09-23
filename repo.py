"""One shallow-cloned repo plus the two search primitives every probe is built on.

glob(regex)  -> matching relative paths
grep(regex)  -> [{"path", "line", "snippet"}] for the first match in each file

Monorepos: `subpath` limits code probes to one package. Repo-level infrastructure
(.github/, files at the repo root) always stays in scope. Regexes that look for
directories should therefore be written `(^|/)dir/`, never `^dir/`.
"""

import functools
import os
import re
from pathlib import Path

TEXT_EXT = {".sol", ".vy", ".toml", ".yml", ".yaml", ".json", ".md", ".txt",
            ".ts", ".js", ".mjs", ".cjs", ".py", ".sh", ".cfg", ".conf",
            ".spec", ".config"}
TEXT_NAMES = {"makefile", "gnumakefile", "justfile", "dockerfile",
              ".gitmodules", "license", "codeowners"}
SKIP_DIRS = {".git", "node_modules", "out", "cache", "artifacts", "typechain",
             "typechain-types", "coverage", ".venv", "venv", "dist", "build"}
MAX_FILE_BYTES = 400_000

# Paths that never count as the protocol's own practice: vendored code,
# deployment dumps, audit reports (they *talk about* tools), lockfiles.
NOISE = re.compile(
    r"(^|/)(node_modules|vendor|vendored|third[-_]party|deployments?|broadcast|"
    r"audits?|cache|artifacts)/"
    r"|(^|/)(package-lock\.json|yarn\.lock|pnpm-lock\.yaml|npm-shrinkwrap\.json)$"
    r"|\.lock$", re.I)


@functools.lru_cache(maxsize=2048)
def _compile(pattern, flags):
    return re.compile(pattern, flags)


def _rx(p, flags):
    """Probes pass regexes as strings; cache them so ~50 variables x 100 repos
    does not re-compile the same few hundred patterns (re's own cache is small
    and gets evicted by the volume of distinct patterns here)."""
    return p if isinstance(p, re.Pattern) else _compile(p, flags)


class Repo:
    def __init__(self, repo_id, url, path, role="core", subpath=""):
        self.repo_id = repo_id
        self.url = url
        self.path = Path(path)
        self.role = role
        self.subpath = subpath.strip("/")
        self.files = []
        self._cache = {}
        self._memo = {}
        self._index()
        self.scoped = [f for f in self.files if self._in_scope(f)]
        self.first_party_submodules = self._count_first_party_submodules()

    # -- indexing ---------------------------------------------------------

    def _gitmodules(self):
        gm = self.path / ".gitmodules"
        return gm.read_text(errors="ignore") if gm.exists() else ""

    def _submodule_paths(self):
        return {m.group(1).strip().strip("/") for m in re.finditer(
            r"^\s*path\s*=\s*(.+)$", self._gitmodules(), re.M)}

    def _count_first_party_submodules(self):
        """Submodules owned by the same GitHub org as this repo.

        A shallow clone does not fetch submodule contents, so a protocol that splits
        its implementation across its own repos (Maple: 15 of 16) leaves almost no
        first-party source in the tree, and anything measured against that source is
        meaningless. One or two (a shared utils library) is normal and harmless.
        """
        owner = re.sub(r"^https?://[^/]+/", "", self.url or "").split("/")[0]
        if not owner:
            return 0
        rx = re.compile(r"[:/]" + re.escape(owner) + r"/", re.I)
        return sum(1 for m in re.finditer(r"^\s*url\s*=\s*(\S+)", self._gitmodules(), re.M)
                   if rx.search(m.group(1)))

    def _index(self):
        subs = self._submodule_paths()
        for dirpath, dirnames, filenames in os.walk(self.path):
            rel = Path(dirpath).relative_to(self.path).as_posix()
            rel = "" if rel == "." else rel
            # submodule checkouts are third-party code (or empty in a shallow clone)
            dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS
                           and (f"{rel}/{d}" if rel else d) not in subs]
            for fn in filenames:
                self.files.append(f"{rel}/{fn}" if rel else fn)
        self.files.sort()

    def _in_scope(self, f):
        if not self.subpath:
            return True
        return (f.startswith(self.subpath + "/") or f.startswith(".github/")
                or "/" not in f)

    def memo(self, key, fn):
        if key not in self._memo:
            self._memo[key] = fn()
        return self._memo[key]

    # -- reading ----------------------------------------------------------

    def text(self, relpath):
        """Read a file (cached). '' if unreadable, binary, or too big."""
        if relpath in self._cache:
            return self._cache[relpath]
        p = self.path / relpath
        try:
            if p.stat().st_size > MAX_FILE_BYTES:
                t = ""
            elif (p.suffix.lower() not in TEXT_EXT
                  and p.name.lower() not in TEXT_NAMES):
                t = ""
            else:
                t = p.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            t = ""
        self._cache[relpath] = t
        return t

    # -- search primitives ------------------------------------------------

    def glob(self, pattern, exclude=None):
        rx = _rx(pattern, re.I)
        ex = _rx(exclude, re.I) if exclude else None
        return [f for f in self.scoped
                if rx.search(f) and not (ex and ex.search(f))]

    def grep(self, pattern, path_filter=None, path_exclude=None,
             exclude=NOISE, limit=6, flags=re.I | re.M):
        rx = _rx(pattern, flags)
        pf = _rx(path_filter, re.I) if path_filter else None
        pe = _rx(path_exclude, re.I) if path_exclude else None
        hits = []
        for f in self.scoped:
            if exclude and exclude.search(f):
                continue
            if pf and not pf.search(f):
                continue
            if pe and pe.search(f):
                continue
            t = self.text(f)
            if not t:
                continue
            m = rx.search(t)
            if not m:
                continue
            start = t.rfind("\n", 0, m.start()) + 1
            end = t.find("\n", m.end())
            end = len(t) if end == -1 else end
            hits.append({"path": f,
                         "line": t.count("\n", 0, m.start()) + 1,
                         "snippet": t[start:end].strip()[:200]})
            if len(hits) >= limit:
                break
        return hits


def paths(hits):
    """Evidence dicts (or plain paths) -> evidence dicts."""
    return [h if isinstance(h, dict) else {"path": h} for h in hits]
