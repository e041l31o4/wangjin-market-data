#!/usr/bin/env python3
"""Build recurring official-name-matched PRESALE samples, separately from reviewed v1 prices.

Only an explicit official project name + district matches a unique presale registry
entry. No alias, address guess, completed-home first-sale inference or manual price
approval is applied. Every published transaction must retain its official identity,
location and MOI source. Missing evidence is excluded and disclosed.
"""
import argparse
import hashlib
import json
import math
import re
import unicodedata
from collections import defaultdict
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MOI = "https://plvr.land.moi.gov.tw/DownloadOpenData"
RULE = "official_presale_name_district_v1"
PRICE_FIELDS = ("transactionCount", "latestTransactionDate", "latestUnitPriceWanPing",
                "averageUnitPriceWanPing", "minUnitPriceWanPing", "maxUnitPriceWanPing",
                "latestDayMinUnitPriceWanPing", "latestDayMaxUnitPriceWanPing")


def identity_name(value):
    # Whitespace/width/case only. Preserve punctuation, phase numbers and prefixes.
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", str(value or ""))).casefold()


def usable_name(value):
    return bool(value) and not any(c in value for c in ("?", "？", "\ufffd"))


def build_feed(registry, transactions, market):
    projects = registry["projects"]
    if len(projects) != 386 or len({str(p["projectId"]) for p in projects}) != 386:
        raise ValueError("建案名單必須有386筆唯一ID")
    keys = [(p["district"].strip(), identity_name(p["name"])) for p in projects]
    if any(not d or not n for d, n in keys) or len(set(keys)) != len(keys):
        raise ValueError("建案名稱／行政區為空或重複，停止自動配對")
    if any(p["kind"] not in ("預售屋", "成屋") for p in projects):
        raise ValueError("未知建案類型")
    updated = market.get("updatedAt")
    if not isinstance(updated, str):
        raise ValueError("缺少官方資料更新日")
    updated_day = date.fromisoformat(updated)
    groups = defaultdict(list)
    official_ids = defaultdict(list)
    for tx in transactions:
        key = (str(tx.get("district") or "").strip(), identity_name(tx.get("projectName")))
        groups[key].append(tx)
        if tx.get("officialTransactionId"):
            official_ids[tx["officialTransactionId"]].append(tx)
    # Conflicting or duplicated official IDs must never inflate sample counts.
    repeated = {key for key, rows in official_ids.items() if len(rows) > 1}
    entries, audit = [], []
    for project, key in zip(projects, keys):
        found = groups.get(key, [])
        accepted, reasons = [], set()
        if project["kind"] != "預售屋":
            reasons.add("completed_home_not_eligible")
        elif not usable_name(key[1]):
            reasons.add("unreadable_project_name")
        else:
            for tx in found:
                official_id = tx.get("officialTransactionId")
                location = tx.get("officialLocation")
                if not (isinstance(official_id, str) and official_id.strip() and
                        isinstance(location, str) and location.startswith("高雄市" + key[0]) and
                        tx.get("officialSource") == MOI):
                    reasons.add("missing_official_provenance")
                    continue
                expected_id = hashlib.sha256(("預售屋:" + official_id).encode()).hexdigest()[:24]
                if tx.get("transactionId") != expected_id or official_id in repeated:
                    reasons.add("duplicate_or_conflicting_transaction_identity")
                    continue
                try:
                    traded = date.fromisoformat(tx["date"])
                    price, area = float(tx["unitPriceWanPing"]), float(tx["areaPing"])
                except (KeyError, TypeError, ValueError):
                    reasons.add("invalid_transaction_values")
                    continue
                if (not math.isfinite(price) or not math.isfinite(area) or
                        not 2 <= price <= 300 or area <= 0 or traded > updated_day):
                    reasons.add("invalid_transaction_values")
                    continue
                accepted.append(tx)
            # A same-name project in two different official locations is ambiguous.
            # Do not guess that street names and parcels represent the same site.
            all_locations = {t.get("officialLocation") for t in found if t.get("officialLocation")}
            if len(all_locations) > 1:
                reasons.add("conflicting_official_locations")
                accepted = []
        entry = {
            "projectId": str(project["projectId"]), "name": project["name"],
            "district": project["district"], "registryKind": project["kind"],
            "registryAddress": project.get("address"),
            "priceStatus": "official_name_matched" if accepted else "pending_verification",
            "transactionKind": "預售屋", "matchingRule": RULE if accepted else None,
            "sourceRecordCount": len(found),
            "excludedRecordCount": len(found) - len(accepted),
            "officialLocations": sorted({t["officialLocation"] for t in accepted}),
            "officialIdentityCount": len(accepted),
            "verificationSource": MOI if accepted else None,
            "transactionSource": "內政部實價登錄預售屋（官方案名與行政區配對）",
            "verifiedOn": None, "reviewedBy": None,
            **{field: None for field in PRICE_FIELDS},
        }
        if accepted:
            accepted.sort(key=lambda t: (t["date"], t["transactionId"]), reverse=True)
            prices = [float(t["unitPriceWanPing"]) for t in accepted]
            latest = accepted[0]["date"]
            day_prices = [float(t["unitPriceWanPing"]) for t in accepted if t["date"] == latest]
            entry.update(
                transactionCount=len(accepted), latestTransactionDate=latest,
                latestUnitPriceWanPing=accepted[0]["unitPriceWanPing"],
                averageUnitPriceWanPing=round(sum(prices) / len(prices), 2),
                minUnitPriceWanPing=min(prices), maxUnitPriceWanPing=max(prices),
                latestDayMinUnitPriceWanPing=min(day_prices),
                latestDayMaxUnitPriceWanPing=max(day_prices),
            )
        entries.append(entry)
        audit.append({
            "projectId": entry["projectId"], "name": entry["name"],
            "district": entry["district"], "kind": entry["registryKind"],
            "sourceRecords": len(found), "includedRecords": len(accepted),
            "reasons": sorted(reasons) if found or project["kind"] != "預售屋" else ["no_exact_official_sample"],
        })
    return {
        "schemaVersion": 2, "feedKind": "public_official_presale",
        "source": MOI, "sourceDataUpdatedAt": updated,
        "totalProjects": len(entries),
        "matchedPriceProjects": sum(r["priceStatus"] == "official_name_matched" for r in entries),
        "includedTransactionCount": sum(r["transactionCount"] or 0 for r in entries),
        "importantNotes": [
            "價格為已收錄且可追溯的預售成交樣本，不是全案完整成交紀錄。",
            "依官方案名與行政區配對；不等同基地或座標逐案核實。",
            "平均單價採收錄逐筆單價的算術平均；缺少官方編號或位置的紀錄不列入。",
            "不自動套用別名；目前列為成屋的建案不以預售紀錄當成成屋首售。",
            "新成交通過同一配對規則後自動計入；歧義與缺失資料維持待確認。",
        ],
        "projects": entries,
    }, {"sourceDataUpdatedAt": updated, "projects": audit}


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--registry", type=Path, default=ROOT / "data/project_identity_register.json")
    p.add_argument("--transactions", type=Path, default=ROOT / "data/project_transactions.json")
    p.add_argument("--market", type=Path, default=ROOT / "market.json")
    p.add_argument("--output", type=Path, default=ROOT / "data/official_presale_project_feed.json")
    p.add_argument("--audit", type=Path, default=ROOT / "analysis/official-presale-match-audit.json")
    args = p.parse_args()
    feed, audit = build_feed(
        json.loads(args.registry.read_text(encoding="utf-8")),
        json.loads(args.transactions.read_text(encoding="utf-8")),
        json.loads(args.market.read_text(encoding="utf-8")),
    )
    for path, data in ((args.output, feed), (args.audit, audit)):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("建案=%s 預售可顯示=%s 可追溯成交=%s" %
          (feed["totalProjects"], feed["matchedPriceProjects"], feed["includedTransactionCount"]))


if __name__ == "__main__":
    main()
