import unittest
from pathlib import Path

from pilot.journeys import (
    STATUS_CENSORED,
    STATUS_COMPLETED,
    STATUS_FAILED,
    STATUS_NOT_BOOKED,
    build_journeys,
    default_observation_end,
)
from pilot.sources import load

ROOT = Path(__file__).resolve().parents[1]


class JourneyBuildTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.sources = load(ROOT / "sources")
        cls.end = default_observation_end(cls.sources)
        cls.journeys = {j.invite: j for j in build_journeys(cls.sources, cls.end)}

    def test_rebooked_arrival_counted_once(self):
        # T1-2b 被 T1-2c 替代：改签后的到达不能记成两次。
        j = self.journeys["I1-2"]
        self.assertEqual(j.status, STATUS_COMPLETED)
        self.assertEqual(j.completion_day, 11)
        self.assertTrue(j.rebooked)
        self.assertNotIn("T1-2b", j.observed_events)
        self.assertIn("T1-2c", j.observed_events)

    def test_switched_to_ground(self):
        # 航段取消、地面到达：改走地面仍计完成。
        j = self.journeys["I1-3"]
        self.assertEqual(j.status, STATUS_COMPLETED)
        self.assertTrue(j.switched_to_ground)
        self.assertEqual(j.air_state, "cancelled")

    def test_ground_missed_is_failure(self):
        self.assertEqual(self.journeys["I3-2"].status, STATUS_FAILED)

    def test_not_booked_has_no_journey(self):
        j = self.journeys["I1-6"]
        self.assertEqual(j.status, STATUS_NOT_BOOKED)
        self.assertFalse(j.booked)

    def test_group_assignment_is_itt(self):
        # 分组来自发出邀约时的区块分配，而非实际行程。
        self.assertEqual(self.journeys["I1-6"].group, "protection")
        self.assertEqual(self.journeys["I3-6"].group, "ordinary")

    def test_observation_window_censors_unresolved(self):
        # 观察期截止到第 10 日：I1-2 的改签到达（第 11 日）尚未观察到，
        # 行程记为未决，既不算成功也不算失败。
        journeys = {j.invite: j for j in build_journeys(self.sources, 10)}
        self.assertEqual(journeys["I1-2"].status, STATUS_CENSORED)
        self.assertEqual(journeys["I1-1"].status, STATUS_COMPLETED)

    def test_every_invitation_appears_once(self):
        self.assertEqual(len(self.journeys), len(self.sources.invitations))


if __name__ == "__main__":
    unittest.main()
