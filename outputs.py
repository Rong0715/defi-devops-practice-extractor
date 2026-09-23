"""Writers: per-protocol JSON, all.json, matrix.csv (wide), matrix_long.csv, variables.csv."""

import csv
import json
import re

from levels import RANK
from probes import VARS

SCHEMA_VERSION = 2
CONTEXT_COLS = ["protocol", "name", "parent_id", "category", "launch_year",
                "upgradeability", "has_admin_role", "commit"]


def permalink(repo_meta, ev):
    """https://github.com/<o>/<r>/blob/<sha>/<path>#L<n>, or None."""
    path = ev.get("path")
    url = repo_meta.get("url", "")
    if not path or "github.com" not in url or not repo_meta.get("commit"):
        return None
    base = re.sub(r"\.git$", "", url.rstrip("/"))
    frag = f"#L{ev['line']}" if ev.get("line") else ""
    return f"{base}/blob/{repo_meta['commit']}/{path}{frag}"


def cell(res):
    if res["status"] != "ok":
        return res["status"]
    v = res["value"]
    if isinstance(v, bool):
        return "yes" if v else "no"
    return v


def build_record(proto, repo_metas, results, generated_at):
    by_id = {m["id"]: m for m in repo_metas}
    vars_out = {}
    for vid, res in results.items():
        v = VARS[vid]
        ev = []
        for e in res.get("evidence", []):
            e = dict(e)
            link = permalink(by_id.get(e.get("repo"), {}), e)
            if link:
                e["url"] = link
            ev.append(e)
        vars_out[vid] = dict(res, evidence=ev, type=v.type, dim=v.dim, stage=v.stage)
    return {"schema_version": SCHEMA_VERSION, "generated_at": generated_at,
            "protocol": {k: val for k, val in proto.items() if k != "gap_tags"},
            "repos": repo_metas, "vars": vars_out}


def write_record(rec, out_dir):
    """One protocol, written as soon as it is done: a long run stays resumable."""
    (out_dir / f"{rec['protocol']['protocol_id']}.json").write_text(json.dumps(rec, indent=2))


def write_run_report(out_dir, generated_at, records, skipped, clone_failures):
    errs = {}
    for rec in records:
        for vid, c in rec["vars"].items():
            if c["status"] == "error":
                errs.setdefault(vid, []).append(rec["protocol"]["protocol_id"])
    report = {"generated_at": generated_at, "protocols": len(records),
              "skipped": skipped, "clone_failures": clone_failures,
              "error_cells": errs,
              "status_counts": {s: sum(1 for r in records for c in r["vars"].values()
                                       if c["status"] == s)
                                for s in ("ok", "na", "unknown", "error")}}
    (out_dir / "run.json").write_text(json.dumps(report, indent=2))
    return report


def write_tables(records, out_dir):
    (out_dir / "all.json").write_text(json.dumps(records, indent=2))

    with open(out_dir / "matrix.csv", "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(CONTEXT_COLS + list(VARS))
        for rec in records:
            p = rec["protocol"]
            core = next((m for m in rec["repos"] if m["role"] == "core"), {})
            w.writerow([p["protocol_id"], p.get("name", ""), p.get("parent_id", ""),
                        p.get("category", ""), p.get("launch_year", ""),
                        p.get("upgradeability", ""), p.get("has_admin_role", ""),
                        core.get("commit", "")[:10]]
                       + [cell(rec["vars"][vid]) for vid in VARS])

    with open(out_dir / "matrix_long.csv", "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["protocol", "variable", "dim", "stage", "type", "status", "value",
                    "level_rank", "reason", "evidence_repo", "evidence_path",
                    "evidence_line", "evidence_url", "evidence_snippet"])
        for rec in records:
            for vid, r in rec["vars"].items():
                e = (r.get("evidence") or [{}])[0]
                val = cell(r)
                rank = RANK.get(r["value"], "") if r["type"] == "adoption" and r["status"] == "ok" else ""
                w.writerow([rec["protocol"]["protocol_id"], vid, r["dim"], r["stage"], r["type"],
                            r["status"], val if r["status"] == "ok" else "", rank,
                            r.get("reason", ""), e.get("repo", ""), e.get("path", ""),
                            e.get("line", ""), e.get("url", ""), e.get("snippet", "")])

    with open(out_dir / "variables.csv", "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["variable", "dim", "stage", "type", "scope", "na_when", "gap_tag",
                    "description", "how_measured"])
        for v in VARS.values():
            w.writerow([v.id, v.dim, v.stage, v.type, v.scope, v.na or "", v.gap or "",
                        v.desc, v.measured])
