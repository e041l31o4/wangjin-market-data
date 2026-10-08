#!/usr/bin/env python3
"""WANGJIN Kaohsiung high-rise transaction summaries from MOI official CSV ZIPs.

Data limitation: public release batches are NOT a full-year transaction census.
The output always identifies the release-based coverage and never labels it a
complete year or computes year-over-year rates without verified coverage.
"""
import argparse
import csv
import hashlib
import io
import json
import os
from pathlib import Path
import re
import sys
import time
import urllib.request
import zipfile
from collections import defaultdict
from datetime import datetime, timezone, timedelta

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / 'data' / 'transactions.json'
OUTPUT = ROOT / 'market.json'
SOURCE = 'https://plvr.land.moi.gov.tw/DownloadOpenData'
CURRENT = 'https://plvr.land.moi.gov.tw/Download?type=zip&fileName=lvr_landcsv.zip'
# The archived quarterly releases are a separate optional backfill and need
# validation before claiming full-year coverage.
PING_M2 = 3.305785
DISTRICTS = {'楠梓區','橋頭區','左營區','鼓山區','鳳山區','三民區','前鎮區','苓雅區',
'新興區','前金區','鹽埕區','小港區','旗津區','岡山區','仁武區','鳥松區','大寮區',
'大社區','林園區','大樹區','燕巢區','路竹區','阿蓮區','田寮區','湖內區',
'茄萣區','永安區','彌陀區','梓官區','旗山區','美濃區','六龜區','甲仙區',
'杉林區','內門區','茂林區','桃源區','那瑪夏區'}
EXCLUDE_NOTES = ('親友、員工、共有人或其他特殊關係', '特殊關係', '協議價購',
 '債權債務', '急買急賣', '瑕疵', '含增建', '含未登記建物', '地上權',
 '法拍', '拍賣', '親屬', '政府機關', '建商與地主')


def download_zip(url):
    request = urllib.request.Request(url, headers={'User-Agent': 'WANGJIN-Market-Data/1.0 (public research)', 'Accept': 'application/zip,application/octet-stream,*/*'})
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
    s = str(raw or '').strip()
    if not re.fullmatch(r'\d{7}', s):
        return None
    year, month, day = int(s[:3]) + 1911, int(s[3:5]), int(s[5:7])
    try:
        return datetime(year, month, day).date().isoformat()
    except ValueError:
        return None


def is_single_unit(s):
    m = re.search(r'建物(\d+)', s or '')
    return bool(m and int(m.group(1)) == 1)


def normalize(row, kind):
    district = row.get('鄉鎮市區', '').strip()
    if district not in DISTRICTS or row.get('主要用途', '').strip() != '住家用':
        return None
    if row.get('建物型態', '').strip() != '住宅大樓(11層含以上有電梯)':
        return None
    if not row.get('交易標的', '').startswith('房地('):
        return None
    if not is_single_unit(row.get('交易筆棟數', '')):
        return None
    note = row.get('備註', '') or ''
    if any(term in note for term in EXCLUDE_NOTES):
        return None
    # Do not misrepresent registration of an already presold home as a new
    # contemporaneous finished-building purchase.
    if kind == '成屋' and '預售屋' in note:
        return None
    if kind == '預售屋' and (row.get('解約情形', '') or '').strip():
        return None
    date = parse_date(row.get('交易年月日'))
    if not date or not (2025 <= int(date[:4]) <= 2026):
        return None
    area = number(row.get('建物移轉總面積平方公尺'))
    unit = number(row.get('單價元平方公尺'))
    if not area or not unit or area <= 0 or unit <= 0:
        return None
    park_area = number(row.get('車位移轉總面積平方公尺')) or 0
    park_price = number(row.get('車位總價元')) or 0
    # MOI's official per-m² price excludes separately priced parking area.
    # When a parking price is supplied, weight the unit price by building
    # area excluding parking; otherwise use the official reported area.
    adjusted_area = area - park_area if park_price > 0 and park_area < area else area
    if adjusted_area <= 0:
        return None
    price_ping = unit * PING_M2 / 10_000
    # Broad plausibility guard only, not a substitute for human review.
    if not 2 <= price_ping <= 300:
        return None
    identity = (row.get('編號') or '').strip()
    if not identity:
        return None
    key = hashlib.sha256(f'{kind}:{identity}'.encode()).hexdigest()[:24]
    return key, {'date': date, 'district': district, 'category': kind,
                 'areaM2': round(adjusted_area, 4), 'unitPriceM2': round(unit, 4)}


def decode_csv(data):
    for enc in ('utf-8-sig', 'cp950', 'big5'):
        try:
            return data.decode(enc)
        except UnicodeError:
            continue
    raise RuntimeError('官方 CSV 編碼無法辨識')


def extract(archive):
    records = {}
    with zipfile.ZipFile(io.BytesIO(archive)) as z:
        names = {Path(name).name.lower(): name for name in z.namelist()}
        found = 0
        for suffix, kind in [('a', '成屋'), ('b', '預售屋')]:
            filename = f'e_lvr_land_{suffix}.csv'
            if filename not in names:
                raise RuntimeError(f'下載 ZIP 缺少高雄市檔案：{filename}')
            rows = list(csv.DictReader(io.StringIO(decode_csv(z.read(names[filename])))))
            if not rows or '鄉鎮市區' not in rows[0] or '編號' not in rows[0]:
                raise RuntimeError(f'官方 CSV 欄位已改變：{filename}')
            found += len(rows)
            for row in rows:
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
    return obj


def build_payload(records, today):
    grouped = defaultdict(lambda: [0, 0.0, 0.0])
    months = set()
    for row in records.values():
        y = int(row['date'][:4]); d = row['district']; k = row['category']
        area_ping = row['areaM2'] / PING_M2
        total_wan = row['unitPriceM2'] * row['areaM2'] / 10_000
        key = (y, d, k)
        grouped[key][0] += 1
        grouped[key][1] += total_wan
        grouped[key][2] += area_ping
        months.add(row['date'][:7])
    output = []
    for (y, d, k), (count, price, area) in sorted(grouped.items()):
        output.append({'year': y, 'district': d, 'category': k, 'count': count,
                       'totalPriceWan': round(price, 3), 'totalAreaPing': round(area, 4)})
    return {'updatedAt': today, 'period': '官方已收集批次（非全年完整統計；涵蓋交易月份：' +
            (', '.join(sorted(months)) if months else '尚無') + '）',
            'sourceLabel': '內政部不動產交易實價登錄 Open Data（住宅大樓、住家用、排除部分特殊交易）',
            'sourceUrl': SOURCE,
            # No comparable yearMonths: the published batches do not prove
            # the same completeness across years. Disable misleading YoY.
            'yearMonths': {}, 'records': output}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--zip', help='使用已下載官方 ZIP 進行初始匯入／測試')
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()
    raw = Path(args.zip).read_bytes() if args.zip else download_zip(CURRENT)
    incoming = extract(raw)
    if not incoming:
        raise RuntimeError('沒有通過住宅大樓資料檢查的交易，為安全起見停止更新')
    existing = load_existing()
    existing.update(incoming)
    today = datetime.now(timezone(timedelta(hours=8))).date().isoformat()
    payload = build_payload(existing, today)
    if not args.dry_run:
        DB.parent.mkdir(parents=True, exist_ok=True)
        DB.write_text(json.dumps(existing, ensure_ascii=False, sort_keys=True, separators=(',', ':')) + '\n', encoding='utf-8')
        OUTPUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(f'官方本批合格筆數={len(incoming)}, 累積去重筆數={len(existing)}, 統計列數={len(payload["records"])}')
    print(f'期間={payload["period"]}')
    if args.dry_run:
        print('dry-run: 未寫入任何檔案')


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print(f'更新失敗，保留原資料：{exc}', file=sys.stderr)
        sys.exit(1)
