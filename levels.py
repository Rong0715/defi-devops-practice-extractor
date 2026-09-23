"""Adoption levels and the CI engine behind them.

  runs_in_ci   a workflow triggered by push/pull_request/merge_group (or called by
               one) invokes the practice, directly or one-to-three hops away
               through make / yarn / npm / just targets.  Not marked
               continue-on-error / `|| true` (those count as configured: advisory).  Whether the check is *required*
               to merge is branch protection -- server-side, invisible to us.
  configured   config file, Makefile/package.json/justfile/script entry, or the
               practice's code exists in the repo (or it runs only on a
               schedule/manually) -- the team can run it, nothing forces them to
  mentioned    appears only in docs/comments
  absent       no trace

Path filters (`paths:`, `branches:`) on workflow triggers are not evaluated.
"""

import json
import re

from repo import paths as _paths

LEVELS = ["absent", "mentioned", "configured", "runs_in_ci"]
RANK = {l: i for i, l in enumerate(LEVELS)}

EVENTS = {"push", "pull_request", "pull_request_target", "merge_group",
          "workflow_call", "workflow_run", "schedule", "workflow_dispatch",
          "release", "repository_dispatch", "create", "deployment", "issues",
          "issue_comment", "status", "check_run", "pull_request_review"}
GATING = {"push", "pull_request", "pull_request_target", "merge_group",
          "workflow_call", "workflow_run"}

CONFIG_FILES = (r"(^|/)(makefile|gnumakefile|justfile|package\.json|foundry\.toml|"
                r"hardhat\.config\.\w+|[\w.-]*\.config\.(js|ts|cjs|mjs|json)|"
                r"[\w.-]+\.toml|[\w.-]+\.ya?ml|\.[\w-]*rc(\.\w+)?)$"
                r"|(^|/)(scripts?|tasks?)/[^/]+\.(sh|ts|js|py|mjs|cjs)$")


def _ind(s):
    return len(s) - len(s.lstrip(" \t"))


# ---------------------------------------------------------------------------
# workflow parsing (no YAML library: only the few keys we need)
# ---------------------------------------------------------------------------

def parse_triggers(lines):
    start, inline = None, ""
    for i, l in enumerate(lines):
        m = re.match(r"""^["']?on["']?\s*:\s*(.*)$""", l)
        if m:
            start, inline = i, m.group(1)
            break
    if start is None:
        return set()
    ev = set()
    inline = re.sub(r"\s#.*$", "", inline).strip()
    if inline:
        ev |= set(re.findall(r"[a-z_]+", inline)) & EVENTS
    base = None
    for l in lines[start + 1:]:
        if not l.strip() or l.lstrip().startswith("#"):
            continue
        if not l[0].isspace() and not l.startswith("-"):
            break
        if base is None:
            base = _ind(l)
        if _ind(l) == base:
            m = re.match(r"\s*(?:-\s*)?([a-z_]+)\b", l)
            if m and m.group(1) in EVENTS:
                ev.add(m.group(1))
    return ev


def _step_span(lines, i):
    """Line range of the YAML step containing line i."""
    s, ind = i, _ind(lines[i])
    while s >= 0:
        m = re.match(r"^(\s*)-\s", lines[s])
        if m and (s == i or len(m.group(1)) < ind):
            break
        s -= 1
    if s < 0:
        return i, i + 1
    d = _ind(lines[s])
    e = i + 1
    while e < len(lines) and not (lines[e].strip() and _ind(lines[e]) <= d):
        e += 1
    return s, e


_COE = re.compile(r"continue-on-error\s*:\s*true")


def _job_continue_on_error(lines, i):
    """`continue-on-error: true` on the job that owns line i (a sibling key of `steps:`)."""
    s = i
    while s >= 0 and not re.match(r"^\s*steps\s*:", lines[s]):
        s -= 1
    if s < 0:
        return False
    ind = _ind(lines[s])
    for k in range(s - 1, -1, -1):
        if not lines[k].strip() or lines[k].lstrip().startswith("#"):
            continue
        if _ind(lines[k]) < ind:      # reached the job id -- searched the whole job
            break
        if _ind(lines[k]) == ind and _COE.search(lines[k]):
            return True
    return False


def _continue_on_error(lines, i):
    s, e = _step_span(lines, i)
    return (any(_COE.search(lines[k]) for k in range(s, e))
            or _job_continue_on_error(lines, i))


def _swallowed(text):
    return bool(re.search(r"\|\|\s*(true|exit\s+0|:)\b", text))


_RUN = re.compile(r"^(-\s*)?run\s*:\s*(.*)$")
_USES = re.compile(r"^(-\s*)?uses\s*:")


def command_lines(lines, kind="workflow"):
    """[(index, text)] for the lines of a workflow that actually invoke something:
    `run:` (including its block scalar) and `uses:`.

    Everything else -- env vars, `with:` arguments, artifact paths, cache keys,
    `if:` expressions -- is data, and matching tool names there produces false
    positives (an ETHERSCAN_KEY secret read as automated verification, a
    `path: coverage/lcov.info` read as a coverage run). An action's identity is
    already on its `uses:` line, so dropping `with:` loses no tool.
    Non-GitHub CI files keep every line: their shapes vary too much.
    """
    out, block = [], None          # block = indent a run: body must exceed
    for i, l in enumerate(lines):
        s = l.strip()
        if not s or s.startswith("#"):
            continue
        ind = _ind(l)
        if block is not None:
            if ind > block:
                out.append((i, re.sub(r"\s#.*$", "", s)))
                continue
            block = None
        if kind == "other":
            out.append((i, re.sub(r"\s#.*$", "", s)))
            continue
        m = _RUN.match(s)
        if m:
            rest = re.sub(r"\s#.*$", "", m.group(2)).strip()
            if rest and rest not in ("|", ">", "|-", ">-", "|+", ">+"):
                out.append((i, rest))
            block = ind
        elif _USES.match(s):
            out.append((i, re.sub(r"\s#.*$", "", s)))
    return out


# ---------------------------------------------------------------------------
# make / package.json / justfile target resolution
# ---------------------------------------------------------------------------

_INV = [
    ("make", re.compile(r"\bmake\b((?:[ \t]+[^\s&|;]+)*)")),
    ("npm", re.compile(r"\b(?:yarn|pnpm|bun|npm)[ \t]+(?:run(?:-script)?[ \t]+|exec[ \t]+)?([\w:.@-]+)")),
    ("just", re.compile(r"\bjust[ \t]+([\w-]+)")),
]


def _parse_make(text):
    out, cur = {}, []
    for l in text.splitlines():
        if l.startswith("\t") and cur:
            for n in cur:
                out[n]["body"].append(l.strip().lstrip("@-").strip())
            continue
        m = re.match(r"^([A-Za-z0-9_.\-/%]+(?:[ \t]+[A-Za-z0-9_.\-/%]+)*)[ \t]*:(?!=)[ \t]*([^#\n]*)", l)
        if m and not l.startswith((" ", "#")):
            cur = m.group(1).split()
            for n in cur:
                out[n] = {"body": [], "deps": m.group(2).split()}
        else:
            cur = []
    return out


def _parse_just(text):
    out, cur = {}, None
    for l in text.splitlines():
        if l[:1] in (" ", "\t") and cur:
            out[cur]["body"].append(l.strip().lstrip("@-").strip())
            continue
        m = re.match(r"^@?([\w-]+)[^:=\n]*:(?!=)[ \t]*([^#\n]*)", l)
        cur = m.group(1) if m else None
        if m:
            out[cur] = {"body": [], "deps": m.group(2).split()}
    return out


class Resolver:
    def __init__(self, r):
        self.store = {}
        order = lambda f: (f.count("/"), f)
        for f in sorted(r.glob(r"(^|/)(makefile|gnumakefile)$"), key=order):
            for n, e in _parse_make(r.text(f)).items():
                self.store.setdefault(("make", n), dict(e, via=f"{f}:{n}"))
        for f in sorted(r.glob(r"(^|/)justfile$"), key=order):
            for n, e in _parse_just(r.text(f)).items():
                self.store.setdefault(("just", n), dict(e, via=f"{f}:{n}"))
        for f in sorted(r.glob(r"(^|/)package\.json$", exclude=r"node_modules"), key=order):
            try:
                scripts = json.loads(r.text(f)).get("scripts", {})
            except Exception:
                continue
            if not isinstance(scripts, dict):
                continue
            for n, body in scripts.items():
                if isinstance(body, str):
                    self.store.setdefault(("npm", n), {"body": [body], "deps": [],
                                                       "via": f"{f}#scripts.{n}"})

    def expand(self, text, depth=3, seen=None):
        """Lines reachable from a command line by following make/npm/just targets."""
        seen = set() if seen is None else seen
        out = []
        for kind, rx in _INV:
            for m in rx.finditer(text):
                names = m.group(1).split() if kind == "make" else [m.group(1)]
                for n in names:
                    if n.startswith("-") or "=" in n:
                        continue
                    keys = [(kind, n)]
                    if kind == "npm":
                        keys += [("npm", "pre" + n), ("npm", "post" + n)]
                    for k in keys:
                        e = self.store.get(k)
                        if not e or k in seen:
                            continue
                        seen.add(k)
                        for bl in e["body"]:
                            out.append((bl, e["via"]))
                            if depth > 1:
                                out += self.expand(bl, depth - 1, seen)
                        if depth > 1:
                            for dep in e["deps"]:
                                out += self.expand(f"{kind} {dep}", depth - 1, seen)
        return out


# ---------------------------------------------------------------------------
# CI model
# ---------------------------------------------------------------------------

class CI:
    def __init__(self, r):
        self.r = r
        res = Resolver(r)
        self.files = []
        for f in r.glob(r"^\.github/workflows/[^/]+\.ya?ml$"):
            lines = r.text(f).splitlines()
            self.files.append({"path": f, "kind": "workflow", "lines": lines,
                               "triggers": parse_triggers(lines)})
        for f in r.glob(r"^\.github/(actions/.+|workflows/.+/[^/]+)\.ya?ml$"):
            self.files.append({"path": f, "kind": "action",
                               "lines": r.text(f).splitlines(), "triggers": set()})
        for f in r.glob(r"(^|/)(\.circleci/config\.yml|\.gitlab-ci\.yml|"
                        r"azure-pipelines\.ya?ml|\.travis\.yml)$"):
            self.files.append({"path": f, "kind": "other",
                               "lines": r.text(f).splitlines(), "triggers": {"push"}})
        gating_text = "\n".join(
            r.text(c["path"]) for c in self.files
            if c["kind"] != "action" and c["triggers"] & GATING)
        for c in self.files:
            if c["kind"] == "action":
                d = c["path"].rsplit("/", 1)[0]
                c["gating"] = d in gating_text
            else:
                c["gating"] = bool(c["triggers"] & GATING)
            c["cmds"] = [(i, s, [(t, v) for t, v in res.expand(s)])
                         for i, s in command_lines(c["lines"], c["kind"])]

    def hits(self, rx, extra=(), limit=4):
        """(gating_hits, non_gating_hits) for a regex over CI command lines."""
        rx = rx if isinstance(rx, re.Pattern) else re.compile(rx, re.I)
        extra = set(extra)
        gating, other = [], []
        for c in self.files:
            is_g = c["gating"] or bool(c["triggers"] & extra)
            best = None
            for i, s, exp in c["cmds"]:
                for text, via in [(s, None)] + exp:
                    if not rx.search(text):
                        continue
                    # runs, but its failure cannot fail the build: advisory, so not a gate
                    advisory = _swallowed(text) or (
                        via is None and c["kind"] != "other" and _continue_on_error(c["lines"], i))
                    e = {"path": c["path"], "line": i + 1, "snippet": s[:200]}
                    if advisory:
                        e["advisory"] = True
                    if via:
                        e["via"] = via
                    if c["kind"] == "workflow":
                        e["triggers"] = sorted(c["triggers"])
                    if re.search(r"uses:\s*[\w.-]+/[\w.-]+/\.github/workflows/", s):
                        # CI delegated to a reusable workflow in another repo: we only see its name
                        e["external_workflow"] = True
                    if best is None or (best.get("advisory") and not advisory):
                        best = e   # one hit per file, but an enforcing step beats an advisory one
                    break
                if best is not None and not best.get("advisory"):
                    break
            if best is None and c["kind"] == "workflow" and rx.search(
                    c["path"].rsplit("/", 1)[-1]):
                # No command names the tool, but the workflow does (verify-state.yml,
                # run-slither.yaml). Weaker than a command, so it is only a fallback:
                # a workflow that *does* name the tool in a `|| true` step stays advisory.
                best = {"path": c["path"], "line": 1, "snippet": "workflow name",
                        "by_filename": True, "triggers": sorted(c["triggers"])}
            if best is not None:
                (gating if is_g and not best.get("advisory") else other).append(best)
        return gating[:limit], other[:limit]

    def any_trigger(self, names):
        names = set(names)
        return [{"path": c["path"], "snippet": "on: " + ", ".join(sorted(c["triggers"]))}
                for c in self.files if c["triggers"] & names][:4]


def ci_of(r):
    return r.memo("ci", lambda: CI(r))


# ---------------------------------------------------------------------------
# adoption level
# ---------------------------------------------------------------------------

def _tag(hits, level):
    return [dict(h, level=level) for h in _paths(hits)]


def adoption(r, *, ci=None, ci_extra=(), ci_needs_present=False, present=None,
             cfg=None, cfg_where=CONFIG_FILES, cfg_paths=None,
             mention=None, mention_paths=None):
    """
    ci               regex for CI command lines (after make/yarn/just resolution)
    ci_extra         extra workflow triggers that count for this variable
                     (e.g. {"schedule"} for recurring state checks, {"release"} for ABIs)
    present          callable -> evidence that the practice's code exists in the repo
    ci_needs_present for technique-style practices (fuzzing, fork tests): CI running the
                     test runner only counts if the technique's code exists
    cfg / cfg_where  regex over config-ish files -> configured (default: the ci regex)
    cfg_paths        path regex; existence of such a file -> configured
    mention          regex (default: the ci regex; False disables) -> mentioned
    """
    pres = present() if present else []
    other = []
    if cfg is None and ci and not ci_needs_present:
        cfg = ci  # a tool named in a Makefile / package.json / config file is configured
    if ci:
        g, other = ci_of(r).hits(ci, ci_extra)
        if g and (pres or not ci_needs_present):
            return "runs_in_ci", _tag(g[:2] + pres[:2], "runs_in_ci")
    conf = list(pres)
    if cfg_paths:
        conf += _paths(r.glob(cfg_paths)[:3])
    if cfg:
        conf += r.grep(cfg, path_filter=cfg_where, path_exclude=r"^\.github/", limit=3)
    if ci and not ci_needs_present:
        conf += other  # in CI, but only on a schedule / manually
    if conf:
        return "configured", _tag(conf[:4], "configured")
    m = ci if mention is None else mention
    if m:
        h = r.grep(m, path_filter=mention_paths, limit=2)
        if h:
            return "mentioned", _tag(h, "mentioned")
    return "absent", []
