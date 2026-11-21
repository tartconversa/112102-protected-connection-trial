import unittest
from pathlib import Path

from pilot.analysis import (
    assignment_space,
    run_coverage,
    run_itt,
    run_spillover,
    run_trial,
    assess_hubs,
)
from pilot.journeys import build_journeys, default_observation_end
from pilot.sources import load

ROOT = Path(__file__).resolve().parents[1]


class AnalysisTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.sources = load(ROOT / "sources")
        cls.end = default_observation_end(cls.sources)
        cls.journeys = build_journeys(cls.sources, cls.end)
        cls.itt = run_itt(cls.journeys, list(cls.sources.blocks))

    def test_primary_itt_difference(self):
        # 主分析：未购票计入分母且视为未完成。30/36 − 24/36 = 1/6。
        self.assertAlmostEqual(self.itt.primary.rate_protection, 30 / 36)
        self.assertAlmostEqual(self.itt.primary.rate_ordinary, 24 / 36)
        self.assertAlmostEqual(self.itt.primary.diff, 6 / 36)

    def test_sensitivity_difference(self):
        # 敏感性：仅可判定行程。30/30 − 24/30 = 0.2。
        self.assertAlmostEqual(self.itt.sensitivity.diff, 0.2)

    def test_degenerate_cluster_variance_flagged(self):
        # 每个普通区块恰为 4/5 完成：集群间无变异，区间退化必须标注。
        self.assertTrue(self.itt.primary.degenerate)

    def test_assignment_space_respects_design(self):
        # 第 2、5 周两枢纽均为保护（周级比例 100%），第 3、6 周均为普通；
        # 只有第 1、4 周（50%）可重排枢纽 → 4 种分配。
        assignments = assignment_space(list(self.sources.blocks))
        self.assertEqual(len(assignments), 4)
        for labels in assignments:
            self.assertEqual(labels["Beast2"], "protection")
            self.assertEqual(labels["Bwest5"], "protection")
            self.assertEqual(labels["Beast3"], "ordinary")

    def test_randomization_pvalue(self):
        # 观测差值是 4 种分配中最大的 → 单侧 p = 1/4。
        self.assertEqual(self.itt.ri_permutations, 4)
        self.assertAlmostEqual(self.itt.ri_p_primary, 0.25)

    def test_spillover_excludes_censored(self):
        spill = run_spillover(list(self.sources.ordinary), list(self.sources.blocks))
        self.assertEqual(spill.censored, 1)
        self.assertEqual(spill.censored_ids, ("Nwest2",))
        self.assertEqual(spill.observations, 11)
        # east：保护周错过率更高（席位被优先占用的外溢迹象）。
        self.assertAlmostEqual(spill.per_hub["east"].diff, 0.046026, places=5)
        # 合计接近零：外溢在合计口径下被稀释，必须分枢纽看。
        self.assertAlmostEqual(spill.overall.diff, -0.000844, places=5)

    def test_coverage(self):
        cov = run_coverage(self.journeys, self.sources, self.end)
        self.assertEqual(cov.offer_mismatches, ())
        self.assertEqual(cov.ordinary_censored, ("Nwest2",))
        self.assertEqual(cov.journey_censored, ())
        self.assertEqual(cov.per_group["protection"].switched_to_ground, 12)
        self.assertEqual(cov.per_group["ordinary"].switched_to_ground, 6)

    def test_hub_assessment(self):
        hubs = assess_hubs(self.journeys, self.sources, self.itt, alpha=0.05)
        # 两个枢纽点估计方向一致，但都达不到扩围门槛。
        self.assertFalse(hubs["east"].supported)
        self.assertFalse(hubs["west"].supported)
        self.assertTrue(hubs["east"].coverage_ok)
        # west 有一条普通旅客观测未查明去向 → 覆盖不足。
        self.assertFalse(hubs["west"].coverage_ok)
        self.assertTrue(any("随机化" in r for r in hubs["east"].reasons))
        self.assertTrue(any("未查明" in r for r in hubs["west"].reasons))

    def test_run_trial_end_to_end(self):
        assessment = run_trial(self.sources, self.end)
        self.assertEqual(assessment.supported_hubs, [])
        self.assertEqual(assessment.itt.ri_permutations, 4)


if __name__ == "__main__":
    unittest.main()
