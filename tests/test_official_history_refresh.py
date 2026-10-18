"""A partially upgraded snapshot must actually re-read its imported archives."""
import contextlib
import copy
import io
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import update_market as updater


class OfficialHistoryRefreshTests(unittest.TestCase):
    def run_snapshot(self, missing):
        row = {'date': '2026-01-12', 'category': '成屋', 'district': '楠梓區',
               'areaM2': 100, 'unitPriceM2': 100000, 'completedAt': '2025-01-01',
               'addressKey': '測試路1號', 'officialTransactionId': 'official-1',
               'officialLocation': '高雄市楠梓區測試路1號', 'officialSource': updater.SOURCE}
        existing = {'stable-key': copy.deepcopy(row)}
        if missing:
            for field in ('officialTransactionId', 'officialLocation', 'officialSource'):
                existing['stable-key'].pop(field)
        with contextlib.ExitStack() as stack:
            stack.enter_context(patch.object(sys, 'argv', ['update_market.py', '--dry-run']))
            stack.enter_context(patch.object(updater, 'load_existing', return_value=existing))
            stack.enter_context(patch.object(updater, 'imported_seasons_from_previous_output', return_value={'115S1'}))
            stack.enter_context(patch.object(updater, 'previous_schema_version', return_value=updater.SCHEMA_VERSION))
            stack.enter_context(patch.object(updater, 'seasons_to_attempt', return_value=['115S1']))
            stack.enter_context(patch.object(updater, 'load_community_mapping', return_value={}))
            stack.enter_context(patch.object(updater, 'load_verified_first_sales', return_value={}))
            download = stack.enter_context(patch.object(updater, 'download_zip', side_effect=lambda url: url))
            stack.enter_context(patch.object(updater, 'extract', side_effect=lambda url: {'stable-key': copy.deepcopy(row)} if 'DownloadSeason' in url else {}))
            stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
            updater.main()
        return existing, [call.args[0] for call in download.call_args_list]

    def test_current_schema_with_missing_proof_reloads_archive_and_repairs_same_record(self):
        records, urls = self.run_snapshot(missing=True)
        self.assertEqual(len(records), 1)
        self.assertEqual(records['stable-key']['officialTransactionId'], 'official-1')
        self.assertEqual(urls, [updater.SEASON_URL.format(season='115S1'), updater.CURRENT])

    def test_complete_snapshot_only_fetches_current_release(self):
        records, urls = self.run_snapshot(missing=False)
        self.assertEqual(len(records), 1)
        self.assertEqual(urls, [updater.CURRENT])
