#!/usr/bin/env python3
"""Fetch SIX Swiss Bank Master and write data/swiss_bank_master.json."""
from __future__ import annotations

import json
import urllib.request
from pathlib import Path

URL = "https://api.six-group.com/api/epcd/bankmaster/v3/bankmaster.json?prettyPrint=false"
ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "swiss_bank_master.json"


def main() -> None:
    with urllib.request.urlopen(URL, timeout=60) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    banks = []
    for e in data.get("entries") or []:
        banks.append({
            "iid": e.get("iid"),
            "sic_iid": e.get("sicIid") or "",
            "type": e.get("iidType") or "",
            "name": e.get("bankOrInstitutionName") or "",
            "town": e.get("townName") or "",
            "bic": e.get("bic") or "",
            "hq": e.get("headQuarters"),
        })
    OUT.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "source": "SIX Swiss Bank Master API v3",
        "source_url": "https://api.six-group.com/api/epcd/bankmaster/v3/bankmaster.json",
        "valid_on": data.get("validOn"),
        "count": len(banks),
        "banks": banks,
    }
    OUT.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"Wrote {OUT} ({len(banks)} banks, valid_on={payload['valid_on']})")


if __name__ == "__main__":
    main()
