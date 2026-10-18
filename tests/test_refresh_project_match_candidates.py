"""Deterministic checks for recurring 386-project transaction matching."""
import importlib.util
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "refresh", ROOT / "scripts/refresh_project_match_candidates.py")
refresh = importlib.util.module_from_spec(spec)
spec.loader.exec_module(refresh)


def baseline():
    return {"projects": [{"projectId": str(i), "projectName": "以學 MDT" if i == 1 else f"建案{i}",
                          "district": "楠梓區"} for i in range(1, 387)]}


class RefreshCandidateTests(unittest.TestCase):
    def test_exact_match_not_auto_verified(self):
        records = [
            {"date": "2026-08-31", "district": "楠梓區", "projectName": "以學MDT",
             "areaPing": 30, "unitPriceWanPing": 42},
            {"date": "2026-08-01", "district": "楠梓區", "projectName": "以學 MDT",
             "areaPing": 32, "unitPriceWanPing": 40},
        ]
        data = refresh.recalculate(baseline(), records, {"updatedAt": "2026-10-18"})
        self.assertEqual(data["originalProjects"], 386)
        self.assertEqual(data["summary"]["projectsWithDirectMatches"], 1)
        self.assertEqual(data["projects"][0]["transactionCount"], 2)
        self.assertEqual(data["projects"][0]["averageUnitPriceWanPing"], 41)
        self.assertFalse(data["projects"][0]["readyForPublish"])

    def test_different_district_and_alias_not_counted(self):
        records = [
            {"date": "2026-08-31", "district": "左營區", "projectName": "以學 MDT",
             "areaPing": 30, "unitPriceWanPing": 42},
            {"date": "2026-08-31", "district": "楠梓區", "projectName": "華友聯以學 MDT",
             "areaPing": 30, "unitPriceWanPing": 42}
        ]
        data = refresh.recalculate(baseline(), records, {})
        self.assertEqual(data["summary"]["projectsWithDirectMatches"], 0)

    def test_duplicate_baseline_rejected(self):
        b = baseline()
        b["projects"][1]["projectId"] = "1"
        with self.assertRaises(ValueError):
            refresh.recalculate(b, [], {})

    def test_same_district_normalized_name_collision_rejected(self):
        b = baseline()
        b["projects"][1]["projectName"] = "以學MDT"
        with self.assertRaises(ValueError):
            refresh.recalculate(b, [], {})

    def test_bad_transaction_rejected(self):
        bad = [{"date": "2026-08-31", "district": "楠梓區", "projectName": "以學MDT",
                "areaPing": 30, "unitPriceWanPing": -5}]
        with self.assertRaises(ValueError):
            refresh.recalculate(baseline(), bad, {})


if __name__ == "__main__":
    unittest.main()
