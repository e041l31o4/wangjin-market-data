#!/usr/bin/env python3
"""Build a safe project-map feed from reviewed transaction candidates.

Writes one public-ready feed WITHOUT price figures unless a project has an explicit
human-approved, evidence-backed verification record. Candidate prices are kept
in a separate internal-review feed and never copied to the public feed.
This script does not touch market.json or the live website.
"""
import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REVIEW_PATH = ROOT / "analysis/project-pre-sale-price-review-20261018.json"
APPROVAL_PATH = ROOT / "data/verified_project_matches.json"
DEFAULT_OUTPUT = ROOT / "analysis/project-map-feed-preview.json"


def build_feed(review, approvals=None):
    approvals = approvals or {}
    rows = review["projects"]
    if len(rows) != 386 or len({str(row["projectId"]) for row in rows}) != 386:
        raise ValueError("建案基準清單應有386筆且ID唯一；來源異動須先人工驗證")
    items = []
    for row in rows:
        project_id = str(row["projectId"])
        approval = approvals.get(project_id)
        verified = (
            isinstance(approval, dict)
            and approval.get("verified") is True
            and approval.get("officialEvidence")
            and approval.get("verifiedOn")
            and approval.get("projectName") == row["projectName"]
            and approval.get("district") == row["district"]
            and row.get("matchLevel") == "name_district_exact"
            and row.get("transactionCount", 0) > 0
            and not row.get("unverifiedAlias")
        )
        # No auto-publishing based on name match, web listing, or address alone.
        entry = {
            "projectId": project_id,
            "name": row["projectName"],
            "district": row["district"],
            "transactionSource": "內政部實價登錄（目前已收錄逐筆資料）",
            "sourceDataUpdatedAt": review.get("dataUpdatedAt"),
            "priceStatus": "verified" if verified else "pending_verification",
            "transactionCount": row["transactionCount"] if verified else None,
            "latestTransactionDate": row["latestTransactionDate"] if verified else None,
            "latestUnitPriceWanPing": row["latestUnitPriceWanPing"] if verified else None,
            "averageUnitPriceWanPing": row["averageUnitPriceWanPing"] if verified else None,
            "minUnitPriceWanPing": row["minUnitPriceWanPing"] if verified else None,
            "maxUnitPriceWanPing": row["maxUnitPriceWanPing"] if verified else None,
            "verificationSource": approval.get("officialEvidence") if verified else None,
            "verifiedOn": approval.get("verifiedOn") if verified else None,
        }
        items.append(entry)
    return {
        "schemaVersion": 1,
        "feedKind": "review_only_not_live",
        "source": "內政部不動產交易實價登錄 Open Data",
        "sourceDataUpdatedAt": review.get("dataUpdatedAt"),
        "importantNotes": [
            "未顯示價格不代表無成交，而是尚未完成建案與逐筆官方資料核實。",
            "成交資料非完整市場全集，近期交易可能尚未揭露。",
            "初步別名與成屋門牌候選不可自動發布；成屋建商首售另需證據。",
            "本JSON不會自動更動現有WANGJIN網站。",
        ],
        "totalProjects": len(items),
        "verifiedPriceProjects": sum(x["priceStatus"] == "verified" for x in items),
        "projects": items,
    }


def read_approvals(path):
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("審核資料必須是以projectId為鍵的JSON物件")
    return data


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, default=REVIEW_PATH)
    parser.add_argument("--approvals", type=Path, default=APPROVAL_PATH)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    feed = build_feed(json.loads(args.source.read_text(encoding="utf-8")),
                      read_approvals(args.approvals))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(feed, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"建案={feed['totalProjects']}，已核實可顯示行情={feed['verifiedPriceProjects']}；輸出={args.output}")


if __name__ == "__main__":
    main()
