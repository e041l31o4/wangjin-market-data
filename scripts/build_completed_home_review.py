#!/usr/bin/env python3
"""Refresh all completed-project address candidates; never infer first sale or publish prices."""
import csv
import json
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path

from audit_community_mapping import address_key, inspect_mapping

ROOT = Path(__file__).resolve().parents[1]
MOI = "https://plvr.land.moi.gov.tw/DownloadOpenData"


def build_review(registry, transactions, mapping_rows, year):
    problems, mapping = inspect_mapping(mapping_rows)
    if problems:
        raise ValueError("社區門牌對照表不完整或衝突")
    projects = [p for p in registry["projects"] if p["kind"] == "成屋"]
    owners, groups = defaultdict(set), defaultdict(list)
    for p in projects:
        key = address_key(p.get("address"), p["district"])
        if key:
            owners[(p["district"], key)].add(str(p["projectId"]))
    completed = [(i, r) for i, r in transactions.items() if r.get("category") == "成屋" and str(r.get("date", "")).startswith(f"{year}-")]
    official_ids = Counter(r.get("officialTransactionId") for _, r in completed if r.get("officialTransactionId"))
    for i, r in completed:
        groups[(r["district"], address_key(r.get("addressKey"), r["district"]))].append((i, r))
    cases = []
    for p in projects:
        key = (p["district"], address_key(p.get("address"), p["district"]))
        rows = groups.get(key, []) if key[1] else []
        verified = key in mapping and {x["buildingName"] for x in mapping[key]} == {p["name"]}
        conflict = len(owners.get(key, set())) > 1
        evidence = []
        for i, r in rows:
            provenance = bool(r.get("officialTransactionId") and official_ids[r["officialTransactionId"]] == 1 and
                              r.get("officialSource") == MOI and
                              str(r.get("officialLocation", "")).startswith("高雄市" + p["district"]))
            try:
                traded, built = date.fromisoformat(r["date"]), date.fromisoformat(r["completedAt"])
                age_days = (traded - built).days
                recent = 0 <= age_days <= 5 * 365.2425
            except (KeyError, TypeError, ValueError):
                age_days, recent = None, False
            evidence.append({"transactionId": i, "officialTransactionId": r.get("officialTransactionId"),
                             "date": r.get("date"), "completedAt": r.get("completedAt"),
                             "officialLocation": r.get("officialLocation"), "officialSource": r.get("officialSource"),
                             "traceable": provenance, "withinFiveYears": recent,
                             "ageDays": age_days})
        status = ("address_conflict" if conflict else "verified_community_address" if verified else
                  "address_candidate_needs_evidence" if rows else "no_exact_address_sample" if key[1] else "registry_needs_full_address")
        cases.append({"projectId": str(p["projectId"]), "name": p["name"], "district": p["district"],
                      "registryAddress": p.get("address"), "addressKey": key[1], "status": status,
                      "transactionCountAtAddress": len(rows),
                      "traceableRecentTransactionCount": sum(e["traceable"] and e["withinFiveYears"] for e in evidence),
                      "communityMappingVerified": verified and not conflict,
                      "developerFirstSaleVerified": False, "transactions": evidence})
    return {"year": year, "completedProjectCount": len(cases), "officialCompletedTransactionCount": len(completed),
            "statusCounts": dict(sorted(Counter(c["status"] for c in cases).items())),
            "note": "門牌候選不等同建案成交或建商首售。屋齡另行列出；本清單不輸出價格，不自動建立首售對照。",
            "cases": cases}


def main():
    market = json.loads((ROOT / "market.json").read_text())
    with (ROOT / "data/community_mapping.csv").open(encoding="utf-8-sig", newline="") as f:
        mapping = list(csv.DictReader(f))
    review = build_review(json.loads((ROOT / "data/project_identity_register.json").read_text()),
                          json.loads((ROOT / "data/transactions.json").read_text()), mapping, market["year"])
    review["sourceDataUpdatedAt"] = market["updatedAt"]
    out = ROOT / "analysis/completed-home-review.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(review, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"成屋建案={review['completedProjectCount']} 狀態={review['statusCounts']}")


if __name__ == "__main__":
    main()
