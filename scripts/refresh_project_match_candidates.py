#!/usr/bin/env python3
"""Recalculate *candidate* presale matches from the latest MOI-derived records.

The 386 project IDs/names are a frozen list taken from the approved initial
WANGJIN project register. This script never trusts aliases or address heuristics
and does not publish candidate prices to the public feed.
"""
import argparse
import hashlib
import json
import re
import unicodedata
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REGISTER = ROOT / "analysis/project-match-candidates-20261018.json"
TRANSACTIONS = ROOT / "data/project_transactions.json"
MARKET = ROOT / "market.json"
DEFAULT_OUTPUT = ROOT / "analysis/project-pre-sale-price-latest.json"


def normalize_name(s):
    return re.sub(r"[\s·・．.－\-—_()（）【】\[\]]", "", unicodedata.normalize("NFKC", str(s or "")).lower())


def recalculate(register, transactions, market):
    baseline = register.get("projects")
    if not isinstance(baseline, list) or len(baseline) != 386:
        raise ValueError("建案清單已變更；需先審查386筆基準清單")
    ids = [str(p["projectId"]) for p in baseline]
    if len(set(ids)) != len(ids):
        raise ValueError("建案ID重複")
    registry_keys = [(str(p["district"]).strip(), normalize_name(p["projectName"])) for p in baseline]
    if any(not district or not name for district, name in registry_keys):
        raise ValueError("建案名稱或行政區不可為空")
    if len(set(registry_keys)) != len(registry_keys):
        raise ValueError("存在同行政區同名稱建案，成交不可重複歸屬；須先人工拆分")
    lookup = defaultdict(list)
    for index, row in enumerate(transactions, start=1):
        try:
            district = str(row["district"]).strip()
            name = normalize_name(row["projectName"])
            date = str(row["date"])
            price = float(row["unitPriceWanPing"])
            area = float(row["areaPing"])
        except (KeyError, TypeError, ValueError) as error:
            raise ValueError(f"官方來源第{index}筆格式錯誤") from error
        if not (re.fullmatch(r"\d{4}-\d{2}-\d{2}", date) and
                2 <= price <= 300 and area > 0 and name and district):
            raise ValueError(f"官方來源第{index}筆欄位異常")
        lookup[(district, name)].append(row)
    data = []
    for project in baseline:
        key = (str(project["district"]).strip(), normalize_name(project["projectName"]))
        found = lookup.get(key, [])
        ordered = sorted(found, key=lambda t: t["date"], reverse=True)
        prices = [float(t["unitPriceWanPing"]) for t in found]
        # Exact raw-row fingerprint: the moment the candidate transactions change,
        # a previous human approval must not authorize the new price range.
        fingerprint_rows = sorted(
            [{"date": t["date"], "district": t["district"], "projectName": t["projectName"],
              "areaPing": t["areaPing"], "unitPriceWanPing": t["unitPriceWanPing"]}
             for t in found],
            key=lambda x: (x["date"], x["district"], x["projectName"],
                           x["areaPing"], x["unitPriceWanPing"]))
        fingerprint = hashlib.sha256(
            json.dumps(fingerprint_rows, ensure_ascii=False, sort_keys=True,
                       separators=(",", ":")).encode("utf-8")).hexdigest() if found else None
        data.append({
            "projectId": str(project["projectId"]),
            "projectName": project["projectName"],
            "district": project["district"],
            "matchLevel": "name_district_exact" if found else "not_matched",
            "unverifiedAlias": None,
            "evidenceUrl": None,
            "transactionCount": len(found),
            "exactTransactionCount": len(found),
            "transactionFingerprint": fingerprint,
            "aliasCandidateTransactionCount": 0,
            "latestTransactionDate": ordered[0]["date"] if found else None,
            "latestUnitPriceWanPing": ordered[0]["unitPriceWanPing"] if found else None,
            "averageUnitPriceWanPing": round(sum(prices) / len(prices), 2) if found else None,
            "minUnitPriceWanPing": min(prices) if found else None,
            "maxUnitPriceWanPing": max(prices) if found else None,
            "readyForPublish": False,
        })
    return {
        "schemaVersion": 1,
        "source": "data/project_transactions.json",
        "sourceDataUpdatedAt": market.get("updatedAt"),
        "coverageMonths": market.get("availableMonths", []),
        "transactionSampleSize": len(transactions),
        "originalProjects": len(data),
        "summary": {
            "projectsWithDirectMatches": sum(bool(x["transactionCount"]) for x in data),
            "transactionsMatched": sum(x["transactionCount"] for x in data),
            "projectsWithoutMatchedRecord": sum(not x["transactionCount"] for x in data),
        },
        "warnings": [
            "本檔只有名稱及行政區初步匹配，並非已核實建案成交。",
            "未配對不等於0成交；資料覆蓋非完整市場全集。",
            "不得直接以此檔更新公開網站價格；交由安全匯出器根據正式核准證據遮蔽。",
        ],
        "projects": data,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--register", type=Path, default=REGISTER)
    parser.add_argument("--transactions", type=Path, default=TRANSACTIONS)
    parser.add_argument("--market", type=Path, default=MARKET)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    result = recalculate(
        json.loads(args.register.read_text(encoding="utf-8")),
        json.loads(args.transactions.read_text(encoding="utf-8")),
        json.loads(args.market.read_text(encoding="utf-8")),
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("建案=%s 直接候選=%s 未匹配=%s 样本=%s" % (
        result["originalProjects"], result["summary"]["projectsWithDirectMatches"],
        result["summary"]["projectsWithoutMatchedRecord"], result["transactionSampleSize"]))


if __name__ == "__main__":
    main()
