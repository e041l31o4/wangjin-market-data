import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location("market_update", Path(__file__).resolve().parents[1] / "scripts/update_market.py")
market = importlib.util.module_from_spec(spec)
spec.loader.exec_module(market)


class OfficialEvidenceTests(unittest.TestCase):
    def row(self):
        return {
            "鄉鎮市區": "楠梓區", "主要用途": "住家用",
            "建物型態": "住宅大樓(11層含以上有電梯)",
            "交易標的": "房地(土地+建物)", "交易筆棟數": "土地1建物1車位0",
            "交易年月日": "1150831", "建物移轉總面積平方公尺": "100",
            "單價元平方公尺": "100000", "編號": "TEST-OFFICIAL-1",
            "建案名稱": "測試案", "土地位置建物門牌": "高雄市楠梓區測試段123地號",
        }

    def test_official_evidence_does_not_change_price(self):
        row = self.row()
        _, result = market.normalize(row, "預售屋")
        self.assertEqual(result["officialTransactionId"], row["編號"])
        self.assertEqual(result["officialLocation"], row["土地位置建物門牌"])
        self.assertEqual(result["officialSource"], market.SOURCE)
        self.assertEqual(result["unitPriceM2"], 100000)
        self.assertIsNone(result["addressKey"])

    def test_missing_location_is_not_fabricated(self):
        row = self.row()
        del row["土地位置建物門牌"]
        _, result = market.normalize(row, "預售屋")
        self.assertIsNone(result["officialLocation"])

    def test_refresh_preserves_evidence_when_fields_missing(self):
        key, result = market.normalize(self.row(), "預售屋")
        existing = {key: result}
        incoming = dict(result, officialTransactionId=None, officialLocation=None, officialSource=None)
        market.merge_records(existing, {key: incoming})
        self.assertEqual(existing[key]["officialTransactionId"], "TEST-OFFICIAL-1")
        self.assertEqual(existing[key]["officialLocation"], self.row()["土地位置建物門牌"])

    def test_new_official_location_replaces_previous(self):
        key, result = market.normalize(self.row(), "預售屋")
        existing = {key: result}
        market.merge_records(existing, {key: dict(result, officialLocation="修正後基地")})
        self.assertEqual(existing[key]["officialLocation"], "修正後基地")


if __name__ == "__main__":
    unittest.main()
