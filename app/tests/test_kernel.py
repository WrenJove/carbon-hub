"""核算内核的验收用例。

对应 PLAN_OCR_CALC.md 第三节的三个例子，数字必须对得上。
"""

from __future__ import annotations

import unittest
from pathlib import Path

from carbonhub.factors import FactorLibrary, FactorNotFound
from carbonhub.kernel import build_ledger, calculate, issue_passport
from carbonhub.models import ActivityRecord

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"


def activity(uid: str, category: str, quantity: float, unit: str) -> ActivityRecord:
    return ActivityRecord(
        id=uid,
        source_id="test",
        occurred_at="2026-09-15",
        category=category,
        quantity=quantity,
        unit=unit,
    )


class KernelTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.library = FactorLibrary.from_csv(FIXTURES / "factors_seed.csv", version="test")

    def test_three_worked_examples(self) -> None:
        """电费 320 度 / 加油 40 升 / 打车 8.2 公里。"""

        activities = [
            activity("a-elec", "electricity", 320, "kWh"),
            activity("a-fuel", "fuel", 40, "L"),
            activity("a-trip", "transport", 8.2, "km"),
        ]
        emissions = calculate(activities, self.library)
        values = [round(e.kgco2e, 4) for e in emissions]

        self.assertAlmostEqual(values[0], 169.792, places=3)
        self.assertAlmostEqual(values[1], 92.8, places=3)
        self.assertAlmostEqual(values[2], 1.5744, places=4)

    def test_scope_assignment(self) -> None:
        """自己烧的记范围一，买的电记范围二，出行外卖记范围三。"""

        activities = [
            activity("a-elec", "electricity", 320, "kWh"),
            activity("a-fuel", "fuel", 40, "L"),
            activity("a-trip", "transport", 8.2, "km"),
        ]
        scopes = {e.activity_id: e.scope for e in calculate(activities, self.library)}

        self.assertEqual(scopes["a-elec"], 2)
        self.assertEqual(scopes["a-fuel"], 1)
        self.assertEqual(scopes["a-trip"], 3)

    def test_ledger_totals_and_traceability(self) -> None:
        activities = [
            activity("a-elec", "electricity", 320, "kWh"),
            activity("a-fuel", "fuel", 40, "L"),
            activity("a-trip", "transport", 8.2, "km"),
        ]
        ledger = build_ledger("u", activities, self.library, "2026-09-01", "2026-09-30")

        self.assertAlmostEqual(ledger.totals["scope1"], 92.8, places=3)
        self.assertAlmostEqual(ledger.totals["scope2"], 169.792, places=3)
        self.assertAlmostEqual(ledger.totals["scope3"], 1.574, places=3)
        self.assertAlmostEqual(ledger.totals["total"], 264.166, places=3)

        # 每条排放都要能回溯到活动与因子出处
        ids = {a.id for a in activities}
        for record in ledger.emissions:
            self.assertIn(record.activity_id, ids)
            self.assertTrue(record.factor_id)

    def test_factor_year_is_recorded(self) -> None:
        """算排放时要带上因子年份，方便报告里讲清楚口径。"""

        activities = [
            activity("a-new", "electricity", 100, "kWh"),
            ActivityRecord("a-old", "test", "2017-07-31", "electricity", 100, "kWh"),
        ]
        records = calculate(activities, self.library)
        by_id = {r.activity_id: r for r in records}

        self.assertTrue(by_id["a-new"].factor_year)
        # 2017 年的电，库里没有那年的因子，就近取并如实记下年份
        self.assertEqual(by_id["a-old"].factor_year, by_id["a-new"].factor_year)

    def test_match_prefers_exact_year(self) -> None:
        from carbonhub.models import EmissionFactor

        library = FactorLibrary(
            [
                EmissionFactor("old", "electricity", "kWh", 0.7, 2, 2017, "测试"),
                EmissionFactor("new", "electricity", "kWh", 0.5, 2, 2024, "测试"),
            ]
        )
        self.assertEqual(library.match("electricity", "kWh", year=2017).id, "old")
        self.assertEqual(library.match("electricity", "kWh", year=2026).id, "new")
        self.assertEqual(library.match("electricity", "kWh").id, "new")
    def test_missing_factor_raises(self) -> None:
        """匹配不到因子要报错，绝不能默认按 0 算。"""

        with self.assertRaises(FactorNotFound):
            self.library.match("waste", "kg")

    def test_passport_is_self_consistent(self) -> None:
        activities = [
            activity("a-elec", "electricity", 320, "kWh"),
            activity("a-fuel", "fuel", 40, "L"),
        ]
        ledger = build_ledger("u", activities, self.library, "2026-09-01", "2026-09-30")
        passport = issue_passport(ledger, activities, issued_at="2026-10-13T00:00:00+08:00")
        payload = passport.to_dict()

        self.assertEqual(payload["specVersion"], "carbon-passport/0.2")
        self.assertEqual(len(payload["entries"]), 2)
        self.assertEqual(len(payload["evidenceHashes"]), 2)
        self.assertEqual(len(set(payload["evidenceHashes"])), 2)
        self.assertAlmostEqual(payload["totalKgCO2e"], 262.592, places=3)

    def test_validation_rejects_bad_values(self) -> None:
        with self.assertRaises(ValueError):
            activity("bad", "not-a-category", 1, "kWh")
        with self.assertRaises(ValueError):
            ActivityRecord("bad", "t", "2026-09-15", "electricity", 1, "kWh", confidence=1.5)


if __name__ == "__main__":
    unittest.main()
