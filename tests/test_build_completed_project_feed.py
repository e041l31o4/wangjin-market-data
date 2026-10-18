import copy
import hashlib
import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from build_completed_project_feed import build_feed, MOI, PRICE_FIELDS


class CompletedPriceFeedTests(unittest.TestCase):
    def setUp(self):
        self.registry = {'projects': [{'projectId': str(i), 'name': '社區' + str(i),
                         'district': '楠梓區', 'kind': '成屋' if i == 1 else '預售屋',
                         'address': '測試路' if i == 1 else '其他路'} for i in range(1, 387)]}
        self.mapping = [{'district': '楠梓區', 'addressKey': '測試路1號', 'buildingName': '社區1',
                         'source': 'https://example.com/reviewed-house-number'}]
        self.market = {'year': 2026, 'updatedAt': '2026-10-18'}
        self.transactions = dict([self.transaction('official-1')])

    def transaction(self, official_id, **changes):
        tid = hashlib.sha256(('成屋:' + official_id).encode()).hexdigest()[:24]
        return tid, {'date': '2026-08-01', 'category': '成屋', 'district': '楠梓區',
                     'addressKey': '測試路1號', 'completedAt': '2025-06-01',
                     'unitPriceM2': 100000, 'areaM2': 100,
                     'officialTransactionId': official_id, 'officialSource': MOI,
                     'officialLocation': '高雄市楠梓區測試路1號五樓', **changes}

    def feed(self):
        return build_feed(self.registry, self.transactions, self.mapping, self.market)[0]

    def test_current_recent_traceable_sample_and_new_transaction_update_without_reapproval(self):
        self.transactions.update([self.transaction('official-2', date='2026-09-01', unitPriceM2=120000)])
        feed = self.feed(); row = feed['projects'][0]
        self.assertEqual(feed['includedTransactionCount'], 2)
        self.assertEqual(row['latestTransactionDate'], '2026-09-01')
        self.assertEqual(row['minUnitPriceWanPing'], 33.06)
        self.assertEqual(row['maxUnitPriceWanPing'], 39.67)
        self.assertFalse(row['developerFirstSaleVerified'])

    def test_missing_proof_old_and_future_samples_excluded_and_disclosed(self):
        self.transactions.update([self.transaction('missing', officialSource=None),
                                  self.transaction('old', completedAt='2010-01-01'),
                                  self.transaction('future', date='2026-11-01')])
        row = self.feed()['projects'][0]
        self.assertEqual((row['transactionCount'], row['sourceRecordCount'], row['excludedRecordCount']), (1, 4, 3))

    def test_unreviewed_adjacent_address_and_wrong_official_house_number_never_join(self):
        self.transactions.update([self.transaction('other', addressKey='測試路3號',
                                  officialLocation='高雄市楠梓區測試路3號三樓')])
        tid = next(iter(self.transactions))
        self.transactions[tid]['officialLocation'] = '高雄市楠梓區測試路5號五樓'
        row = self.feed()['projects'][0]
        self.assertIsNone(row['transactionCount'])
        self.assertTrue(all(row[k] is None for k in PRICE_FIELDS))

    def test_tampered_hash_and_duplicate_official_identity_do_not_publish(self):
        tx = next(iter(self.transactions.values()))
        self.transactions['tampered-key'] = copy.deepcopy(tx)
        self.assertEqual(self.feed()['includedTransactionCount'], 0)
        self.transactions = {'tampered-key': tx}
        self.assertEqual(self.feed()['includedTransactionCount'], 0)

    def test_presale_records_and_registry_kind_never_become_completed_prices(self):
        for tx in self.transactions.values(): tx['category'] = '預售屋'
        self.assertEqual(self.feed()['includedTransactionCount'], 0)
        self.transactions = dict([self.transaction('official-1')])
        self.registry['projects'][0]['kind'] = '預售屋'
        self.assertEqual(self.feed()['includedTransactionCount'], 0)

    def test_shared_house_number_stops_output(self):
        self.registry['projects'][1].update(kind='成屋', address='測試路1號')
        with self.assertRaises(ValueError): self.feed()
