import hashlib
import importlib.util
import unittest
from pathlib import Path

spec = importlib.util.spec_from_file_location("presale", Path(__file__).resolve().parents[1] / "scripts/build_presale_project_feed.py")
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


def registry():
    return {"projects": [
        {"projectId": str(i), "name": "以學 MDT" if i == 1 else f"建案{i}",
         "district": "楠梓區", "kind": "成屋" if i == 2 else "預售屋", "address": "大學西路"}
        for i in range(1, 387)
    ]}


def transaction(official_id="ONE", **changes):
    row = {
        "date": "2026-08-01", "district": "楠梓區", "projectName": "以學MDT",
        "areaPing": 30, "unitPriceWanPing": 40,
        "officialTransactionId": official_id,
        "transactionId": hashlib.sha256(("預售屋:" + official_id).encode()).hexdigest()[:24],
        "officialLocation": "高雄市楠梓區大學西路", "officialSource": m.MOI,
    }
    return {**row, **changes}


class ReviewedAliasTests(unittest.TestCase):
    def aliases(self, **changes):
        return {"aliases": [{"projectId": "1", "projectName": "以學 MDT", "district": "楠梓區",
                              "officialName": "華友聯以學MDT", "officialLocation": "高雄市楠梓區大學西路",
                              "primaryEvidence": ["https://example.com/official"], **changes}]}

    def build(self, records, aliases=None):
        return m.build_feed(registry(), records, {"updatedAt": "2026-10-18"}, aliases or self.aliases())

    def test_reviewed_alias_accepts_new_records_at_fixed_location(self):
        records = [transaction(projectName="華友聯以學MDT"), transaction("TWO", projectName="華友聯以學MDT")]
        f, audit = self.build(records)
        self.assertEqual(f["includedTransactionCount"], 2)
        self.assertEqual(f["projects"][0]["approvedAliasNames"], ["華友聯以學MDT"])

    def test_changed_location_holds_alias(self):
        f, audit = self.build([transaction(projectName="華友聯以學MDT", officialLocation="高雄市楠梓區藍田路")])
        self.assertEqual(f["includedTransactionCount"], 0)
        self.assertIn("reviewed_alias_location_changed", audit["projects"][0]["reasons"])

    def test_only_explicit_floor_suffix_is_normalized(self):
        f, _ = self.build([transaction(projectName="華友聯以學MDT", officialLocation="高雄市楠梓區大學西路A09棟F07樓"),
                           transaction("TWO", projectName="華友聯以學MDT", officialLocation="高雄市楠梓區大學西路B01棟F12樓")])
        self.assertEqual(f["includedTransactionCount"], 2)
        self.assertEqual(f["projects"][0]["officialLocations"], ["高雄市楠梓區大學西路"])
        for value in ("高雄市楠梓區大學西路12號", "高雄市楠梓區大學西路12地號", "高雄市楠梓區大學西路A09棟地下1樓"):
            self.assertEqual(m.official_base_location(value), value)
        self.assertEqual(
            m.official_base_location("高雄市楠梓區後勁北路、後勁東路"),
            m.official_base_location("高雄市楠梓區後勁東路、後勁北路"),
        )
        self.assertEqual(
            m.official_base_location("高雄市左營區新下街VS群英街"),
            m.official_base_location("高雄市左營區群英街、新下街"),
        )

    def test_alias_evidence_and_registry_ownership_required(self):
        for changes in ({"primaryEvidence": []}, {"projectName": "另案"}, {"officialName": "建案2"}, {"projectId": "2"}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.build([], self.aliases(**changes))

    def test_alias_location_conflict_holds_exact_name_records_too(self):
        f, _ = self.build([transaction(), transaction("TWO", projectName="華友聯以學MDT", officialLocation="高雄市楠梓區藍田路")])
        self.assertEqual(f["includedTransactionCount"], 0)


class RecurringPresaleTests(unittest.TestCase):
    def build(self, records, reg=None):
        return m.build_feed(reg or registry(), records, {"updatedAt": "2026-10-18"})[0]

    def test_new_transaction_updates_without_reapproval(self):
        old = self.build([transaction()])
        new = self.build([transaction(), transaction("TWO", date="2026-08-02", unitPriceWanPing=44)])
        self.assertEqual(old["projects"][0]["transactionCount"], 1)
        self.assertEqual(new["projects"][0]["transactionCount"], 2)
        self.assertEqual(new["projects"][0]["averageUnitPriceWanPing"], 42)
        self.assertEqual(new["projects"][0]["priceStatus"], "official_name_matched")
        self.assertIsNone(new["projects"][0]["reviewedBy"])

    def test_wrong_district_prefix_and_phase_are_not_aliases(self):
        records = [
            transaction("A", district="橋頭區", officialLocation="高雄市橋頭區大學西路"),
            transaction("B", projectName="華友聯以學MDT"),
            transaction("C", projectName="以學MDT2"),
        ]
        self.assertEqual(self.build(records)["matchedPriceProjects"], 0)

    def test_completed_home_not_published_as_presale_or_first_sale(self):
        result = self.build([transaction(projectName="建案2")])
        self.assertEqual(result["matchedPriceProjects"], 0)
        self.assertIsNone(result["projects"][1]["transactionCount"])

    def test_completed_home_can_show_historical_presale_without_claiming_first_sale(self):
        feed, audit = m.build_feed(
            registry(), [transaction(projectName="建案2")], {"updatedAt": "2026-10-18"},
            include_completed_history=True,
        )
        row = feed["projects"][1]
        self.assertEqual(row["transactionKind"], "預售屋")
        self.assertEqual(row["registryKind"], "成屋")
        self.assertEqual(row["transactionCount"], 1)
        self.assertFalse(row["developerFirstSaleVerified"])
        self.assertEqual(feed["includedTransactionCount"], 1)

    def test_missing_provenance_excluded_and_count_disclosed(self):
        result = self.build([transaction(), transaction("TWO", officialSource=None)])
        row = result["projects"][0]
        self.assertEqual(row["transactionCount"], 1)
        self.assertEqual(row["sourceRecordCount"], 2)
        self.assertEqual(row["excludedRecordCount"], 1)

    def test_duplicate_official_ids_never_inflate_count(self):
        result = self.build([transaction(), transaction(unitPriceWanPing=44)])
        self.assertEqual(result["includedTransactionCount"], 0)

    def test_conflicting_official_locations_hold_entire_group(self):
        result = self.build([transaction(), transaction("TWO", officialLocation="高雄市楠梓區藍田路")])
        self.assertEqual(result["matchedPriceProjects"], 0)

    def test_invalid_provenance_location_does_not_poison_valid_samples(self):
        result = self.build([
            transaction(),
            transaction("TWO", officialSource=None, officialLocation="高雄市楠梓區藍田路"),
        ])
        self.assertEqual(result["projects"][0]["transactionCount"], 1)
        self.assertEqual(result["includedTransactionCount"], 1)

    def test_registry_collision_and_unreadable_name_block_matching(self):
        reg = registry()
        reg["projects"][1]["name"] = "以學MDT"
        with self.assertRaises(ValueError):
            self.build([], reg)
        reg = registry()
        reg["projects"][0]["name"] = "以?MDT"
        self.assertEqual(self.build([transaction(projectName="以?MDT")], reg)["matchedPriceProjects"], 0)

    def test_tampered_identity_invalid_price_and_future_date_excluded(self):
        for change in (
            {"transactionId": "a"*24}, {"unitPriceWanPing": float("nan")},
            {"unitPriceWanPing": 0}, {"date": "2026-10-19"},
            {"officialLocation": "高雄市橋頭區大學西路"},
        ):
            with self.subTest(change=change):
                self.assertEqual(self.build([transaction(**change)])["matchedPriceProjects"], 0)

    def test_same_day_range_and_input_order_are_deterministic(self):
        records = [transaction(), transaction("TWO", unitPriceWanPing=44)]
        a, b = self.build(records), self.build(list(reversed(records)))
        self.assertEqual(a, b)
        self.assertEqual(a["projects"][0]["latestDayMinUnitPriceWanPing"], 40)
        self.assertEqual(a["projects"][0]["latestDayMaxUnitPriceWanPing"], 44)

    def test_pending_rows_never_leak_values(self):
        f = self.build([transaction("BAD", officialSource=None)])
        for row in f["projects"]:
            self.assertTrue(all(row[field] is None for field in m.PRICE_FIELDS))


if __name__ == "__main__":
    unittest.main()

