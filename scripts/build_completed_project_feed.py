#!/usr/bin/env python3
"""Publish traceable current-year recent-home samples at reviewed exact addresses.

Community identity does not establish developer first-sale. Unknown or unreviewed
addresses, unverifiable transactions and homes older than five years stay masked.
"""
import argparse
import csv
import hashlib
import json
import math
from collections import Counter
from datetime import date
from pathlib import Path

from audit_community_mapping import address_key
from build_completed_home_review import build_review, MOI
from build_presale_project_feed import PRICE_FIELDS

ROOT = Path(__file__).resolve().parents[1]
RULE = 'reviewed_exact_address_recent_completed_v1'


def build_feed(registry, transactions, mapping_rows, market, *, year_only=True, recent_only=True):
    projects = registry['projects']
    if len(projects) != 386 or len({str(p['projectId']) for p in projects}) != 386:
        raise ValueError('建案名單必須有386筆唯一ID')
    identities = [(p['district'], p['name']) for p in projects]
    if len(set(identities)) != len(identities):
        raise ValueError('建案名稱及行政區重複')
    year = market['year']
    updated = date.fromisoformat(market['updatedAt'])
    if updated.year != year:
        raise ValueError('行情年度與更新日期不一致')
    review = build_review(registry, transactions, mapping_rows, year)
    cases = {p['projectId']: p for p in review['cases']}
    if any(p['status'] == 'address_conflict' and p['candidateAddresses'] for p in review['cases']):
        raise ValueError('成屋門牌存在建案歸屬衝突')
    identities = Counter(t.get('officialTransactionId') for t in transactions.values()
                         if t.get('officialTransactionId'))
    entries, audit = [], []
    for project in projects:
        pid = str(project['projectId'])
        case = cases.get(pid)
        addresses = case['verifiedAddresses'] if case and case['communityMappingVerified'] else []
        sources = sorted({r['source'] for r in mapping_rows if
                          r['district'] == project['district'] and r['buildingName'] == project['name'] and
                          address_key(r['addressKey'], project['district']) in addresses})
        found = [{'transactionId': tid} for tid, tx in transactions.items()
                 if project['kind'] == '成屋' and tx.get('category') == '成屋' and
                 tx.get('district') == project['district'] and
                 address_key(tx.get('addressKey'), project['district']) in addresses and
                 (not year_only or str(tx.get('date', '')).startswith(str(year) + '-'))]
        accepted, reasons = [], set()
        for evidence in found:
            tid = evidence['transactionId']
            tx = transactions[tid]
            official_id = tx.get('officialTransactionId')
            location = tx.get('officialLocation')
            if not (isinstance(official_id, str) and official_id.strip() and
                    identities[official_id] == 1 and
                    tid == hashlib.sha256(('成屋:' + official_id).encode()).hexdigest()[:24] and
                    tx.get('officialSource') == MOI and isinstance(location, str) and
                    location.startswith('高雄市' + project['district']) and
                    address_key(location, project['district']) == address_key(tx.get('addressKey'), project['district']) and
                    address_key(location, project['district']) in addresses):
                reasons.add('missing_or_conflicting_official_provenance')
                continue
            if recent_only:
                try:
                    age = (date.fromisoformat(tx['date']) - date.fromisoformat(tx['completedAt'])).days
                except (KeyError, TypeError, ValueError):
                    age = -1
                if not 0 <= age <= 5 * 365.2425:
                    reasons.add('not_within_five_years_or_unknown_completion')
                    continue
            try:
                traded = date.fromisoformat(tx['date'])
                unit_price = float(tx['unitPriceM2']) * 3.305785 / 10000
                area = float(tx['areaM2'])
            except (KeyError, TypeError, ValueError):
                reasons.add('invalid_transaction_values')
                continue
            if ((year_only and traded.year != year) or traded > updated or not math.isfinite(unit_price) or
                    not math.isfinite(area) or not 2 <= unit_price <= 300 or area <= 0):
                reasons.add('invalid_transaction_values')
                continue
            accepted.append({'id': tid, 'date': tx['date'], 'price': round(unit_price, 2),
                             'location': location, 'officialId': official_id})
        entry = {'projectId': pid, 'name': project['name'], 'district': project['district'],
                 'registryKind': project['kind'], 'registryAddress': project.get('address'),
                 'transactionKind': '成屋', 'ageCategory': '5年內成屋' if recent_only else '成屋', 'year': year,
                 'priceStatus': 'community_address_matched' if accepted else 'pending_verification',
                 'matchingRule': RULE if accepted else None,
                 'verifiedAddresses': addresses, 'addressEvidenceSources': sources,
                 'sourceRecordCount': len(found), 'excludedRecordCount': len(found) - len(accepted),
                 'officialIdentityCount': len(accepted),
                 'officialLocations': sorted({t['location'] for t in accepted}),
                 'verificationSource': MOI if accepted else None,
                 'verifiedOn': None, 'reviewedBy': None, 'developerFirstSaleVerified': False,
                 **{field: None for field in PRICE_FIELDS}}
        if accepted:
            accepted.sort(key=lambda t: (t['date'], t['id']), reverse=True)
            prices = [t['price'] for t in accepted]
            latest = accepted[0]['date']
            day_prices = [t['price'] for t in accepted if t['date'] == latest]
            entry.update(transactionCount=len(accepted), latestTransactionDate=latest,
                         latestUnitPriceWanPing=accepted[0]['price'],
                         averageUnitPriceWanPing=round(sum(prices) / len(prices), 2),
                         minUnitPriceWanPing=min(prices), maxUnitPriceWanPing=max(prices),
                         latestDayMinUnitPriceWanPing=min(day_prices), latestDayMaxUnitPriceWanPing=max(day_prices))
        entries.append(entry)
        audit.append({'projectId': pid, 'sourceRecords': len(found), 'includedRecords': len(accepted),
                      'reasons': sorted(reasons) or ([] if accepted else ['no_traceable_recent_reviewed_sample'])})
    return {'schemaVersion': 1, 'feedKind': 'public_official_completed', 'year': year,
            'source': MOI, 'sourceDataUpdatedAt': updated.isoformat(), 'totalProjects': 386,
            'matchedPriceProjects': sum(e['priceStatus'] == 'community_address_matched' for e in entries),
            'includedTransactionCount': sum(e['transactionCount'] or 0 for e in entries),
            'importantNotes': [('只含今年成交。' if year_only else '累計已收錄的歷年成交。') +
                              ('成交時屋齡限5年內。' if recent_only else '社區成交不限制屋齡。') +
                              '僅限已核對門牌且具官方追溯的樣本。',
                               '社區歸屬不等於建商首售，也不代表全案所有門牌或全部成交。',
                               '無樣本不是零成交；低於11層華廈不在原始市場收錄範圍。',
                               '平均單價採逐筆單價算術平均；每次官方資料更新後依相同條件重建。'],
            'projects': entries}, {'sourceDataUpdatedAt': updated.isoformat(), 'projects': audit}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--all-time', action='store_true',
                        help='建立歷年成屋行情；仍只納入已核對門牌及可追溯官方成交')
    args = parser.parse_args()
    with (ROOT / 'data/community_mapping.csv').open(encoding='utf-8-sig', newline='') as f:
        mapping = list(csv.DictReader(f))
    transaction_path = ROOT / ('data/project_history_transactions.json' if args.all_time else 'data/transactions.json')
    if args.all_time and not transaction_path.exists():
        raise SystemExit('缺少歷年官方交易資料，不能產生歷年行情')
    feed, audit = build_feed(
        json.loads((ROOT / 'data/project_identity_register.json').read_text()),
        json.loads(transaction_path.read_text()), mapping,
        json.loads((ROOT / 'market.json').read_text()),
        year_only=not args.all_time, recent_only=not args.all_time)
    if args.all_time:
        feed['feedKind'] = 'public_official_completed_all_time'
        feed['year'] = None
        feed['period'] = 'all_time'
        feed['importantNotes'][0] = '累計已收錄的歷年成屋成交，不限成交年度與屋齡；僅限已核對門牌且具官方追溯的樣本。'
        feed_path = ROOT / 'data/official_completed_all_time_project_feed.json'
        audit_path = ROOT / 'analysis/official-completed-all-time-match-audit.json'
        label = '歷年成屋'
    else:
        feed_path = ROOT / 'data/official_completed_project_feed.json'
        audit_path = ROOT / 'analysis/official-completed-match-audit.json'
        label = '五年內今年成屋'
    for path, data in ((feed_path, feed), (audit_path, audit)):
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(f"{label}可顯示={feed['matchedPriceProjects']} 可追溯成交={feed['includedTransactionCount']}")


if __name__ == '__main__':
    main()
