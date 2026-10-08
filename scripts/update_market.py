#!/usr/bin/env python3
"""WANGJIN 2026 Kaohsiung housing market data, based on MOI disclosed batches.

The feed is NOT a complete census of 2026 transactions. Months without loaded
records are unknown, not zero. Backfill archived quarterly releases automatically on the first scheduled run;
subsequent runs skip successfully imported quarters.
Keep the existing records schema for the current Framer dashboard, while adding
monthlyRecords, ageMonthlyRecords, and projectRankings for the new dashboard.
"""
import argparse
import csv
import hashlib
import io
import json
from pathlib import Path
import re
import sys
import unicodedata
import time
import urllib.request
import zipfile
from collections import defaultdict
from datetime import date, datetime, timezone, timedelta

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / 'data' / 'transactions.json'
OUTPUT = ROOT / 'market.json'
COMMUNITY_MAP = ROOT / 'data' / 'community_mapping.csv'
SCHEMA_VERSION = 3
SOURCE = 'https://plvr.land.moi.gov.tw/DownloadOpenData'
CURRENT = 'https://plvr.land.moi.gov.tw/Download?type=zip&fileName=lvr_landcsv.zip'
SEASON_URL = 'https://plvr.land.moi.gov.tw/DownloadSeason?season={season}&type=zip&fileName=lvr_landcsv.zip'
# 115S1/2/3 are official *release* quarters, not necessarily transaction quarters.
# Store successfully imported seasons inside market.json, which the existing workflow already commits.
YEAR = 2026
PING_M2 = 3.305785
DISTRICTS = {'楠梓區','橋頭區','左營區','鼓山區','鳳山區','三民區','前鎮區','苓雅區',
'新興區','前金區','鹽埕區','小港區','旗津區','岡山區','仁武區','鳥松區','大寮區',
'大社區','林園區','大樹區','燕巢區','路竹區','阿蓮區','田寮區','湖內區',
'茄萣區','永安區','彌陀區','梓官區','旗山區','美濃區','六龜區','甲仙區',
'杉林區','內門區','茂林區','桃源區','那瑪夏區'}
EXCLUDE_NOTES = ('親友、員工、共有人或其他特殊關係', '特殊關係', '協議價購',
 '債權債務', '急買急賣', '瑕疵', '含增建', '含未登記建物', '地上權',
 '法拍', '拍賣', '親屬', '政府機關', '建商與地主')
UNKNOWN_AGE = '屋齡未明成屋'


def download_zip(url):
    request = urllib.request.Request(url, headers={
        'User-Agent': 'WANGJIN-Market-Data/2.0 (public research)',
        'Accept': 'application/zip,application/octet-stream,*/*'})
    for attempt in range(3):
        try:
            with urllib.request.urlopen(request, timeout=75) as response:
                content = response.read(150_000_001)
            if len(content) > 150_000_000:
                raise RuntimeError('官方下載檔超過 150 MB 安全上限')
            if not zipfile.is_zipfile(io.BytesIO(content)):
                raise RuntimeError('官方網址沒有回傳 ZIP（可能是網站維護或下載介面異動）')
            return content
        except Exception:
            if attempt == 2:
                raise
            time.sleep(3 * (attempt + 1))


def number(value):
    try:
        n = float(str(value).replace(',', '').strip())
        return n if 0 <= n < 10**12 else None
    except (TypeError, ValueError):
        return None


def parse_date(raw):
    """Convert ROC 7-digit YYYYMMDD or 5-digit YYYYMM dates to ISO.

    A month-only building completion date is assigned day 1 for internal
    calculation; borderline 5-year classifications are left unknown below.
    """
    s = str(raw or '').strip()
    if re.fullmatch(r'\d{7}', s):
        y, m, d = int(s[:3]) + 1911, int(s[3:5]), int(s[5:7])
    elif re.fullmatch(r'\d{5}', s):
        y, m, d = int(s[:3]) + 1911, int(s[3:5]), 1
    else:
        return None
    try:
        return date(y, m, d).isoformat()
    except ValueError:
        return None


def is_single_unit(s):
    m = re.search(r'建物(\d+)', s or '')
    return bool(m and int(m.group(1)) == 1)


def age_category(transaction_date, completed):
    if not completed:
        return UNKNOWN_AGE
    try:
        traded = date.fromisoformat(transaction_date)
        built = date.fromisoformat(completed)
        if built > traded:
            return UNKNOWN_AGE
        try:
            fifth_anniversary = built.replace(year=built.year + 5)
        except ValueError:  # February 29 -> February 28
            fifth_anniversary = built.replace(year=built.year + 5, day=28)
        return '5年內成屋' if traded <= fifth_anniversary else '中古屋'
    except (TypeError, ValueError):
        return UNKNOWN_AGE


def clean_project_name(value):
    name = re.sub(r'\s+', ' ', str(value or '')).strip()
    if name in ('', '無', '未提供', '不詳', '其他', 'NA', 'N/A', '－', '-'):
        return None
    return name[:120]


def address_key(value, district):
    """Extract a *building-number* address for verified community mapping.

    Never use street-only matching: one road can contain multiple communities.
    No apartment floor/unit number is retained in the public JSON.
    """
    value = unicodedata.normalize('NFKC', str(value or ''))
    value = re.sub(r'(號(?:之\d+)?)\s+.*$', r'\1', value)
    value = re.sub(r'\s+', '', value).replace('臺', '台')
    value = re.sub(r'^台灣(?:省)?', '', value)
    value = re.sub(r'^高雄市', '', value)
    if value.startswith(district):
        value = value[len(district):]
    # Only match addresses with a complete building number, not a road name.
    match = re.match(r'^(.+?號(?:之\d+)?)', value)
    return match.group(1) if match else None


def load_community_mapping():
    """Optional hand-verified exact-address-to-community table.

    Columns: district,addressKey,buildingName,source. source is a human
    verification note; absence of a source means the mapping is not trusted.
    """
    if not COMMUNITY_MAP.exists():
        return {}
    result = {}
    with COMMUNITY_MAP.open('r', encoding='utf-8-sig', newline='') as file:
        reader = csv.DictReader(file)
        required = {'district', 'addressKey', 'buildingName', 'source'}
        if not required.issubset(reader.fieldnames or []):
            raise RuntimeError('community_mapping.csv 缺少欄位：district,addressKey,buildingName,source')
        for index, row in enumerate(reader, start=2):
            if not any((v or '').strip() for v in row.values() if isinstance(v, str)):
                continue
            district = (row.get('district') or '').strip()
            address = address_key(row.get('addressKey'), district)
            name = clean_project_name(row.get('buildingName'))
            source = (row.get('source') or '').strip()
            if district not in DISTRICTS or not address or not name or not source:
                raise RuntimeError(f'社區對照表第 {index} 列不完整；需區域、完整門牌、社區名稱與查證來源')
            key = (district, address)
            if key in result and result[key] != name:
                raise RuntimeError(f'社區對照表門牌重複且名稱衝突：{district} {address}')
            result[key] = name
    return result


def normalize(row, kind):
    district = (row.get('鄉鎮市區') or '').strip()
    if district not in DISTRICTS or (row.get('主要用途') or '').strip() != '住家用':
        return None
    if (row.get('建物型態') or '').strip() != '住宅大樓(11層含以上有電梯)':
        return None
    if not (row.get('交易標的') or '').startswith('房地('):
        return None
    if not is_single_unit(row.get('交易筆棟數', '')):
        return None
    note = row.get('備註', '') or ''
    if any(term in note for term in EXCLUDE_NOTES):
        return None
    if kind == '成屋' and '預售屋' in note:
        return None
    if kind == '預售屋' and (row.get('解約情形', '') or '').strip():
        return None
    traded = parse_date(row.get('交易年月日'))
    if not traded or int(traded[:4]) != YEAR:
        return None
    area = number(row.get('建物移轉總面積平方公尺'))
    unit = number(row.get('單價元平方公尺'))
    if not area or not unit:
        return None
    parking_area = number(row.get('車位移轉總面積平方公尺')) or 0
    parking_price = number(row.get('車位總價元')) or 0
    adjusted_area = area - parking_area if parking_price > 0 and parking_area < area else area
    if adjusted_area <= 0:
        return None
    if not 2 <= unit * PING_M2 / 10_000 <= 300:
        return None
    identity = (row.get('編號') or '').strip()
    if not identity:
        return None
    key = hashlib.sha256(f'{kind}:{identity}'.encode()).hexdigest()[:24]
    completed_raw = (row.get('建築完成年月') or row.get('建築完成日期') or '').strip()
    completed = parse_date(completed_raw) if kind == '成屋' else None
    completion_month_only = bool(re.fullmatch(r'\d{5}', completed_raw))
    if kind == '成屋' and completion_month_only and completed:
        # When exactly on the 5-year boundary, month-only data cannot decide.
        traded_day = date.fromisoformat(traded)
        built_day = date.fromisoformat(completed)
        if traded_day.year == built_day.year + 5 and traded_day.month == built_day.month:
            completed = None
    project = clean_project_name(row.get('建案名稱') or row.get('預售屋建案名稱')) if kind == '預售屋' else None
    # Some official datasets explicitly contain community/project names for
    # completed homes; most do not. Never invent names from the street address.
    community = (clean_project_name(row.get('社區名稱') or row.get('建案名稱'))
                 if kind == '成屋' else None)
    location = (row.get('土地位置建物門牌') or row.get('建物門牌') or row.get('門牌'))
    address = address_key(location, district) if kind == '成屋' else None
    return key, {'date': traded, 'district': district, 'category': kind,
                 'areaM2': round(adjusted_area, 4), 'unitPriceM2': round(unit, 4),
                 'completedAt': completed, 'projectName': project,
                 'addressKey': address, 'communityName': community}


def decode_csv(data):
    for encoding in ('utf-8-sig', 'cp950', 'big5'):
        try:
            return data.decode(encoding)
        except UnicodeError:
            pass
    raise RuntimeError('官方 CSV 編碼無法辨識')


def extract(archive):
    records = {}
    with zipfile.ZipFile(io.BytesIO(archive)) as z:
        names = {Path(n).name.lower(): n for n in z.namelist()}
        found = 0
        for suffix, kind in [('a', '成屋'), ('b', '預售屋')]:
            filename = f'e_lvr_land_{suffix}.csv'
            if filename not in names:
                raise RuntimeError(f'下載 ZIP 缺少高雄市檔案：{filename}')
            rows = csv.DictReader(io.StringIO(decode_csv(z.read(names[filename]))))
            if not {'鄉鎮市區', '編號'}.issubset(rows.fieldnames or []):
                raise RuntimeError(f'官方 CSV 欄位已改變：{filename}')
            for row in rows:
                found += 1
                item = normalize(row, kind)
                if item:
                    records[item[0]] = item[1]
        if found < 2:
            raise RuntimeError('官方資料筆數異常，停止更新')
    return records


def load_existing():
    if not DB.exists():
        return {}
    obj = json.loads(DB.read_text(encoding='utf-8'))
    if not isinstance(obj, dict):
        raise RuntimeError('交易資料庫格式異常')
    return {key: row for key, row in obj.items()
            if isinstance(row, dict) and str(row.get('date', '')).startswith(f'{YEAR}-')
            and row.get('category') in ('成屋', '預售屋')}


def aggregate_rows(rows, key_fn):
    groups = defaultdict(lambda: [0, 0.0, 0.0])
    for row in rows:
        key = key_fn(row)
        area = float(row['areaM2'])
        unit = float(row['unitPriceM2'])
        groups[key][0] += 1
        groups[key][1] += unit * area / 10_000
        groups[key][2] += area / PING_M2
    result = []
    for key, (count, price, area) in sorted(groups.items()):
        entry = dict(key)
        entry.update(count=count, totalPriceWan=round(price, 3),
                     totalAreaPing=round(area, 4),
                     avgUnitPriceWanPing=round(price / area, 3) if area else None)
        result.append(entry)
    return result


def build_payload(records, today, imported_seasons=None, attempted_seasons=None, community_map=None, schema_version=SCHEMA_VERSION):
    rows = [row for row in records.values()
            if row.get('date', '').startswith(f'{YEAR}-')
            and row.get('category') in ('成屋', '預售屋')]
    months = sorted({r['date'][:7] for r in rows})
    # Legacy yearly records remain to avoid breaking the existing Framer UI.
    yearly = aggregate_rows(rows, lambda r: (
        ('year', YEAR), ('district', r['district']), ('category', r['category'])))
    monthly = aggregate_rows(rows, lambda r: (
        ('month', r['date'][:7]), ('district', r['district']), ('category', r['category'])))
    aged_rows = []
    for r in rows:
        copy = dict(r)
        copy['ageCategory'] = ('預售屋' if r['category'] == '預售屋' else
                               age_category(r['date'], r.get('completedAt')))
        aged_rows.append(copy)
    age_monthly = aggregate_rows(aged_rows, lambda r: (
        ('month', r['date'][:7]), ('district', r['district']), ('category', r['ageCategory'])))
    projects = aggregate_rows([r for r in rows if r['category'] == '預售屋' and r.get('projectName')],
        lambda r: (('month', r['date'][:7]), ('district', r['district']),
                   ('projectName', r['projectName'])))
    projects.sort(key=lambda r: (r['month'], -r['count'], r['district'], r['projectName']))
    for month in months:
        rank = 0
        for project in projects:
            if project['month'] == month:
                rank += 1
                project['rankInMonth'] = rank
    # Community rankings: a transaction is counted only when its community
    # name is explicitly present in the official record or has an exact,
    # manually verified building-number address match. The '成屋' rollup and
    # age-specific rollups are alternative views, never summed together.
    community_map = community_map or {}
    named_completed = []
    total_completed = 0
    for r in rows:
        if r['category'] != '成屋':
            continue
        total_completed += 1
        name = (clean_project_name(r.get('communityName')) or
                community_map.get((r['district'], r.get('addressKey'))))
        if not name:
            continue
        for label in ('成屋', age_category(r['date'], r.get('completedAt'))):
            copy = dict(r)
            copy['buildingName'] = name
            copy['rankingCategory'] = label
            named_completed.append(copy)
    buildings = aggregate_rows(named_completed, lambda r: (
        ('month', r['date'][:7]), ('district', r['district']),
        ('category', r['rankingCategory']), ('buildingName', r['buildingName'])))
    buildings.sort(key=lambda r: (r['month'], r['category'], -r['count'],
                                  r['district'], r['buildingName']))
    identified_completed = len(named_completed) // 2
    unknown_age = sum(r['category'] == '成屋' and
                      age_category(r['date'], r.get('completedAt')) == UNKNOWN_AGE
                      for r in rows)
    return {
        'schemaVersion': schema_version,
        'updatedAt': today, 'year': YEAR,
        'period': f'{YEAR}年官方已收集揭露批次（非全年完整統計；已涵蓋交易月份：' +
                  (', '.join(months) if months else '尚無') + '）',
        'sourceLabel': '內政部不動產交易實價登錄 Open Data（高雄住宅大樓、住家用、排除部分特殊交易）',
        'sourceUrl': SOURCE,
        'coverageNote': '僅代表已收集的官方揭露資料，非全年完整成交量；未出現的月份不代表零成交。近期交易可能尚未揭露。',
        'availableMonths': months,
        'importedSeasons': sorted(imported_seasons or []),
        'attemptedSeasons': sorted(attempted_seasons or []),
        'backfillNote': '已匯入的季度為資料「發布季度」，不等於交易月份完整覆蓋；近期及補報案件可能仍未揭露。',
        'missingMonths': [f'{YEAR}-{m:02d}' for m in range(1, 13)
                          if f'{YEAR}-{m:02d}' not in months],
        'unknownAgeCount': unknown_age,
        'rankingBasis': '已揭露預售屋成交筆數，僅納入有建案名稱的案件；非實際總銷售量。',
        'projectRankingNote': '若官方批次 CSV 未提供建案名稱，排行榜會留空，不能以路名或地址冒充建案。',
        'yearMonths': {},
        'records': yearly,
        'monthlyRecords': monthly,
        'ageMonthlyRecords': age_monthly,
        'projectRankings': projects,
        'buildingRankings': buildings,
        'communityMatchCount': identified_completed,
        'communityUnmatchedCount': total_completed - identified_completed,
        'buildingRankingNote': '成屋社區排行只統計官方直接具名或經完整建物門牌人工核實的案件；未辨識社區不納入排名。',
    }


def imported_seasons_from_previous_output():
    if not OUTPUT.exists():
        return set()
    try:
        data = json.loads(OUTPUT.read_text(encoding='utf-8'))
        return {s for s in data.get('importedSeasons', [])
                if isinstance(s, str) and re.fullmatch(r'\d{3}S[1-4]', s)}
    except (OSError, ValueError, TypeError):
        return set()


def seasons_to_attempt(today):
    """Historical release quarters for 2026, including early 2027 late reports.

    Only *finished* quarters are candidates. The archive may not yet be
    published immediately after a quarter closes; that is nonfatal and is
    retried on the next scheduled run.
    """
    start_year = 2026
    final_year = min(today.year, 2027)
    result = []
    for year in range(start_year, final_year + 1):
        for quarter in range(1, 5):
            if (year, quarter * 3) >= (today.year, today.month):
                continue
            result.append(f'{year - 1911}S{quarter}')
    return result


def previous_schema_version():
    if not OUTPUT.exists():
        return 0
    try:
        obj = json.loads(OUTPUT.read_text(encoding='utf-8'))
        return int(obj.get('schemaVersion', 0))
    except (OSError, ValueError, TypeError):
        return 0


def merge_records(existing, incoming):
    for key, row in incoming.items():
        previous = existing.get(key, {})
        for field in ('completedAt', 'projectName', 'addressKey', 'communityName'):
            if not row.get(field) and previous.get(field):
                row[field] = previous[field]
        existing[key] = row


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--zip', action='append', default=[],
                        help='手動匯入官方歷史 ZIP，可重複使用')
    parser.add_argument('--zip-dir', help='從資料夾匯入所有官方 ZIP')
    parser.add_argument('--no-backfill', action='store_true',
                        help='僅更新當期，不補歷史季度')
    parser.add_argument('--refresh-history', action='store_true',
                         help='強制重新下載已匯入季度，以補齊社區名稱與門牌欄位')
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()
    today_date = datetime.now(timezone(timedelta(hours=8))).date()
    existing = load_existing()
    imported = imported_seasons_from_previous_output()
    attempted = set()
    incoming_count = 0
    # First run after this schema upgrade refreshes imported seasons to add
    # addresses/community names to transactions stored by the old script.
    refresh_history = args.refresh_history or previous_schema_version() < SCHEMA_VERSION
    refresh_failed = False
    community_map = load_community_mapping()

    zip_paths = [Path(p) for p in args.zip]
    if args.zip_dir:
        directory = Path(args.zip_dir)
        if not directory.is_dir():
            raise RuntimeError(f'找不到 ZIP 資料夾：{directory}')
        zip_paths.extend(sorted(directory.glob('*.zip')))

    for path in zip_paths:
        batch = extract(path.read_bytes())
        merge_records(existing, batch)
        incoming_count += len(batch)
        print(f'手動匯入 {path.name}：符合2026年條件 {len(batch)} 筆', flush=True)

    if not args.no_backfill:
        for season in seasons_to_attempt(today_date):
            if season in imported and not refresh_history:
                continue
            attempted.add(season)
            try:
                # Failures (e.g. 115S3 not published yet) do NOT mark imported.
                archive = download_zip(SEASON_URL.format(season=season))
                batch = extract(archive)
                # A valid archive may have zero qualifying 2026 transactions,
                # especially if it is a very early release quarter.
                merge_records(existing, batch)
                incoming_count += len(batch)
                imported.add(season)
                print(f'歷史季度 {season}：匯入符合2026年條件 {len(batch)} 筆', flush=True)
            except Exception as exc:
                refresh_failed = True
                print(f'警告：歷史季度 {season} 暫時無法匯入，未標記完成；下次會重試：{exc}',
                      file=sys.stderr, flush=True)

    # Still retrieve the current release on each scheduled run.
    # For manual ZIP-only dry-runs avoid network and keep tests reproducible.
    if not zip_paths:
        batch = extract(download_zip(CURRENT))
        merge_records(existing, batch)
        incoming_count += len(batch)
        print(f'當期：符合2026年條件 {len(batch)} 筆', flush=True)

    if not existing:
        raise RuntimeError('沒有任何符合2026年條件的紀錄，停止更新，避免清空既有資料')
    payload = build_payload(existing, today_date.isoformat(), imported, attempted,
                            community_map=community_map,
                            schema_version=(SCHEMA_VERSION if not refresh_failed else previous_schema_version()))
    if not args.dry_run:
        DB.parent.mkdir(parents=True, exist_ok=True)
        DB.write_text(json.dumps(existing, ensure_ascii=False, sort_keys=True,
                                 separators=(',', ':')) + '\n', encoding='utf-8')
        OUTPUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + '\n',
                          encoding='utf-8')
    print(f'本次讀入2026年合格筆數={incoming_count}；2026累積去重={len(existing)}；'
          f'涵蓋交易月份={len(payload["availableMonths"])}；'
          f'已匯入歷史季度={len(imported)}；'
          f'屋齡不明成屋={payload["unknownAgeCount"]}；'
          f'具名預售建案排名列數={len(payload["projectRankings"])}；'
          f'可辨識成屋社區交易={payload["communityMatchCount"]}；'
          f'未辨識成屋社區交易={payload["communityUnmatchedCount"]}；'
          f'成屋社區排名列數={len(payload["buildingRankings"])}')
    print(payload['period'])
    if args.dry_run:
        print('dry-run：未寫入任何檔案')


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print(f'更新失敗，保留原資料：{exc}', file=sys.stderr)
        sys.exit(1)
