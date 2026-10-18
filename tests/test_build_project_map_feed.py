"""Safety tests for the candidate-to-map feed export."""
import importlib.util
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("project_feed", ROOT / "scripts/build_project_map_feed.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def sample():
    return {"dataUpdatedAt": "2026-10-12", "projects": [
        {"projectId": str(i), "projectName": f"建案{i}", "district": "楠梓區",
         "matchLevel": "name_district_exact" if i == 1 else "external_alias_candidate_only" if i == 2 else "not_matched",
         "transactionCount": 3 if i in (1, 2) else 0,
         "transactionFingerprint": "a"*64 if i in (1, 2) else None,
         "latestTransactionDate": "2026-08-01" if i in (1, 2) else None,
         "latestUnitPriceWanPing": 42.0 if i in (1, 2) else None,
         "averageUnitPriceWanPing": 40.0 if i in (1, 2) else None,
         "minUnitPriceWanPing": 38.0 if i in (1, 2) else None,
         "maxUnitPriceWanPing": 45.0 if i in (1, 2) else None,
         "unverifiedAlias": "建商建案" if i == 2 else None}
        for i in range(1, 387)
    ]}


class SafeFeedTests(unittest.TestCase):
    def test_default_never_exposes_candidate_prices(self):
        feed = module.build_feed(sample())
        self.assertEqual(feed["totalProjects"], 386)
        self.assertEqual(feed["verifiedPriceProjects"], 0)
        for project in feed["projects"]:
            self.assertEqual(project["priceStatus"], "pending_verification")
            for name in ("transactionCount", "latestTransactionDate", "latestUnitPriceWanPing",
                         "averageUnitPriceWanPing", "minUnitPriceWanPing", "maxUnitPriceWanPing"):
                self.assertIsNone(project[name])

    def test_alias_not_approved_by_simple_override(self):
        approvals = {"2": {"verified": True, "officialEvidence": "official record",
                           "verifiedOn": "2026-10-18", "projectName": "建案2", "district": "楠梓區"}}
        self.assertEqual(module.build_feed(sample(), approvals)["verifiedPriceProjects"], 0)

    def test_verified_exact_match_requires_all_fields(self):
        approval = {"verified": True, "officialEvidence": "https://plvr.land.moi.gov.tw/",
                    "verifiedOn": "2026-10-18", "projectName": "建案1", "district": "楠梓區",
                    "reviewedBy": "human-reviewer", "approvedTransactionCount": 3,
                    "transactionFingerprint": "a"*64}
        feed = module.build_feed(sample(), {"1": approval})
        self.assertEqual(feed["verifiedPriceProjects"], 1)
        self.assertEqual(feed["projects"][0]["latestUnitPriceWanPing"], 42)

    def test_updated_transaction_fingerprint_revokes_old_approval(self):
        approval = {"verified": True, "officialEvidence": "https://plvr.land.moi.gov.tw/",
                    "verifiedOn": "2026-10-18", "projectName": "建案1", "district": "楠梓區",
                    "reviewedBy": "human-reviewer", "approvedTransactionCount": 3,
                    "transactionFingerprint": "a"*64}
        newer = sample()
        newer["projects"][0]["transactionFingerprint"] = "b"*64
        self.assertEqual(module.build_feed(newer, {"1": approval})["verifiedPriceProjects"], 0)

    def test_duplicate_ids_fail(self):
        data = sample()
        data["projects"][1]["projectId"] = "1"
        with self.assertRaises(ValueError):
            module.build_feed(data)


if __name__ == "__main__":
    unittest.main()
