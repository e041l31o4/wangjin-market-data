"""Test review queue prioritization and fail-closed matching."""
import importlib.util
import unittest
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location("review_queue", ROOT/"scripts/build_project_review_queue.py")
q=importlib.util.module_from_spec(spec)
spec.loader.exec_module(q)

def data():
    base=[{"projectId":str(i),"projectName":f"建案{i}","district":"楠梓區"} for i in range(1,387)]
    latest=[{**r,"transactionCount":0,"latestTransactionDate":None} for r in base]
    latest[0].update(transactionCount=3,latestTransactionDate="2026-08-20")
    return {"projects":base},{"projects":latest,"sourceDataUpdatedAt":"2026-10-12"}

class ReviewQueueTests(unittest.TestCase):
    def test_three_distinct_priorities(self):
        b,l=data()
        aliases={"candidates":[{"candidateProjectId":"2","transactionName":"建商建案2"}]}
        completed={"cases":[{"projectId":"3","pdfAddress":"某路30號"}]}
        rows=q.make_queue(b,l,aliases,completed)
        self.assertEqual(len(rows),386)
        self.assertEqual(rows[0]["projectId"],"1")
        self.assertEqual(rows[0]["priority"],"P1")
        self.assertEqual(len([r for r in rows if r["priority"]=="P2"]),2)
        self.assertEqual(len([r for r in rows if r["priority"]=="P3"]),383)
        self.assertEqual([r for r in rows if r["projectId"]=="2"][0]["candidateTransactionCount"],"")

    def test_base_name_change_stops_report(self):
        b,l=data()
        l["projects"][0]["projectName"]="另一建案"
        with self.assertRaises(ValueError):
            q.make_queue(b,l,{},{})

    def test_duplicate_ids_stop_report(self):
        b,l=data()
        l["projects"][1]["projectId"]="1"
        with self.assertRaises(ValueError):
            q.make_queue(b,l,{},{})

    def test_report_does_not_claim_zero_sales(self):
        b,l=data()
        lines=q.summary_md(q.make_queue(b,l,{},{}),l)
        self.assertIn("沒有匹配不等於零成交",lines)
        self.assertIn("未獲得成交歸屬核准",lines)

if __name__=="__main__":
    unittest.main()
