#!/usr/bin/env python3
"""out/all.json (+ optional data/accuracy.json) -> site/data/data.js

The site is static: it reads window.DATA from data.js (a .js file, not .json, so the
page also works when opened straight from disk). site/data/ is committed; out/ is not.
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from levels import LEVELS  # noqa: E402
from probes import VARS, STAGES  # noqa: E402
from extract import load_csv  # noqa: E402

LABELS = {
    "four_naly3er": "4naly3er", "runs_on_pull_request": "CI runs on pull requests",
    "test_to_src_ratio": "Test-to-source LoC ratio", "suite_present": "Test suite present",
    "any_analyzer": "Any static analyzer", "formal_any": "Any formal / symbolic tool",
    "audited_publicly": "Audit report public", "competitive_audit": "Competitive audit",
    "lint_gate": "Lint / format gate", "gas_regression": "Gas regression tracking",
    "fork_testing": "Fork testing", "fuzz_testing": "Fuzz tests", "invariant_testing": "Invariant tests",
    "formal_certora": "Certora", "formal_halmos": "Halmos", "formal_kontrol": "Kontrol",
    "fuzz_echidna": "Echidna", "fuzz_medusa": "Medusa", "slither": "Slither", "aderyn": "Aderyn",
    "semgrep": "Semgrep", "mythril": "Mythril / MythX", "coverage": "Coverage",
    "upgrade_safety_check": "Upgrade-safety check", "proposal_simulation": "Proposal simulation",
    "onchain_state_verification": "On-chain state verification", "abi_release": "ABI release",
    "verification_automated": "Source verification", "deterministic_deploy": "Deterministic deploy",
    "artifacts_committed": "Deployment artifacts committed", "deploy_or_governance_automation": "Deploy / governance in CI",
    "bug_bounty": "Bug bounty", "security_policy": "Security policy", "incident_runbook": "Incident runbook",
    "monitoring_tools": "Monitoring tools", "release_process_doc": "Release process doc",
    "dep_lockfile": "Dependency lockfile", "adr": "Decision records (ADR)", "multichain": "Multichain deploy",
}


def label(v):
    return LABELS.get(v.name, v.name.replace("_", " ").capitalize())


def headline_ids(protocols):
    """One subject per parent for headline statistics: the latest version (launch year),
    ties broken by Ethereum TVL. Versions and sibling products of one team share its
    practices, so counting all of them would weight Uniswap three times."""
    best = {}
    for p in protocols:
        key = (p["launch_year"] or 0, p["tvl"] or 0, p["id"])
        fam = p["parent"] or p["id"]
        if fam not in best or key > best[fam][0]:
            best[fam] = (key, p["id"])
    return {pid for _, pid in best.values()}


def repo_check(auto, hand):
    """Is the scanned repo the protocol's real code? A hand decision overrides the tool's.
    status: auto (tools/verify_mapping.py tied it to deployed contracts) | hand (a person
    confirmed it) | doubtful (a person flagged it) | unconfirmed | None (never checked)."""
    if hand and hand["decision"] == "doubtful":
        return {"status": "doubtful", "detail": hand.get("note", "")}
    if auto and auto["verdict"] == "confirmed":
        return {"status": "auto", "detail": auto["matched"]}
    if hand and hand["decision"] == "confirmed":
        return {"status": "hand", "detail": hand.get("note", "")}
    if auto:
        why = {"no_match": "the deployed contracts DefiLlama lists are not declared in the repo "
                           "(often only tokens or an older version)",
               "no_evidence": "DefiLlama's adapter names no deployed contract to compare against",
               "weak": "only generic contract names matched"}
        return {"status": "unconfirmed", "detail": why.get(auto["verdict"], auto["verdict"])}
    return None


def main():
    recs = json.load(open(ROOT / "out" / "all.json"))
    mc, mr = ROOT / "data" / "mapping_check.csv", ROOT / "data" / "mapping_review.csv"
    auto = {r["protocol_id"]: r for r in load_csv(mc)} if mc.exists() else {}
    hand = {r["protocol_id"]: r for r in load_csv(mr)} if mr.exists() else {}
    protocols = []
    for r in recs:
        vs = {}
        for vid, c in r["vars"].items():
            vs[vid] = {"s": c["status"], "v": c.get("value"), "reason": c.get("reason"),
                       "per_repo": c.get("per_repo"),
                       "ev": [{k: e[k] for k in ("repo", "path", "line", "snippet", "url", "via",
                                                  "level", "external_workflow", "advisory", "by_filename", "source", "triggers")
                               if k in e} for e in c.get("evidence", [])[:5]]}
        p = r["protocol"]
        core = next((m for m in r["repos"] if m["role"] == "core"), {})
        protocols.append({"id": p["protocol_id"], "name": p.get("name") or p["protocol_id"],
                          "parent": p.get("parent_id"), "category": p.get("category"),
                          "launch_year": int(p["launch_year"]) if str(p.get("launch_year", "")).isdigit() else None,
                          "upgradeability": p.get("upgradeability") or None,
                          "has_admin_role": p.get("has_admin_role") or None,
                          "tvl": float(p["tvl_eth_usd"]) if p.get("tvl_eth_usd") else None,
                          "label_status": p.get("label_status"),
                          "commit_date": (core.get("commit_date") or "")[:10] or None,
                          "repo_check": repo_check(auto.get(p["protocol_id"]), hand.get(p["protocol_id"])),
                          "repos": r["repos"], "vars": vs})
    heads = headline_ids(protocols)
    for p in protocols:
        p["headline"] = p["id"] in heads
    acc = ROOT / "data" / "accuracy.json"
    data = {"generated_at": recs[0]["generated_at"] if recs else None,
            # the DefiLlama snapshot the sample and TVL come from (not the scan date)
            "snapshot_date": recs[0]["protocol"].get("tvl_snapshot_date") if recs else None,
            "levels": LEVELS, "stages": list(dict.fromkeys(STAGES.values())),
            "dims": [{"id": d, "stage": s} for d, s in STAGES.items()],
            "variables": [{"id": v.id, "dim": v.dim, "name": v.name, "label": label(v), "stage": v.stage,
                           "type": v.type, "scope": v.scope, "na": v.na, "desc": v.desc,
                           "measured": v.measured,
                           "derived": v.fn is None} for v in VARS.values()],
            "protocols": protocols,
            "accuracy": json.load(open(acc)) if acc.exists() else None}
    out = ROOT / "site" / "data" / "data.js"
    out.write_text("window.DATA = " + json.dumps(data, separators=(",", ":")) + ";\n")
    print(f"wrote {out} ({len(protocols)} subjects, {len(heads)} headline, {len(data['variables'])} variables)")


if __name__ == "__main__":
    main()
