import csv
import hashlib
import io
import sys
from datetime import date
from pathlib import Path
import unittest
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import test_build_completed_project_feed as completed_fixtures
from update_project_history import build_periods, release_quarters
from update_market import normalize, extract, SOURCE


class ProjectPeriodTests(unittest.TestCase):
    def test_year_and_all_presale_and_completed_are_separate_not_added_together(self):
        fixture = completed_fixtures.CompletedPriceFeedTests(); fixture.setUp()
        tx = fixture.transactions
        tx.update([fixture.transaction('historical', date='2025-08-01', unitPriceM2=80000),
                   fixture.transaction('older-home', completedAt='2010-01-01')])
        for oid, traded in [('pre-new', '2026-06-01'), ('pre-old', '2024-06-01')]:
            tid = hashlib.sha256(('預售屋:' + oid).encode()).hexdigest()[:24]
            tx[tid] = {'category': '預售屋', 'district': '楠梓區', 'projectName': '社區2',
                       'date': traded, 'areaM2': 100, 'unitPriceM2': 100000,
                       'officialTransactionId': oid, 'officialLocation': '高雄市楠梓區其他路',
                       'officialSource': SOURCE}
        feed = build_periods(fixture.registry, tx, fixture.mapping, fixture.market, {},
                             {'historyBackfillComplete': False})
        self.assertEqual(feed['periods']['year']['completed']['includedTransactionCount'], 2)
        self.assertEqual(feed['periods']['all']['completed']['includedTransactionCount'], 3)
        self.assertEqual(feed['periods']['year']['presale']['includedTransactionCount'], 1)
        self.assertEqual(feed['periods']['all']['presale']['includedTransactionCount'], 2)
        self.assertFalse(feed['coverage']['historyBackfillComplete'])
        self.assertFalse(feed['periods']['all']['completed']['projects'][0]['developerFirstSaleVerified'])

    def test_only_the_history_importer_accepts_prior_years_and_old_archives_without_presale(self):
        row = {'鄉鎮市區': '楠梓區', '主要用途': '住家用', '建物型態': '住宅大樓(11層含以上有電梯)',
               '交易標的': '房地(土地+建物)', '交易筆棟數': '土地1建物1車位0', '備註': '',
               '交易年月日': '1140101', '建物移轉總面積平方公尺': '100', '單價元平方公尺': '100000',
               '車位移轉總面積平方公尺': '0', '車位總價元': '0', '編號': 'old-1',
               '土地位置建物門牌': '高雄市楠梓區測試路1號', '建築完成年月': '1130101'}
        self.assertIsNone(normalize(row, '成屋'))
        self.assertEqual(normalize(row, '成屋', year=None)[1]['date'], '2025-01-01')
        text = io.StringIO(); writer = csv.DictWriter(text, fieldnames=list(row))
        writer.writeheader(); writer.writerow(row); writer.writerow({**row, '編號': 'old-2'})
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, 'w') as archive: archive.writestr('e_lvr_land_a.csv', text.getvalue())
        with self.assertRaises(RuntimeError): extract(buf.getvalue())
        self.assertEqual(len(extract(buf.getvalue(), year=None, require_presale=False)), 2)

    def test_archive_coverage_starts_at_registration_scheme_and_excludes_unpublished_quarter(self):
        quarters = release_quarters(date(2026, 10, 19))
        self.assertEqual((quarters[0], quarters[-1], len(quarters)), ('115S3', '101S3', 57))
        self.assertNotIn('115S4', quarters)
