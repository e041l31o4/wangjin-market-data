#!/usr/bin/env python3
"""Backfill official release quarters and publish year/all project samples.

Keep only reviewed project identities, preserve official transaction IDs, and
never interpret a downloaded release quarter as a transaction-year census.
"""
import csv
import hashlib
import json
from datetime import date
from pathlib import Path

from audit_community_mapping import address_key
from build_presale_project_feed import build_feed as presale_feed, identity_name, reviewed_alias_lookup
from build_completed_project_feed import build_feed as completed_feed
from update_market import extract, download_zip, SEASON_URL, CURRENT, merge_records, PING_M2

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / 'data/project_history_transactions.json'
MANIFEST = ROOT / 'analysis/project-history-coverage.json'
OUTPUT = ROOT / 'data/official_project_periods.json'


def release_quarters(today):
    # The official archive starts with the 2012 registration scheme. The
    # current release is fetched separately; do not label an unpublished
    # current quarter as a missing historical release.
    current = (today.month - 1) // 3 + 1
    return [f'{year - 1911}S{quarter}' for year in range(today.year, 2011, -1)
            for quarter in range(4, 0, -1)
            if (year, quarter) >= (2012, 3) and (year, quarter) < (today.year, current)]


def target_filter(registry, mapping, aliases):
    presales = {(p['district'], identity_name(p['name'])) for p in registry['projects'] if p['kind'] == '預售屋'}
    alias_keys = set(reviewed_alias_lookup(registry['projects'], aliases))
    addresses = {(r['district'], address_key(r['addressKey'], r['district'])) for r in mapping}
    def wanted(tx):
        if tx.get('category') == '成屋':
            return (tx.get('district'), address_key(tx.get('addressKey'), tx.get('district', ''))) in addresses
        return (tx.get('district'), identity_name(tx.get('projectName'))) in presales | alias_keys
    return wanted


def build_periods(registry, transactions, mapping, market, aliases, coverage):
    def presale_rows(rows):
        return [{'transactionId': tid, 'date': tx['date'], 'district': tx['district'],
                 'projectName': tx['projectName'], 'officialTransactionId': tx.get('officialTransactionId'),
                 'officialLocation': tx.get('officialLocation'), 'officialSource': tx.get('officialSource'),
                 'unitPriceWanPing': round(tx['unitPriceM2'] * PING_M2 / 10000, 2),
                 'areaPing': round(tx['areaM2'] / PING_M2, 2)}
                for tid, tx in rows.items() if tx.get('category') == '預售屋' and tx.get('projectName')]
    periods = {}
    for period in ('year', 'all'):
        selected = {tid: tx for tid, tx in transactions.items() if
                    period == 'all' or tx.get('date', '').startswith(str(market['year']) + '-')}
        presale, _ = presale_feed(registry, presale_rows(selected), market, aliases)
        completed, _ = completed_feed(registry, selected, mapping, market,
                                      year_only=period == 'year', recent_only=False)
        periods[period] = {'presale': presale, 'completed': completed}
    return {'schemaVersion': 1, 'feedKind': 'public_official_project_periods',
            'year': market['year'], 'sourceDataUpdatedAt': market['updatedAt'],
            'coverage': coverage, 'periods': periods}


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, separators=(',', ':')) + '\n', encoding='utf-8')


def main():
    registry_path = ROOT / 'data/project_identity_register.json'
    aliases_path = ROOT / 'data/approved_project_aliases.json'
    mapping_path = ROOT / 'data/community_mapping.csv'
    registry = json.loads(registry_path.read_text())
    aliases = json.loads(aliases_path.read_text())
    with mapping_path.open(encoding='utf-8-sig', newline='') as f:
        mapping = list(csv.DictReader(f))
    market = json.loads((ROOT / 'market.json').read_text())
    today = date.fromisoformat(market['updatedAt'])
    fingerprint = hashlib.sha256(b''.join(p.read_bytes() for p in
                                (registry_path, aliases_path, mapping_path))).hexdigest()
    previous = json.loads(MANIFEST.read_text()) if MANIFEST.exists() else {}
    imported = set(previous.get('importedReleaseQuarters', [])) if previous.get('targetFingerprint') == fingerprint else set()
    wanted = target_filter(registry, mapping, aliases)
    existing = json.loads(DB.read_text()) if DB.exists() else {}
    existing = {tid: tx for tid, tx in existing.items() if wanted(tx)}
    # Include retained current-release records (including updates between
    # quarterly snapshots); their provenance is still checked by the feeds.
    merge_records(existing, {tid: tx for tid, tx in json.loads((ROOT / 'data/transactions.json').read_text()).items() if wanted(tx)})
    targets = release_quarters(today)
    failed = {}
    for season in targets:
        if season in imported:
            continue
        try:
            batch = extract(download_zip(SEASON_URL.format(season=season)), year=None, require_presale=False)
            chosen = {tid: tx for tid, tx in batch.items() if wanted(tx)}
            # We read newest to oldest: a stale quarterly row must not
            # overwrite a retained newer correction of the same official ID.
            for tid, tx in chosen.items():
                if tid not in existing:
                    existing[tid] = tx
                else:
                    for field in ('officialTransactionId', 'officialLocation', 'officialSource', 'completedAt'):
                        if not existing[tid].get(field) and tx.get(field):
                            existing[tid][field] = tx[field]
            imported.add(season)
            print(f'歷史建案 {season}: 官方符合市場條件={len(batch)}；目標建案={len(chosen)}；累積={len(existing)}', flush=True)
            write(DB, existing)
        except Exception as exc:
            failed[season] = str(exc)[:300]
            print(f'歷史建案 {season} 未完成，下次重試：{exc}', flush=True)
    current_ok = False
    try:
        batch = extract(download_zip(CURRENT), year=None, require_presale=False)
        merge_records(existing, {tid: tx for tid, tx in batch.items() if wanted(tx)})
        current_ok = True
    except Exception as exc:
        failed['current'] = str(exc)[:300]
    coverage = {'targetFingerprint': fingerprint, 'requestedFrom': '2012-08-01',
                'targetReleaseQuarters': len(targets), 'importedReleaseQuarters': sorted(imported),
                'downloadedReleaseQuarterCount': len(set(targets) & imported),
                'failedReleases': failed, 'currentReleaseFetched': current_ok,
                'historyBackfillComplete': set(targets).issubset(imported) and current_ok,
                'note': '全部為已收錄的官方可追溯樣本，並非保證全案無遺漏。舊資料可能無完整門牌或案名；發布季度不等於交易季度。'}
    feed = build_periods(registry, existing, mapping, market, aliases, coverage)
    all_time_completed, all_time_audit = completed_feed(
        registry, existing, mapping, market, year_only=False, recent_only=False)
    all_time_completed['feedKind'] = 'public_official_completed_all_time'
    all_time_completed['year'] = None
    all_time_completed['period'] = 'all_time'
    all_time_completed['importantNotes'][0] = (
        '累計已收錄的歷年成屋成交，不限成交年度與屋齡；僅限已核對門牌且具官方追溯的樣本。')
    all_time_presale = feed['periods']['all']['presale']
    all_time_presale['period'] = 'all_time'
    write(DB, existing); write(MANIFEST, coverage); write(OUTPUT, feed)
    write(ROOT / 'data/official_presale_all_time_project_feed.json', all_time_presale)
    write(ROOT / 'data/official_completed_all_time_project_feed.json', all_time_completed)
    write(ROOT / 'analysis/official-completed-all-time-match-audit.json', all_time_audit)
    for key, value in feed['periods'].items():
        print(key, {kind: (part['matchedPriceProjects'], part['includedTransactionCount'])
                    for kind, part in value.items()}, flush=True)
    print('all_time_completed', {
        'matchedPriceProjects': all_time_completed['matchedPriceProjects'],
        'includedTransactionCount': all_time_completed['includedTransactionCount'],
    }, flush=True)


if __name__ == '__main__':
    main()
