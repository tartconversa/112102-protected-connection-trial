import unittest
from pathlib import Path

from pilot.capacity import (
    Pause,
    Proposal,
    active_holds,
    check_proposal,
    effective_contracts,
    evaluate_windows,
    impact_of_change,
    pool_guarantee,
)
from pilot.sources import CarrierEvent, Contract, Scenario, load

ROOT = Path(__file__).resolve().parents[1]


def base_inputs():
    sources = load(ROOT / "sources")
    return (
        list(sources.contracts),
        list(sources.carrier_events),
        list(sources.scenarios),
    )


class ContractTimelineTests(unittest.TestCase):
    def setUp(self):
        self.contracts, self.carrier_events, self.scenarios = base_inputs()

    def test_pool_guarantee_not_summed_across_hubs(self):
        # C1/C2 引用同一池 P7：保证是池级的 8 席，不可相加为 16。
        self.assertEqual(pool_guarantee(self.contracts, "P7", "W7", 42), 8)

    def test_commitment_reduction_takes_effect(self):
        # C3 自第 47 日起把 east 的承诺缩减为 5 席。
        caps = {
            key: c.seats for key, c in effective_contracts(self.contracts, 46).items()
        }
        self.assertEqual(caps[("P7", "east", "W7")], 8)
        caps = {
            key: c.seats for key, c in effective_contracts(self.contracts, 47).items()
        }
        self.assertEqual(caps[("P7", "east", "W7")], 5)
        self.assertEqual(caps[("P7", "west", "W7")], 8)

    def test_holds_retained_until_takeover_or_release(self):
        # 已出票保护保留到承运接手（O1 第 48 日）或旅客解除（O2 第 49 日）。
        holds = {h.order: h for h in active_holds(self.carrier_events, 44)}
        self.assertEqual(set(holds), {"O1", "O2"})
        self.assertEqual(holds["O1"].state, "confirmed")
        self.assertEqual(holds["O2"].state, "offered")
        holds = {h.order: h for h in active_holds(self.carrier_events, 48)}
        self.assertEqual(set(holds), {"O2"})
        self.assertEqual(active_holds(self.carrier_events, 49), [])


class WindowEvaluationTests(unittest.TestCase):
    def setUp(self):
        self.contracts, self.carrier_events, self.scenarios = base_inputs()

    def evaluate(self, day, proposals=(), pauses=(), contracts=None, events=None):
        return evaluate_windows(
            contracts or self.contracts,
            events if events is not None else self.carrier_events,
            self.scenarios,
            list(proposals),
            list(pauses),
            day,
        )

    def test_concurrent_scenario_blocks_sale(self):
        # 第 47 日：WX1 并发需求 10 席超过池保证 8 席，两枢纽均不可售。
        states = self.evaluate(47)
        for hub in ("east", "west"):
            state = states[(hub, "W7")]
            self.assertEqual(state.sellable, 0)
            self.assertTrue(any("WX1" in r for r in state.blocked))
        self.assertEqual(states[("east", "W7")].retained, 2)
        self.assertEqual(states[("west", "W7")].retained, 3)

    def test_crowding_out_flag(self):
        # 合成情形：池保证 8 席、无情景、已出票保留 8 席 →
        # 可售 0，但忽略保留可售 8：必须标出“只能靠挤占已承诺席位”。
        contracts = [
            Contract("CX", "PX", "east", "W1", 8, "confirmed_first", 1),
        ]
        events = [
            CarrierEvent("E1", "OX", "PX", "east", 8, "confirmed", 5),
        ]
        states = evaluate_windows(contracts, events, [], [], [], 10)
        state = states[("east", "W1")]
        self.assertEqual(state.sellable, 0)
        self.assertEqual(state.sellable_ignoring_holds, 8)
        self.assertTrue(any("挤占已承诺席位" in r for r in state.blocked))

    def test_pause_blocks_window(self):
        pauses = [Pause("west", "W7", 50, None, "检修")]
        states = self.evaluate(50, pauses=pauses)
        self.assertEqual(states[("west", "W7")].sellable, 0)
        self.assertTrue(states[("west", "W7")].paused)
        self.assertTrue(any("暂停" in r for r in states[("west", "W7")].blocked))

    def test_scenario_pool_check_counts_all_pool_loads(self):
        # 情景只涉及 west，但 east 的占用同样占池：池保证 5，
        # east 2 + west 2 + 情景 2 = 6 > 5 → 不可兑现。
        contracts = [
            Contract("CA", "PX", "east", "W1", 5, "confirmed_first", 1),
            Contract("CB", "PX", "west", "W1", 5, "confirmed_first", 1),
        ]
        events = [
            CarrierEvent("E1", "OE", "PX", "east", 2, "confirmed", 2),
            CarrierEvent("E2", "OW", "PX", "west", 2, "confirmed", 2),
        ]
        scenarios = [
            Scenario("SX", "W1", "west", "G1", 1, 2),
        ]
        states = evaluate_windows(contracts, events, scenarios, [], [], 3)
        west = states[("west", "W1")]
        self.assertEqual(west.sellable, 0)
        self.assertTrue(any("G1" in r and "池缺口 1" in r for r in west.blocked))

    def test_cross_hub_deduction_shared_pool(self):
        # 共同承运人：west 扩容至 12 后，WX1 池余量 2 席为两枢纽共享；
        # 派给 west 的方案要从 east 可承诺范围中扣除。
        contracts = self.contracts + [
            Contract("C9", "P7", "west", "W7", 12, "confirmed_first", 50)
        ]
        states = self.evaluate(50, contracts=contracts)
        self.assertEqual(states[("west", "W7")].sellable, 2)
        self.assertEqual(states[("east", "W7")].sellable, 1)
        proposal = Proposal("PR1", "west", "W7", "P7", 2, 50, 56, 50)
        ok, _ = check_proposal(
            proposal, contracts, self.carrier_events, self.scenarios, [], []
        )
        self.assertTrue(ok)
        states = self.evaluate(50, proposals=[proposal], contracts=contracts)
        self.assertEqual(states[("west", "W7")].sellable, 0)
        self.assertEqual(states[("east", "W7")].sellable, 0)

    def test_no_double_spend_of_free_capacity(self):
        # 同一空余量不能先后生效给两个方案。
        contracts = self.contracts + [
            Contract("C9", "P7", "west", "W7", 12, "confirmed_first", 50)
        ]
        first = Proposal("PR1", "west", "W7", "P7", 2, 50, 56, 50)
        ok, _ = check_proposal(
            first, contracts, self.carrier_events, self.scenarios, [], []
        )
        self.assertTrue(ok)
        second = Proposal("PR2", "west", "W7", "P7", 1, 50, 56, 50)
        ok, reasons = check_proposal(
            second, contracts, self.carrier_events, self.scenarios, [first], []
        )
        self.assertFalse(ok)
        self.assertTrue(any("WX1" in r for r in reasons))

    def test_proposal_blocked_without_contract(self):
        proposal = Proposal("PR3", "east", "W9", "", 1, 50, 56, 50)
        ok, reasons = check_proposal(
            proposal, self.contracts, self.carrier_events, self.scenarios, [], []
        )
        self.assertFalse(ok)
        self.assertTrue(reasons)


class CorrectionImpactTests(unittest.TestCase):
    def setUp(self):
        self.contracts, self.carrier_events, self.scenarios = base_inputs()

    def test_reduction_flags_existing_ticket(self):
        # east 承诺自第 44 日缩减为 1 席：在保留的 O2（2 席）无法保障。
        reduced = self.contracts + [
            Contract("C4", "P7", "east", "W7", 1, "confirmed_first", 44)
        ]
        impact = impact_of_change(
            self.contracts, reduced,
            self.carrier_events, self.carrier_events,
            self.scenarios, [], [], 44, 50,
        )
        at_risk = {(t.ticket, t.day) for t in impact.at_risk}
        self.assertIn(("O2", 44), at_risk)
        # C3（第 47 日 5 席）仍在其后生效，缩减只影响第 44–46 日。
        self.assertNotIn(("O2", 47), at_risk)
        self.assertIn(("east", "W7", 44), set(impact.affected_windows))

    def test_confirmed_first_priority(self):
        # 池保证缩减到 3 席：confirmed 的 O1 优先，offered 的 O2 让位。
        reduced = self.contracts + [
            Contract("C4", "P7", "east", "W7", 3, "confirmed_first", 44),
            Contract("C5", "P7", "west", "W7", 3, "confirmed_first", 44),
        ]
        impact = impact_of_change(
            self.contracts, reduced,
            self.carrier_events, self.carrier_events,
            self.scenarios, [], [], 44, 46,
        )
        tickets = {t.ticket for t in impact.at_risk}
        self.assertIn("O2", tickets)
        self.assertNotIn("O1", tickets)

    def test_carrier_correction_frees_seats(self):
        # 履约更正：O1 提前到第 45 日承运接手 → west 保留自 45 日起释放。
        corrected = self.carrier_events + [
            CarrierEvent("CE5", "O1", "P7", "west", 3, "carrier_accepted", 45)
        ]
        impact = impact_of_change(
            self.contracts, self.contracts,
            self.carrier_events, corrected,
            self.scenarios, [], [], 45, 48,
        )
        self.assertTrue(impact.affected_windows)
        states = evaluate_windows(
            self.contracts, corrected, self.scenarios, [], [], 45
        )
        self.assertEqual(states[("west", "W7")].retained, 0)


if __name__ == "__main__":
    unittest.main()
