"""减排建议 / 数据质量分 / 凭证校验 的验收用例。"""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from carbonhub.advice import build_profile, frontier, humanize, plan
from carbonhub.factors import FactorLibrary
from carbonhub.kernel import build_ledger, issue_passport
from carbonhub.models import ActivityRecord
from carbonhub.quality import assess
from carbonhub.report import render_html
from carbonhub.verify import verify

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"


def activity(uid, category, quantity, unit, confidence=1.0, day="2026-09-15"):
    return ActivityRecord(uid, "test", day, category, quantity, unit, confidence=confidence)


class PlanTest(unittest.TestCase):
    """减排规划：候选由数据算出来，组合由约束优化选出来。"""

    @classmethod
    def setUpClass(cls):
        cls.library = FactorLibrary.from_csv(FIXTURES / "factors_seed.csv", version="t")

    def _ledger(self, acts):
        return build_ledger("u", acts, self.library, "2026-09-01", "2026-09-30")

    def test_candidates_only_for_present_categories(self):
        acts = [activity("a", "electricity", 100, "kWh")]
        result = plan(self._ledger(acts), acts)
        self.assertTrue(result.candidates)
        self.assertEqual({c.category for c in result.candidates}, {"electricity"})

    def test_every_candidate_carries_assumption_and_rationale(self):
        acts = [activity("a", "electricity", 120, "kWh"), activity("b", "fuel", 30, "L")]
        result = plan(self._ledger(acts), acts)
        for candidate in result.candidates:
            self.assertTrue(candidate.assumption)
            self.assertTrue(candidate.rationale)
            self.assertGreater(candidate.reduction_kg, 0)

    def test_budget_constraint_is_respected(self):
        acts = [activity("a", "electricity", 300, "kWh")]
        result = plan(self._ledger(acts), acts, budget_yuan=0, max_actions=8)
        self.assertLessEqual(result.total_setup_cost, 0.01)
        # 花钱的动作被排除，但零成本的还在
        self.assertTrue(all(c.setup_cost_yuan <= 0 for c in result.chosen))

    def test_action_count_constraint_is_respected(self):
        acts = [activity("a", "electricity", 300, "kWh"), activity("b", "fuel", 40, "L")]
        result = plan(self._ledger(acts), acts, budget_yuan=10000, max_actions=2)
        self.assertLessEqual(len(result.chosen), 2)
        self.assertGreaterEqual(len(result.candidates), 3)

    def test_loosening_constraints_never_reduces_saving(self):
        acts = [activity("a", "electricity", 300, "kWh"), activity("b", "fuel", 40, "L")]
        ledger = self._ledger(acts)
        tight = plan(ledger, acts, budget_yuan=0, max_actions=1)
        loose = plan(ledger, acts, budget_yuan=100000, max_actions=20)
        self.assertGreaterEqual(loose.total_reduction_kg, tight.total_reduction_kg)
        # 预算和项数都放开后，受类别上限约束，应当正好取到 cap_ceiling
        self.assertAlmostEqual(loose.total_reduction_kg, loose.cap_ceiling_kg, places=1)

    def test_budget_actually_changes_the_plan(self):
        """预算必须真的能改变方案，否则这个旋钮就是摆设。"""

        acts = [activity("a", "electricity", 300, "kWh")]
        ledger = self._ledger(acts)
        poor = plan(ledger, acts, budget_yuan=0, max_actions=8)
        rich = plan(ledger, acts, budget_yuan=100000, max_actions=8)
        self.assertGreater(
            rich.total_reduction_kg, poor.total_reduction_kg,
            "预算放宽后减排量应当上升，否则说明没有贵的大件可选",
        )
        self.assertGreater(poor.total_setup_cost, -0.01)

    def test_category_cap_limits_a_single_category(self):
        """一个类别的减排不能超过它的年度排放乘以上限比例。"""

        acts = [activity("a", "electricity", 300, "kWh")]
        ledger = self._ledger(acts)
        result = plan(ledger, acts, budget_yuan=100000, max_actions=20, category_cap_ratio=0.4)
        annual_kg = ledger.by_category["electricity"] * (12.0 / 1)
        self.assertLessEqual(result.total_reduction_kg, annual_kg * 0.4 + 0.5)

    def test_frontier_is_a_staircase(self):
        """台阶：投入递增、减排必须严格递增，否则那档没意义。"""

        acts = [activity("a", "electricity", 300, "kWh"), activity("b", "fuel", 40, "L")]
        steps = frontier(self._ledger(acts), acts, max_actions=6)
        self.assertTrue(steps)
        costs = [s.cost_yuan for s in steps]
        savings = [s.reduction_kg for s in steps]
        self.assertEqual(costs, sorted(costs))
        self.assertEqual(savings, sorted(savings))
        self.assertEqual(len(set(costs)), len(costs))

    def test_frontier_marks_dead_zone(self):
        """填一个落在平台区的预算，必须明确告诉用户多花的钱没用。"""

        acts = [activity("a", "electricity", 300, "kWh")]
        ledger = self._ledger(acts)
        steps = frontier(ledger, acts, max_actions=6)
        self.assertGreaterEqual(len(steps), 2)
        dead = steps[-1].cost_yuan + 500  # 最后一档之后随便加钱
        result = plan(ledger, acts, budget_yuan=dead, max_actions=6)
        self.assertEqual(result.total_reduction_kg, steps[-1].reduction_kg)
        self.assertTrue(any("平台区" in n for n in result.notes))

    def test_plan_at_step_matches_frontier(self):
        acts = [activity("a", "electricity", 300, "kWh")]
        ledger = self._ledger(acts)
        steps = frontier(ledger, acts, max_actions=5)
        for step in steps:
            result = plan(ledger, acts, budget_yuan=step.cost_yuan, max_actions=5)
            self.assertAlmostEqual(result.total_reduction_kg, step.reduction_kg, places=1)
    def test_kind_filter_only_returns_device_actions(self):
        """只换东西、不改习惯：过滤后不该出现行为类动作。"""

        acts = [activity("a", "electricity", 300, "kWh"), activity("b", "fuel", 40, "L")]
        ledger = self._ledger(acts)
        result = plan(ledger, acts, budget_yuan=100000, max_actions=20, allowed_kinds=("设备",))
        self.assertTrue(result.chosen)
        self.assertEqual({c.kind for c in result.chosen}, {"设备"})
        self.assertLessEqual(len(result.candidates), len(plan(ledger, acts).candidates))

    def test_every_candidate_is_labelled_device_or_habit(self):
        acts = [activity("a", "electricity", 300, "kWh"), activity("b", "fuel", 40, "L")]
        result = plan(self._ledger(acts), acts)
        self.assertTrue(result.candidates)
        self.assertTrue(all(c.kind in ("设备", "行为") for c in result.candidates))

    def test_letting_habits_in_never_reduces_the_ceiling(self):
        acts = [activity("a", "electricity", 300, "kWh"), activity("b", "fuel", 40, "L")]
        ledger = self._ledger(acts)
        device_only = plan(ledger, acts, budget_yuan=100000, max_actions=20, allowed_kinds=("设备",))
        everything = plan(ledger, acts, budget_yuan=100000, max_actions=20)
        self.assertLess(device_only.ceiling_kg, everything.ceiling_kg)

    def test_profile_flags_out_of_band_usage(self):
        acts = [activity("a", "electricity", 900, "kWh")]  # 月均远超经验上限
        notes = build_profile(self._ledger(acts), acts)
        electricity = next(n for n in notes if n.category == "electricity")
        self.assertEqual(electricity.verdict, "偏高")

    def test_humanize_reads_like_a_sentence(self):
        text = humanize(12.0)
        self.assertIn("公里", text)
        self.assertIn("树", text)

    def test_chosen_sorted_by_reduction(self):
        acts = [activity("a", "electricity", 300, "kWh"), activity("b", "fuel", 40, "L")]
        result = plan(self._ledger(acts), acts, budget_yuan=1000, max_actions=8)
        savings = [c.reduction_kg for c in result.chosen]
        self.assertEqual(savings, sorted(savings, reverse=True))

class QualityTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.library = FactorLibrary.from_csv(FIXTURES / "factors_seed.csv", version="t")

    def test_no_data_scores_zero(self):
        ledger = build_ledger("u", [], self.library, "2026-09-01", "2026-09-30")
        self.assertEqual(assess([], ledger).score, 0)

    def test_clean_data_scores_high(self):
        acts = [activity("a", "electricity", 100, "kWh", confidence=1.0, day="2023-09-15")]
        ledger = build_ledger("u", acts, self.library, "2023-09-01", "2023-09-30")
        report = assess(acts, ledger, warnings=[])
        self.assertGreaterEqual(report.score, 85)
        self.assertEqual(report.level, "高")

    def test_low_confidence_lowers_score_and_hints(self):
        acts = [activity("a", "transport", 10, "km", confidence=0.5)]
        ledger = build_ledger("u", acts, self.library, "2026-09-01", "2026-09-30")
        report = assess(acts, ledger, warnings=["有行没读出来"])
        self.assertLess(report.score, 85)
        self.assertTrue(report.hints)

    def test_parts_add_up(self):
        acts = [activity("a", "electricity", 100, "kWh")]
        ledger = build_ledger("u", acts, self.library, "2026-09-01", "2026-09-30")
        report = assess(acts, ledger)
        self.assertEqual(report.score, round(sum(p.earned for p in report.parts)))


class VerifyTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.library = FactorLibrary.from_csv(FIXTURES / "factors_seed.csv", version="t")

    def _passport(self):
        acts = [
            activity("a", "electricity", 100, "kWh"),
            activity("b", "fuel", 20, "L"),
        ]
        ledger = build_ledger("u", acts, self.library, "2026-09-01", "2026-09-30")
        return issue_passport(ledger, acts, issued_at="2026-10-14T00:00:00+08:00").to_dict()

    def test_untouched_passport_passes(self):
        self.assertEqual(verify(self._passport()), [])

    def test_values_needing_rounding_still_verify(self):
        """112 度 × 0.5306 = 59.4272，会触发四舍五入，指纹必须仍然自洽。"""

        acts = [activity("a", "electricity", 112, "kWh")]
        ledger = build_ledger("u", acts, self.library, "2026-09-01", "2026-09-30")
        payload = issue_passport(ledger, acts, issued_at="2026-10-14T00:00:00+08:00").to_dict()
        self.assertEqual(verify(payload), [])
    def test_edited_entry_is_caught(self):
        payload = self._passport()
        payload["entries"][0]["kgCO2e"] = 999.0   # 偷偷改一条
        problems = verify(payload)
        self.assertTrue(any("指纹链对不上" in p for p in problems))

    def test_edited_total_is_caught(self):
        payload = self._passport()
        payload["totalKgCO2e"] = 1.0
        self.assertTrue(any("总额与明细对不上" in p for p in verify(payload)))

    def test_missing_root_is_reported(self):
        payload = self._passport()
        payload.pop("ledgerRoot")
        self.assertTrue(any("ledgerRoot" in p for p in verify(payload)))

    def test_round_trips_through_json(self):
        payload = json.loads(json.dumps(self._passport(), ensure_ascii=False))
        self.assertEqual(verify(payload), [])


class ReportTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.library = FactorLibrary.from_csv(FIXTURES / "factors_seed.csv", version="t")

    def _build(self):
        acts = [
            activity("a", "electricity", 112, "kWh"),
            activity("b", "fuel", 28.23, "L", confidence=0.6),
        ]
        ledger = build_ledger("我的碳账本", acts, self.library, "2026-09-01", "2026-09-30")
        passport = issue_passport(ledger, acts, issued_at="2026-10-14T00:00:00+08:00")
        return render_html(ledger, acts, assess(acts, ledger, ["一行没读出来"]),
                           plan(ledger, acts, max_actions=4), passport,
                           ["一行没读出来"], "2026-10-14 20:00")

    def test_report_is_self_contained(self):
        page = self._build()
        self.assertIn("<!doctype html>", page)
        # 不许依赖外部资源，否则离线打开就花了
        self.assertNotIn("http://", page)
        self.assertNotIn("https://", page)
        self.assertNotIn("<script", page)

    def test_report_contains_the_numbers(self):
        page = self._build()
        self.assertIn("我的碳账本", page)
        self.assertIn("cn_grid_2023", page)          # 因子可追溯
        self.assertIn("置信度", page)

    def test_report_escapes_html(self):
        acts = [activity("a", "electricity", 100, "kWh")]
        ledger = build_ledger("<script>alert(1)</script>", acts, self.library, "2026-09-01", "2026-09-30")
        passport = issue_passport(ledger, acts, issued_at="2026-10-14T00:00:00+08:00")
        page = render_html(ledger, acts, assess(acts, ledger), plan(ledger, acts),
                           passport, [], "2026-10-14")
        self.assertNotIn("<script>alert(1)</script>", page)
        self.assertIn("&lt;script&gt;", page)

if __name__ == "__main__":
    unittest.main()
