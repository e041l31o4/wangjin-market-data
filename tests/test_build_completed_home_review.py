import importlib.util
import sys
import unittest
from pathlib import Path

scripts = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(scripts))
spec = importlib.util.spec_from_file_location("completed", scripts / "build_completed_home_review.py")
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


class CompletedHomeReviewTests(unittest.TestCase):
    def build(self, *, second=False, rows=None, year=2026, mapping=None):
        projects = [{"projectId": "1", "name": "一案", "district": "楠梓區", "kind": "成屋", "address": "大學路1號"}]
        if second:
            projects.append({**projects[0], "projectId": "2", "name": "二案"})
        tx = {"a": {"category": "成屋", "district": "楠梓區", "addressKey": "大學路1號",
                    "date": "2026-03-01", "completedAt": "2025-01-01", "officialTransactionId": "ONE",
                    "officialLocation": "高雄市楠梓區大學路１號二樓", "officialSource": m.MOI}}
        return m.build_review({"projects": projects}, rows if rows is not None else tx, mapping or [], year)

    def test_address_alone_never_verifies_community_or_first_sale(self):
        row = self.build()["cases"][0]
        self.assertEqual(row["status"], "address_candidate_needs_evidence")
        self.assertFalse(row["communityMappingVerified"])
        self.assertFalse(row["developerFirstSaleVerified"])
        self.assertEqual(row["traceableRecentTransactionCount"], 1)

    def test_conflicting_projects_block_community_mapping(self):
        result = self.build(second=True, mapping=[{"district": "楠梓區", "addressKey": "大學路1號", "buildingName": "一案", "source": "https://example.com"}])
        self.assertTrue(all(r["status"] == "address_conflict" and not r["communityMappingVerified"] for r in result["cases"]))

    def test_verified_address_remains_distinct_from_first_sale(self):
        row = self.build(mapping=[{"district": "楠梓區", "addressKey": "大學路1號", "buildingName": "一案", "source": "https://example.com"}])["cases"][0]
        self.assertTrue(row["communityMappingVerified"])
        self.assertFalse(row["developerFirstSaleVerified"])

    def test_old_missing_and_future_completion_are_not_recent(self):
        base = self.build()["cases"][0]["transactions"][0]
        for built in ("2010-01-01", None, "2027-01-01"):
            row = {"category": "成屋", "district": "楠梓區", "addressKey": "大學路1號", "date": "2026-03-01", "completedAt": built}
            result = self.build(rows={"a": row})["cases"][0]
            self.assertEqual(result["traceableRecentTransactionCount"], 0)
            self.assertFalse(result["transactions"][0]["withinFiveYears"])

    def test_wrong_year_excluded_and_duplicate_identity_not_traceable(self):
        self.assertEqual(self.build(year=2025)["officialCompletedTransactionCount"], 0)
        tx = {"category": "成屋", "district": "楠梓區", "addressKey": "大學路1號", "date": "2026-03-01", "completedAt": "2025-01-01", "officialTransactionId": "DUP", "officialLocation": "高雄市楠梓區大學路1號", "officialSource": m.MOI}
        row = self.build(rows={"a": tx, "b": tx})["cases"][0]
        self.assertEqual(row["transactionCountAtAddress"], 2)
        self.assertEqual(row["traceableRecentTransactionCount"], 0)


if __name__ == "__main__":
    unittest.main()
