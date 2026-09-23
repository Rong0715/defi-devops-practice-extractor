"""Run every variable over a protocol's repos, merge the per-repo results, and apply
the protocol-level rules (N/A, unknown, derived variables).

Statuses
  ok       value is meaningful (may be a negative: absent / False / none)
  na       does not apply to this protocol (immutable, no admin role, no Solidity)
  unknown  the relevant part of the protocol lives outside the scanned repos
           (protocols.csv scope_gaps) and nothing was found in the ones we have
  error    a probe raised; a negative can't be trusted, so it is not reported as one
Only `ok` cells enter adoption rates.
"""

from probes import VARS, NotApplicable
from levels import RANK

MAX_EVIDENCE = 10
DERIVE_BOOL = {"D9_assurance.audited_publicly": lambda v: v in ("in_repo", "external_link")}


def probe_repo(repo):
    out = {}
    for vid, v in VARS.items():
        if v.fn is None:
            continue
        if v.scope == "core" and repo.role != "core":
            continue
        try:
            value, ev = v.fn(repo)
            out[vid] = {"status": "ok", "value": value, "evidence": ev}
        except NotApplicable as e:
            out[vid] = {"status": "na", "reason": str(e)}
        except Exception as e:  # fault isolation: one variable, one repo
            out[vid] = {"status": "error", "error": f"{type(e).__name__}: {e}"}
    return out


def _tokens(values):
    seen = []
    for val in values:
        for t in str(val).split("+"):
            if t and t != "none" and t not in seen:
                seen.append(t)
    return seen


def _best(v, values):
    if v.type == "adoption":
        return max(values, key=lambda x: RANK[x])
    if v.order:
        return max(values, key=v.order.index)
    if v.type == "bool":
        return any(values)
    if v.merge == "first" or v.type == "ratio":
        return values[0]
    return "+".join(_tokens(values)) or "none"


def merge(v, per_repo):
    """per_repo: {repo_id: result}, in repo order -> one result for the protocol."""
    oks = {k: r for k, r in per_repo.items() if r["status"] == "ok"}
    errs = [k for k, r in per_repo.items() if r["status"] == "error"]
    nas = [r for r in per_repo.values() if r["status"] == "na"]
    summary = {k: (r["value"] if r["status"] == "ok" else r["status"])
               for k, r in per_repo.items()}
    if not oks:
        if errs:
            return {"status": "error", "value": None, "per_repo": summary,
                    "reason": "; ".join(per_repo[k]["error"] for k in errs)[:200], "evidence": []}
        if nas:
            return {"status": "na", "value": None, "per_repo": summary,
                    "reason": nas[0]["reason"], "evidence": []}
        return {"status": "unknown", "value": None, "per_repo": summary,
                "reason": "no_repo_in_scope", "evidence": []}
    value = _best(v, [r["value"] for r in oks.values()])
    if errs and value == v.negative:
        return {"status": "error", "value": None, "per_repo": summary,
                "reason": "probe failed in " + ",".join(errs), "evidence": []}
    ev = []
    if value != v.negative:
        for k, r in oks.items():
            if r["value"] == value or v.type == "category":
                ev += [dict(e, repo=k) for e in r["evidence"]]
    elif v.type == "ratio":
        for k, r in oks.items():
            ev += [dict(e, repo=k) for e in r["evidence"]]
    return {"status": "ok", "value": value, "per_repo": summary,
            "evidence_total": len(ev), "evidence": ev[:MAX_EVIDENCE]}


def _derive(v, res):
    srcs = [(s, res[s]) for s in v.sources if s in res]
    ok = [(s, r) for s, r in srcs if r["status"] == "ok"]
    if not ok:
        st = "na" if srcs and all(r["status"] == "na" for _, r in srcs) else "error"
        return {"status": st, "value": None, "reason": "sources: " + ",".join(s for s, _ in srcs),
                "per_repo": {}, "evidence": []}
    if v.id in DERIVE_BOOL:
        s, r = ok[0]
        val = DERIVE_BOOL[v.id](r["value"])
        return {"status": "ok", "value": val, "per_repo": {}, "derived_from": [s],
                "evidence": r["evidence"] if val else []}
    s, r = max(ok, key=lambda x: RANK[x[1]["value"]])
    val = r["value"]
    return {"status": "ok", "value": val, "per_repo": {}, "derived_from": [s],
            "evidence": r["evidence"] if val != "absent" else []}


def apply_context(v, res, proto):
    """N/A and unknown come from the hand-labelled protocol context, not from probes."""
    if v.na == "immutable" and proto.get("upgradeability") == "immutable":
        return dict(res, status="na", value=None, reason="immutable", evidence=[])
    if v.na == "no_admin_role" and proto.get("has_admin_role") == "no":
        return dict(res, status="na", value=None, reason="no_admin_role", evidence=[])
    if (v.gap and v.gap in proto.get("gap_tags", ()) and res["status"] == "ok"
            and v.negative is not None and res["value"] == v.negative):
        return dict(res, status="unknown", value=None, reason=f"scope_gap:{v.gap}")
    return res


def audit_links(res, proto):
    """Audit reports listed by DefiLlama count as external evidence."""
    links = [u for u in proto.get("audit_links", "").split(";") if u.strip()]
    if not links or res["status"] != "ok":
        return res
    order = VARS["D9_assurance.audit_evidence"].order
    if order.index(res["value"]) < order.index("external_link"):
        return dict(res, value="external_link", evidence=[
            {"snippet": u.strip(), "source": "defillama:audit_links"} for u in links[:MAX_EVIDENCE]],
            evidence_total=len(links))
    return res


def run_protocol(proto, repos):
    """repos: list of Repo. Returns {var_id: result}."""
    per = {vid: {} for vid, v in VARS.items() if v.fn}
    for repo in repos:
        for vid, r in probe_repo(repo).items():
            per[vid][repo.repo_id] = r
    res = {}
    for vid, v in VARS.items():
        if v.fn is None:
            continue
        res[vid] = merge(v, per[vid])
    if "D9_assurance.audit_evidence" in res:
        res["D9_assurance.audit_evidence"] = audit_links(res["D9_assurance.audit_evidence"], proto)
    for vid, v in VARS.items():
        if v.fn is None:
            res[vid] = _derive(v, res)
    return {vid: apply_context(VARS[vid], r, proto) for vid, r in res.items()}
