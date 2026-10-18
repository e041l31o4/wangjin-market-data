#!/usr/bin/env python3
"""Generate a human-review queue for WANGJIN's 386 construction projects.

This creates internal review files, not a public price feed. Missing transactions
do not mean zero sales. Do not approve a project solely from this report.
"""
import argparse
import csv
import io
import json
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REGISTER = ROOT / "analysis/project-match-candidates-20261018.json"
LATEST = ROOT / "analysis/project-pre-sale-price-latest.json"
ALIASES = ROOT / "analysis/project-alias-review-20261018.json"
HOME_AUDIT = ROOT / "analysis/completed-home-address-audit-20261018.json"
DEFAULT_CSV = ROOT / "analysis/project-review-queue.csv"
DEFAULT_MD = ROOT / "analysis/project-review-summary.md"

COLUMNS = [
    "projectId", "projectName", "district", "priority", "reviewStatus",
    "candidateTransactionCount", "latestTransactionDate",
    "candidateAliases", "completedHomeAddressCandidates",
    "nextVerification", "sourceDataUpdatedAt",
]


def make_queue(register, latest, alias_review, home_audit):
    baseline = register["projects"]
    newest = latest["projects"]
    if len(baseline) != 386 or len(newest) != 386:
        raise ValueError("兩份建案資料必須各為386筆")
    base_by_id = {str(x["projectId"]): x for x in baseline}
    latest_by_id = {str(x["projectId"]): x for x in newest}
    if len(base_by_id) != 386 or set(base_by_id) != set(latest_by_id):
        raise ValueError("建案ID新增、重複或遺漏，停止產生報告")
    alias_by_id = {}
    for a in alias_review.get("candidates", []):
        key = str(a.get("candidateProjectId") or "")
        if key in base_by_id:
            alias_by_id.setdefault(key, []).append(a["transactionName"])
    home_by_id = {}
    for h in home_audit.get("cases", []):
        key = str(h.get("projectId") or "")
        if key in base_by_id:
            home_by_id.setdefault(key, []).append(h.get("pdfAddress", ""))
    results = []
    for id_, base in base_by_id.items():
        rec = latest_by_id[id_]
        if base["projectName"] != rec["projectName"] or base["district"] != rec["district"]:
            raise ValueError(f"建案{id_}名稱或行政區變動，需人工核對")
        matched = int(rec.get("transactionCount", 0)) > 0
        alias = alias_by_id.get(id_, [])
        home = home_by_id.get(id_, [])
        # Prioritize actionable official presale records, then suspected aliases
        # and completed-home address verification, rather than guessing prices.
        priority = ("P1" if matched else "P2" if alias or home else "P3")
        status = ("官方成交名稱初步吻合，待核實基地" if matched else
                  "名稱別名或成屋門牌候選，待人工查證" if alias or home else
                  "現有成交樣本未匹配（不代表沒有成交）")
        next_step = ("核對官方逐筆建案基地／建照，確認交易歸屬" if matched else
                     "核對別名、基地門牌與使用執照，勿直接併入" if alias or home else
                     "補建案基地資訊並等待新實價登錄批次")
        results.append({
            "projectId": id_, "projectName": rec["projectName"],
            "district": rec["district"], "priority": priority,
            "reviewStatus": status,
            "candidateTransactionCount": rec.get("transactionCount", 0) if matched else "",
            "latestTransactionDate": rec.get("latestTransactionDate") if matched else "",
            "candidateAliases": "、".join(alias),
            "completedHomeAddressCandidates": "、".join(home),
            "nextVerification": next_step,
            "sourceDataUpdatedAt": latest.get("sourceDataUpdatedAt") or "",
        })
    results.sort(key=lambda x: (
        {"P1": 0, "P2": 1, "P3": 2}[x["priority"]],
        -(int(x["candidateTransactionCount"] or 0)),
        x["district"], x["projectId"]))
    return results


def summary_md(rows, latest):
    counts = Counter(r["priority"] for r in rows)
    dates = [r["latestTransactionDate"] for r in rows if r["latestTransactionDate"]]
    lines = [
        "# WANGJIN 386案／實價登錄資料核對工作佇列",
        "",
        f"- 官方資料庫同步標記：{latest.get('sourceDataUpdatedAt') or '未知'}",
        f"- 現有樣本最新匹配成交日：{max(dates) if dates else '尚無'}",
        f"- 建案清單：{len(rows)}",
        f"- P1 名稱及行政區初步匹配（優先核實）：{counts['P1']}",
        f"- P2 別名或成屋門牌候選：{counts['P2']}",
        f"- P3 目前無可比對逐筆資料：{counts['P3']}",
        "",
        "**警告：這是內部稽核佇列，不是成交行情；沒有匹配不等於零成交。**",
        "P1 也未獲得成交歸屬核准，不得直接發布任何均價或筆數。",
        "",
        "## 優先處理的前20案（名稱初步匹配）",
        "",
        "| 建案 | 行政區 | 候選筆數 | 最新樣本成交日 |",
        "| --- | --- | ---: | --- |",
    ]
    for row in [r for r in rows if r["priority"] == "P1"][:20]:
        lines.append(f"| {row['projectName']} | {row['district']} | {row['candidateTransactionCount']} | {row['latestTransactionDate']} |")
    lines.extend(["", "## 下一步", "",
                  "以官方可追溯的建案基地及逐筆交易資訊核實P1，P2逐案處理別名／門牌衝突；只在審核完成後另行更新可公開價格資料。",
                  ""])
    return "\n".join(lines)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--register", type=Path, default=REGISTER)
    p.add_argument("--latest", type=Path, default=LATEST)
    p.add_argument("--aliases", type=Path, default=ALIASES)
    p.add_argument("--completed", type=Path, default=HOME_AUDIT)
    p.add_argument("--csv", type=Path, default=DEFAULT_CSV)
    p.add_argument("--markdown", type=Path, default=DEFAULT_MD)
    args = p.parse_args()
    latest = json.loads(args.latest.read_text(encoding="utf-8"))
    rows = make_queue(
        json.loads(args.register.read_text(encoding="utf-8")), latest,
        json.loads(args.aliases.read_text(encoding="utf-8")),
        json.loads(args.completed.read_text(encoding="utf-8")))
    args.csv.parent.mkdir(parents=True, exist_ok=True)
    with args.csv.open("w", encoding="utf-8-sig", newline="") as out:
        writer = csv.DictWriter(out, COLUMNS)
        writer.writeheader()
        writer.writerows(rows)
    args.markdown.parent.mkdir(parents=True, exist_ok=True)
    args.markdown.write_text(summary_md(rows, latest), encoding="utf-8")
    counts = Counter(x["priority"] for x in rows)
    print(f"建案386筆，P1={counts['P1']} P2={counts['P2']} P3={counts['P3']}")


if __name__ == "__main__":
    main()
