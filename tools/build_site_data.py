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


def main():
    recs = json.load(open(ROOT / "out" / "all.json"))
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
        protocols.append({"id": p["protocol_id"], "name": p.get("name") or p["protocol_id"],
                          "parent": p.get("parent_id"), "category": p.get("category"),
                          "launch_year": int(p["launch_year"]) if str(p.get("launch_year", "")).isdigit() else None,
                          "upgradeability": p.get("upgradeability") or None,
                          "has_admin_role": p.get("has_admin_role") or None,
                          "tvl": float(p["tvl_eth_usd"]) if p.get("tvl_eth_usd") else None,
                          "label_status": p.get("label_status"), "repos": r["repos"], "vars": vs})
    acc = ROOT / "data" / "accuracy.json"
    data = {"generated_at": recs[0]["generated_at"] if recs else None,
            "snapshot_date": recs[0]["generated_at"][:10] if recs else None,
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
    print(f"wrote {out} ({len(protocols)} protocols, {len(data['variables'])} variables)")


if __name__ == "__main__":
    main()
