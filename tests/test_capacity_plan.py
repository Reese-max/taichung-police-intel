"""Keep the #165 input packet honest until real commitments are supplied."""

import json
from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs/govintel/competition-2026"
PLAN = DOCS / "CAPACITY_PLAN.md"


class CapacityPlanTests(unittest.TestCase):
    def setUp(self):
        self.assertTrue(PLAN.is_file(), "#165 capacity planning input register is missing")
        self.text = PLAN.read_text(encoding="utf-8")

    def test_unconfirmed_roles_and_dates_are_not_commitments(self):
        self.assertIn("BLOCKED_PENDING_REAL_INPUTS", self.text)
        roles = [line.split("|")[1:-1] for line in self.text.splitlines()
                 if line.startswith("| R")]
        self.assertEqual(len(roles), 6)
        self.assertEqual({row[1].strip() for row in roles}, {
            "開發", "來源維護", "介面", "資料標註", "獨立覆核", "試用協調",
        })
        for row in roles:
            self.assertEqual([cell.strip() for cell in row[2:]],
                             ["未承諾", "null", "null", "待確認"])
        weeks = [line.split("|")[1:-1] for line in self.text.splitlines()
                 if line.startswith("| W")]
        self.assertEqual([row[0].strip() for row in weeks],
                         [f"W{week}" for week in range(1, 7)])
        for row in weeks:
            self.assertEqual(row[1].strip(), "待確認")
            self.assertEqual(row[-1].strip(), "未排定")

    def test_dependencies_and_reused_receipts_resolve(self):
        for dependency in ("七個有效日", "授權／完整性", "D2", "官方版本案例", "真實人員"):
            self.assertIn(dependency, self.text)
        for target in re.findall(r"\]\(([^)]+)\)", self.text):
            if not target.startswith("https://"):
                self.assertTrue((DOCS / target.split("#")[0]).is_file(), target)
        observation = json.loads((DOCS / "closure-20261006/resource-observation.json")
                                 .read_text(encoding="utf-8"))
        self.assertEqual(observation["collection"]["sample_count"], 1)
        self.assertIsNone(observation["collection"]["p95_seconds"])
        self.assertIsNone(observation["monthly_cost_estimate"])
        self.assertIn("不得重標為本輪量測", self.text)

    def test_cost_and_workload_denominators_stay_explicit(self):
        for field in ("原始文件分母", "文件版本分母", "模型請求／token／耗時",
                      "運算", "儲存傳輸", "來源維護工時", "人工核對工時",
                      "月完成查詢量", "月有效更新量", "月追蹤條件量",
                      "尖峰並行查詢", "每月成本估算", "團隊成本上限"):
            self.assertRegex(self.text, rf"\| {re.escape(field)} \| null \|")
        for unit in ("completed_query", "effective_update", "tracked_condition"):
            self.assertIn(unit, self.text)
        self.assertIn("零分母或缺單價時維持 null", self.text)
        self.assertIn("兩名獨立真人覆核", self.text)
        self.assertIn("同一成員兼任時不可重複計入容量", self.text)

    def test_fallback_and_issue25_issue62_keep_the_gate_open(self):
        self.assertIn("cutoff", self.text)
        self.assertIn("不授權付費開通、購買、招募或外部訊息", self.text)
        self.assertIn("#165 保持 open", self.text)
        for filename in ("PROPOSAL_MAPPING.md", "ACCEPTANCE_CHECKLIST.md"):
            self.assertIn("CAPACITY_PLAN.md", (DOCS / filename).read_text(encoding="utf-8"))
        self.assertRegex((DOCS / "ACCEPTANCE_CHECKLIST.md").read_text(encoding="utf-8"),
                         r"- \[ \] #165 ")
        self.assertIn('"test_capacity_plan.py"',
                      (ROOT / "scripts/verify-project.mjs").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
