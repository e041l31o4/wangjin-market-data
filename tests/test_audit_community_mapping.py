"""Regression checks for completed-home address review; no network needed."""
import importlib.util
import unittest
from pathlib import Path

PATH = Path(__file__).resolve().parents[1] / "scripts" / "audit_community_mapping.py"
spec = importlib.util.spec_from_file_location("address_audit", PATH)
audit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)


class AddressAuditTests(unittest.TestCase):
    def test_full_building_number_required(self):
        self.assertIsNone(audit.address_key("赤德街", "仁武區"))
        self.assertEqual(audit.address_key("高雄市仁武區赤德街50號3樓", "仁武區"),
                         "赤德街50號")
        self.assertNotEqual(audit.address_key("赤德街82號", "仁武區"),
                            audit.address_key("赤德街50號", "仁武區"))

    def test_duplicate_candidate_detected_and_unpublished(self):
        rows = {}
        cases = [
            {"projectId": "1", "name": "澄光", "district": "仁武區",
             "pdfAddress": "赤德街50號"},
            {"projectId": "2", "name": "澄境", "district": "仁武區",
             "pdfAddress": "赤德街50號"}
        ]
        report = audit.run(rows, [], cases)
        self.assertEqual(len(report["candidates"]), 2)
        self.assertTrue(any(w["reason"] == "multiple_projects_share_candidate_address"
                            for w in report["warnings"]))
        self.assertTrue(all(not c["readyForPublish"] for c in report["candidates"]))

    def test_conflicting_verified_mapping_detected(self):
        rows = [
            {"district": "仁武區", "addressKey": "赤德街50號",
             "buildingName": "澄光", "source": "document a"},
            {"district": "仁武區", "addressKey": "赤德街50號",
             "buildingName": "澄境", "source": "document b"}
        ]
        problems, _ = audit.inspect_mapping(rows)
        self.assertTrue(any(p["reason"] == "address_conflict" for p in problems))

    def test_building_number_not_mistaken_for_first_sale(self):
        tx = {"abc": {"category": "成屋", "district": "仁武區",
                      "addressKey": "赤德街50號"}}
        report = audit.run(tx, [], [{"name": "澄光", "district": "仁武區",
                                     "pdfAddress": "赤德街50號"}])
        candidate = report["candidates"][0]
        self.assertEqual(candidate["transactionCountAtAddress"], 1)
        self.assertFalse(candidate["readyForPublish"])


if __name__ == "__main__":
    unittest.main()
