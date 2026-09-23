#!/usr/bin/env python3
"""Download DefiLlama /protocols once and commit a trimmed, dated snapshot.

    python3 tools/fetch_defillama.py

Writes data/sources/defillama_<YYYY-MM-DD>.json: only protocols present on Ethereum, only the
fields the sampling step reads. /protocols returns *current* values, so the snapshot date is the
download date -- committing the file is what makes the sample reproducible.
"""
import datetime
import json
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
URL = "https://api.llama.fi/protocols"
KEEP = ("name", "slug", "category", "parentProtocol", "forkedFromIds", "forkedFrom", "github",
        "audit_links", "deprecated", "listedAt", "url", "openSource", "governanceID", "symbol")


def main():
    req = urllib.request.Request(URL, headers={"User-Agent": "defi-devops-extractor"})
    raw = json.load(urllib.request.urlopen(req, timeout=120))
    out = []
    for p in raw:
        if "Ethereum" not in (p.get("chains") or []):
            continue
        row = {k: p[k] for k in KEEP if p.get(k) not in (None, [], "")}
        row["eth_tvl_usd"] = round((p.get("chainTvls") or {}).get("Ethereum") or 0)
        row["id"] = p.get("id")
        out.append(row)
    out.sort(key=lambda r: -r["eth_tvl_usd"])
    today = datetime.date.today().isoformat()
    dest = ROOT / "data" / "sources" / f"defillama_{today}.json"
    dest.write_text(json.dumps({"snapshot_date": today, "source": URL, "protocols": out}, separators=(",", ":")))
    print(f"{len(raw)} protocols -> {len(out)} on Ethereum -> {dest} ({dest.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    main()
