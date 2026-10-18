#!/usr/bin/env python3
"""Publish traceable current-year recent-home samples at reviewed exact addresses.

Community identity does not establish developer first-sale. Unknown or unreviewed
addresses, unverifiable transactions and homes older than five years stay masked.
"""
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


def build_feed(registry, transactions, mapping_rows, market):
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
        found = [t for t in case['transactions'] if t['communityAddressVerified']] if case else []
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
                    address_key(location, project['district']) in addresses and evidence['traceable']):
                reasons.add('missing_or_conflicting_official_provenance')
                continue
            if not evidence['withinFiveYears']:
                reasons.add('not_within_five_years_or_unknown_completion')
                continue
            try:
                traded = date.fromisoformat(tx['date'])
                unit_price = float(tx['unitPriceM2']) * 3.305785 / 10000
                area = float(tx['areaM2'])
            except (KeyError, TypeError, ValueError):
                reasons.add('invalid_transaction_values')
                continue
            if (traded.year != year or traded > updated or not math.isfinite(unit_price) or
                    not math.isfinite(area) or not 2 <= unit_price <= 300 or area <= 0):
                reasons.add('invalid_transaction_values')
                continue
            accepted.append({'id': tid, 'date': tx['date'], 'price': round(unit_price, 2),
                             'location': location, 'officialId': official_id})
        entry = {'projectId': pid, 'name': project['name'], 'district': project['district'],
                 'registryKind': project['kind'], 'registryAddress': project.get('address'),
                 'transactionKind': '成屋', 'ageCategory': '5年內成屋', 'year': year,
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
            'importantNotes': ['只含今年成交、成交時屋齡5年內且具官方追溯資料的已核對門牌樣本。',
                               '社區歸屬不等於建商首售，也不代表全案所有門牌或全部成交。',
                               '無樣本不是零成交；低於11層華廈不在原始市場收錄範圍。',
                               '平均單價採逐筆單價算術平均；每次官方資料更新後依相同條件重建。'],
            'projects': entries}, {'sourceDataUpdatedAt': updated.isoformat(), 'projects': audit}


def main():
    with (ROOT / 'data/community_mapping.csv').open(encoding='utf-8-sig', newline='') as f:
        mapping = list(csv.DictReader(f))
    feed, audit = build_feed(json.loads((ROOT / 'data/project_identity_register.json').read_text()),
                             json.loads((ROOT / 'data/transactions.json').read_text()), mapping,
                             json.loads((ROOT / 'market.json').read_text()))
    for path, data in ((ROOT / 'data/official_completed_project_feed.json', feed),
                       (ROOT / 'analysis/official-completed-match-audit.json', audit)):
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(f"成屋可顯示={feed['matchedPriceProjects']} 可追溯5年內今年成交={feed['includedTransactionCount']}")


if __name__ == '__main__':
    main()
