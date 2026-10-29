#!/usr/bin/env python3
"""Export the all-time presale feed from the retained official transaction archive."""
import json
from pathlib import Path

from build_presale_project_feed import build_feed
from update_market import PING_M2

ROOT = Path(__file__).resolve().parents[1]


def main():
    registry = json.loads((ROOT / "data/project_identity_register.json").read_text(encoding="utf-8"))
    aliases = json.loads((ROOT / "data/approved_project_aliases.json").read_text(encoding="utf-8"))
    market = json.loads((ROOT / "market.json").read_text(encoding="utf-8"))
    archive_path = ROOT / "data/project_history_transactions.json"
    if not archive_path.exists():
        raise SystemExit("缺少歷年官方交易資料，停止輸出")
    archive = json.loads(archive_path.read_text(encoding="utf-8"))
    transactions = []
    for tid, tx in archive.items():
        if tx.get("category") != "預售屋" or not tx.get("projectName"):
            continue
        try:
            transactions.append({
                "transactionId": tid,
                "date": tx["date"],
                "district": tx["district"],
                "projectName": tx["projectName"],
                "officialTransactionId": tx.get("officialTransactionId"),
                "officialLocation": tx.get("officialLocation"),
                "officialSource": tx.get("officialSource"),
                "unitPriceWanPing": round(float(tx["unitPriceM2"]) * PING_M2 / 10000, 2),
                "areaPing": round(float(tx["areaM2"]) / PING_M2, 2),
            })
        except (KeyError, TypeError, ValueError):
            continue
    feed, _ = build_feed(registry, transactions, market, aliases)
    feed["period"] = "all_time"
    feed["importantNotes"][0] = (
        "累計已收錄的歷年預售成交樣本，不代表全案完整成交；僅計入官方案名／行政區或已核對別名、"
        "具官方編號與位置的可追溯交易。")
    output = ROOT / "data/official_presale_all_time_project_feed.json"
    output.write_text(json.dumps(feed, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"歷年預售可顯示={feed['matchedPriceProjects']} 可追溯成交={feed['includedTransactionCount']}")


if __name__ == "__main__":
    main()
