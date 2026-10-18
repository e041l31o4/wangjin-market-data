#!/usr/bin/env python3
"""Conservative completed-home address mapping audit (no data mutation).

Usage:
  python scripts/audit_community_mapping.py
  python scripts/audit_community_mapping.py --candidates analysis/completed-home-address-audit-20261018.json

The source transaction dataset contains only anonymised, building-number address
keys. An address match is a candidate, not proof of developer first-sale.
"""
import argparse
import csv
import json
import re
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TX = ROOT / "data/transactions.json"
MAPPING = ROOT / "data/community_mapping.csv"
ADDRESS_CANDIDATES = ROOT / "analysis/completed-home-address-audit-20261018.json"


def address_key(address, district):
    """Match the existing updater's exact building-number key semantics."""
    import unicodedata
    address = unicodedata.normalize("NFKC", str(address or ""))
    address = re.sub(r"(號(?:之\d+)?)\s+.*$", r"\1", address)
    address = re.sub(r"\s+", "", address).replace("臺", "台")
    address = re.sub(r"^台灣(?:省)?", "", address)
    address = re.sub(r"^高雄市", "", address)
    if address.startswith(district):
        address = address[len(district):]
    match = re.match(r"^(.+?號(?:之\d+)?)", address)
    return match.group(1) if match else None


def inspect_mapping(rows):
    problems = []
    by_address = defaultdict(list)
    for idx, row in enumerate(rows, 2):
        district = (row.get("district") or "").strip()
        address = address_key(row.get("addressKey"), district)
        name = (row.get("buildingName") or "").strip()
        source = (row.get("source") or "").strip()
        if not district or not address or not name or not source:
            problems.append({"line": idx, "reason": "incomplete_verified_mapping"})
            continue
        by_address[(district, address)].append({"line": idx, "buildingName": name})
    for (district, address), names in by_address.items():
        distinct = sorted({x["buildingName"] for x in names})
        if len(distinct) > 1:
            problems.append({"reason": "address_conflict", "district": district,
                             "addressKey": address, "buildingNames": distinct})
    return problems, by_address


def run(transactions, mapping_rows, cases):
    problems, mapping = inspect_mapping(mapping_rows)
    counts = defaultdict(int)
    for record in transactions.values():
        if record.get("category") != "成屋":
            continue
        district = record.get("district")
        address = address_key(record.get("addressKey"), district)
        if address:
            counts[(district, address)] += 1
    result = []
    candidate_address_owners = defaultdict(set)
    for case in cases:
        district = case.get("district") or ""
        address = address_key(case.get("pdfAddress"), district)
        case_name = case.get("name") or ""
        if address:
            candidate_address_owners[(district, address)].add(case_name)
        verified = (district, address) in mapping
        result.append({
            "projectId": case.get("projectId"), "name": case_name,
            "district": district, "candidateAddress": address,
            "transactionCountAtAddress": counts.get((district, address), 0),
            "verifiedMappingExists": verified,
            "readyForPublish": False,
            "reason": "exact_address_is_only_candidate_not_confirmed_transaction_attribution"
        })
    for (district, address), owners in candidate_address_owners.items():
        if len(owners) > 1:
            problems.append({"reason": "multiple_projects_share_candidate_address",
                             "district": district, "addressKey": address,
                             "buildingNames": sorted(owners)})
    return {"candidateCount": len(result),
            "officialCompletedTransactionCount": sum(
                1 for t in transactions.values() if t.get("category") == "成屋"),
            "mappedCommunityAddressCount": len(mapping),
            "warnings": problems,
            "candidates": result,
            "note": "DO NOT publish address-only candidates. Verify official transaction attribution and correct conflicting addresses first."}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--candidates", default=str(ADDRESS_CANDIDATES))
    parser.add_argument("--output", help="Optional JSON report path")
    args = parser.parse_args()
    records = json.loads(TX.read_text(encoding="utf-8"))
    data = json.loads(Path(args.candidates).read_text(encoding="utf-8"))
    cases = data.get("cases", [])
    rows = []
    if MAPPING.exists():
        with MAPPING.open(encoding="utf-8-sig", newline="") as handle:
            rows = list(csv.DictReader(handle))
    report = run(records, rows, cases)
    payload = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        Path(args.output).write_text(payload, encoding="utf-8")
    else:
        print(payload)
    # An unresolved candidate collision blocks any later auto-publish pipeline,
    # but audit itself returns successfully so it can be reviewed.
    print(f"稽核候選={report['candidateCount']}；成屋資料={report['officialCompletedTransactionCount']}；衝突或欄位問題={len(report['warnings'])}")


if __name__ == "__main__":
    main()
