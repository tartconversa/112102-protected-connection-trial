"""可兑现决策：合同保证、已出票保留、并发情景下的可出售范围。

模型规则（业务口径，随每次评估记录）：

- 合同 seats 是池级保证：同一池被多个枢纽引用时不可相加；
  池保证取该池各枢纽生效行中的最大值，枢纽上限取本枢纽最新生效行；
- 派给一边的兜底席位从另一边可承诺范围中扣除：各枢纽合计承诺
  不得超过池保证（共同承运人跨枢纽扣减）；
- 已出票的保护（承运订单 offered/confirmed）一直保留席位，直到
  carrier_accepted（承运侧接手）或 released（旅客解除权利）；
- 同一 simultaneous_group 的延误需求可能同时出现，可出售量必须
  在每个并发情景下仍可兑现（枢纽上限与池保证双重约束）；
- 新方案按提交顺序生效：已接受方案立即占用容量，后续方案不得
  从同一份空余量重复生效；
- priority=confirmed_first：容量不足时 confirmed 订单优先保障，
  offered 订单与已接受方案依次让位。
"""
from __future__ import annotations

from dataclasses import dataclass

from .sources import HOLDING_STATES, CarrierEvent, Contract, Scenario

# 保障优先级：数值越小越优先（confirmed_first 合同口径）。
RANK_CONFIRMED_HOLD = 0
RANK_OFFERED_HOLD = 1
RANK_PROPOSAL = 2


@dataclass(frozen=True)
class Proposal:
    """运营提交的窗口售票方案。被接受后即成为承诺，占用容量。"""

    proposal: str
    hub: str
    window: str
    pool: str
    seats: int
    start: int
    end: int
    day: int  # 提交（生效评估）日
    note: str = ""


@dataclass(frozen=True)
class Pause:
    """运营侧暂停：自 from_day 起（可选至 until）窗口停止出售。"""

    hub: str
    window: str
    from_day: int
    until: int | None
    reason: str


@dataclass(frozen=True)
class Hold:
    """某评估日仍占用席位的承运订单。"""

    order: str
    hub: str
    pool: str
    seats: int
    state: str  # offered | confirmed
    since: int


@dataclass(frozen=True)
class ScenarioLoad:
    """一个并发情景在当前占用下的可兑现性。"""

    group: str
    demand: tuple[tuple[str, int], ...]  # (hub, seats)
    feasible: bool
    pool_shortfall: int
    hub_shortfalls: tuple[tuple[str, int], ...]


@dataclass(frozen=True)
class WindowState:
    """一个 (枢纽, 窗口) 在评估日的可出售状态。"""

    hub: str
    window: str
    pool: str | None
    cap: int
    guarantee: int
    holds: tuple[Hold, ...]
    committed: int  # 已接受方案占用
    sellable: int
    sellable_ignoring_holds: int
    blocked: tuple[str, ...]
    scenarios: tuple[ScenarioLoad, ...]
    paused: bool

    @property
    def retained(self) -> int:
        return sum(h.seats for h in self.holds)


# ---------------------------------------------------------------- 合同与保留


def effective_contracts(
    contracts: list[Contract], day: int
) -> dict[tuple[str, str, str], Contract]:
    """各 (pool, hub, window) 在评估日的生效合同行（最新生效日 ≤ day）。"""
    effective: dict[tuple[str, str, str], Contract] = {}
    for c in sorted(contracts, key=lambda c: (c.available_day, c.contract)):
        if c.available_day <= day:
            effective[(c.pool, c.hub, c.window)] = c
    return effective


def pool_guarantee(contracts: list[Contract], pool: str, window: str, day: int) -> int:
    """池级保证：同一池被多个枢纽引用时取最大值，不可相加。"""
    seats = [
        c.seats
        for (p, _h, w), c in effective_contracts(contracts, day).items()
        if p == pool and w == window
    ]
    return max(seats, default=0)


def active_holds(carrier_events: list[CarrierEvent], day: int) -> list[Hold]:
    """评估日仍在保留席位的承运订单。

    订单最近一个不晚于 day 的事件为 offered/confirmed 时保留席位；
    carrier_accepted 或 released 后释放。保留各次独立事实。
    """
    by_order: dict[str, list[CarrierEvent]] = {}
    for e in carrier_events:
        by_order.setdefault(e.order, []).append(e)
    holds: list[Hold] = []
    for order, events in sorted(by_order.items()):
        past = [e for e in events if e.day <= day]
        if not past:
            continue
        latest = max(past, key=lambda e: (e.day, e.event))
        if latest.state in HOLDING_STATES:
            holds.append(
                Hold(
                    order=order,
                    hub=latest.hub,
                    pool=latest.pool,
                    seats=latest.seats,
                    state=latest.state,
                    since=min(e.day for e in past),
                )
            )
    return holds


# ---------------------------------------------------------------- 窗口评估


def evaluate_windows(
    contracts: list[Contract],
    carrier_events: list[CarrierEvent],
    scenarios: list[Scenario],
    proposals: list[Proposal],
    pauses: list[Pause],
    day: int,
) -> dict[tuple[str, str], WindowState]:
    """评估日各 (枢纽, 窗口) 的可出售状态与阻塞原因。"""
    effective = effective_contracts(contracts, day)
    holds = active_holds(carrier_events, day)
    active_props = [p for p in proposals if p.start <= day <= p.end]

    keys = {(c.hub, c.window) for c in contracts}
    keys |= {(s.hub, s.window) for s in scenarios}
    keys |= {(p.hub, p.window) for p in proposals}
    keys |= {(p.hub, p.window) for p in pauses}

    states: dict[tuple[str, str], WindowState] = {}
    for hub, window in sorted(keys):
        pool = _pool_of(contracts, proposals, hub, window)
        line = effective.get((pool, hub, window)) if pool else None
        cap = line.seats if line else 0
        guarantee = pool_guarantee(contracts, pool, window, day) if pool else 0

        window_holds = tuple(h for h in holds if h.hub == hub and h.pool == pool)
        pool_holds = [h for h in holds if h.pool == pool]
        own_committed = sum(
            p.seats for p in active_props if p.hub == hub and p.window == window
        )
        pool_committed = sum(p.seats for p in active_props if p.pool == pool)
        retained = sum(h.seats for h in window_holds)
        pool_retained = sum(h.seats for h in pool_holds)

        pause = next(
            (
                p
                for p in pauses
                if p.hub == hub
                and p.window == window
                and p.from_day <= day
                and (p.until is None or day <= p.until)
            ),
            None,
        )

        # 池内各枢纽的当前占用与合同上限（并发情景评估用）
        pool_hubs = {h for (p, h, w) in effective if p == pool and w == window}
        pool_hubs |= {s.hub for s in scenarios if s.window == window}
        pool_hubs.add(hub)
        loads = {
            h: sum(x.seats for x in pool_holds if x.hub == h)
            + sum(p.seats for p in active_props if p.pool == pool and p.hub == h)
            for h in pool_hubs
        }
        caps = {
            h: (effective.get((pool, h, window)).seats
                if effective.get((pool, h, window)) else 0)
            for h in pool_hubs
        }
        window_scenarios = _scenario_loads(scenarios, window, loads, caps, guarantee)

        sellable, blocked = _sellable(
            hub=hub,
            cap=cap,
            guarantee=guarantee,
            retained=retained,
            committed=own_committed,
            pool_retained=pool_retained,
            pool_committed=pool_committed,
            scenarios=window_scenarios,
            pool=pool,
            pause=pause,
        )
        # 若忽略已出票保留的可售量：用于识别“只能靠挤占已承诺席位”。
        sellable_free, _ = _sellable(
            hub=hub,
            cap=cap,
            guarantee=guarantee,
            retained=0,
            committed=own_committed,
            pool_retained=0,
            pool_committed=pool_committed,
            scenarios=window_scenarios,
            pool=pool,
            pause=None,
        )
        reasons = list(blocked)
        if pause is None and sellable == 0 and sellable_free > 0:
            reasons.append(
                f"若忽略已出票保留可售 {sellable_free} 席：只能靠挤占已承诺席位，不得扩售"
            )
        states[(hub, window)] = WindowState(
            hub=hub,
            window=window,
            pool=pool,
            cap=cap,
            guarantee=guarantee,
            holds=window_holds,
            committed=own_committed,
            sellable=sellable,
            sellable_ignoring_holds=sellable_free,
            blocked=tuple(reasons),
            scenarios=window_scenarios,
            paused=pause is not None,
        )
    return states


def _pool_of(
    contracts: list[Contract], proposals: list[Proposal], hub: str, window: str
) -> str | None:
    for c in contracts:
        if c.hub == hub and c.window == window:
            return c.pool
    for p in proposals:
        if p.hub == hub and p.window == window:
            return p.pool
    return None


def _scenario_loads(
    scenarios: list[Scenario],
    window: str,
    loads: dict[str, int],
    caps: dict[str, int],
    guarantee: int,
) -> tuple[ScenarioLoad, ...]:
    """各并发情景在当前占用（保留 + 已接受方案）下的可兑现性。"""
    groups: dict[str, dict[str, int]] = {}
    for s in scenarios:
        if s.window == window:
            groups.setdefault(s.simultaneous_group, {}).setdefault(s.hub, 0)
            groups[s.simultaneous_group][s.hub] += s.seats
    results = []
    for group, demand in sorted(groups.items()):
        hub_shortfalls = tuple(
            (h, loads.get(h, 0) + d - caps.get(h, 0))
            for h, d in sorted(demand.items())
            if loads.get(h, 0) + d > caps.get(h, 0)
        )
        # 池级可行性：池内全部枢纽的现有占用 + 本情景新增需求。
        total = sum(loads.values()) + sum(demand.values())
        pool_shortfall = max(0, total - guarantee)
        results.append(
            ScenarioLoad(
                group=group,
                demand=tuple(sorted(demand.items())),
                feasible=not hub_shortfalls and pool_shortfall == 0,
                pool_shortfall=pool_shortfall,
                hub_shortfalls=hub_shortfalls,
            )
        )
    return tuple(results)


def _sellable(
    *,
    hub: str,
    cap: int,
    guarantee: int,
    retained: int,
    committed: int,
    pool_retained: int,
    pool_committed: int,
    scenarios: tuple[ScenarioLoad, ...],
    pool: str | None,
    pause: Pause | None,
) -> tuple[int, list[str]]:
    """可出售量与阻塞原因。

    约束：枢纽上限、池保证（各枢纽合计）、每个并发情景下的
    枢纽上限与池保证。当前占用在某情景下已不可兑现时直接阻塞。
    """
    if pause is not None:
        until = f"至第 {pause.until} 日" if pause.until is not None else "直至恢复"
        return 0, [f"运营暂停（{pause.reason}，自第 {pause.from_day} 日起{until}）"]
    if pool is None or cap <= 0:
        return 0, ["该窗口在评估日无合同保证"]

    reasons: list[str] = []
    bounds = [cap - retained - committed, guarantee - pool_retained - pool_committed]
    if bounds[0] <= 0:
        reasons.append(
            f"枢纽合同上限 {cap} 席已被保留 {retained} + 已接受方案 {committed} 占满"
        )
    if bounds[1] <= 0:
        reasons.append(
            f"席位池 {pool} 保证 {guarantee} 席已被各枢纽合计保留 "
            f"{pool_retained} + 方案 {pool_committed} 占满"
        )
    for load in scenarios:
        demand = dict(load.demand)
        total_demand = sum(demand.values())
        if not load.feasible:
            parts = []
            if load.pool_shortfall:
                parts.append(f"池缺口 {load.pool_shortfall} 席")
            for h, short in load.hub_shortfalls:
                parts.append(f"{h} 枢纽缺口 {short} 席")
            reasons.append(
                f"并发情景 {load.group} 需求合计 {total_demand} 席，"
                f"当前占用下已不可兑现（{'，'.join(parts)}）"
            )
        own_bound = cap - retained - committed - demand.get(hub, 0)
        pool_bound = guarantee - pool_retained - pool_committed - total_demand
        bounds.extend([own_bound, pool_bound])
        if load.feasible and min(own_bound, pool_bound) <= 0:
            reasons.append(
                f"并发情景 {load.group} 需求合计 {total_demand} 席，"
                f"计入情景余量后无可售席位"
            )
    sellable = max(0, min(bounds))
    if sellable == 0 and not reasons:
        reasons.append("无余量")
    return sellable, reasons


# ---------------------------------------------------------------- 方案与更正


def check_proposal(
    proposal: Proposal,
    contracts: list[Contract],
    carrier_events: list[CarrierEvent],
    scenarios: list[Scenario],
    proposals: list[Proposal],
    pauses: list[Pause],
) -> tuple[bool, list[str]]:
    """原子校验一个方案：生效期内每一天都有足够可售量才接受。

    已接受方案已计入 proposals，因此不会与既有方案重复使用同一份空余量。
    """
    reasons: list[str] = []
    for day in range(proposal.start, proposal.end + 1):
        states = evaluate_windows(
            contracts, carrier_events, scenarios, proposals, pauses, day
        )
        state = states.get((proposal.hub, proposal.window))
        if state is None:
            reasons.append(f"第 {day} 日：窗口无合同或情景定义")
            continue
        if state.sellable < proposal.seats:
            detail = "；".join(state.blocked) if state.blocked else "无余量"
            reasons.append(
                f"第 {day} 日：可售 {state.sellable} 席 < 申请 {proposal.seats} 席（{detail}）"
            )
    return not reasons, reasons


@dataclass(frozen=True)
class AffectedTicket:
    """更正后无法被保障的既有出票/方案。"""

    ticket: str  # 承运订单号或方案号
    kind: str  # hold | proposal
    hub: str
    seats: int
    day: int
    reason: str


@dataclass(frozen=True)
class CorrectionImpact:
    """一次履约更正/承诺缩减的影响核对结果。"""

    affected_windows: tuple[tuple[str, str, int], ...]  # (hub, window, day)
    at_risk: tuple[AffectedTicket, ...]
    summary: tuple[str, ...]


def impact_of_change(
    contracts_before: list[Contract],
    contracts_after: list[Contract],
    carrier_events_before: list[CarrierEvent],
    carrier_events_after: list[CarrierEvent],
    scenarios: list[Scenario],
    proposals: list[Proposal],
    pauses: list[Pause],
    day_from: int,
    day_to: int,
) -> CorrectionImpact:
    """对比更正前后 [day_from, day_to] 内各窗口可售状态，并列出
    因容量收紧而无法保障的既有出票与方案。"""
    affected: set[tuple[str, str, int]] = set()
    at_risk: set[AffectedTicket] = set()
    after_by_day: dict[int, dict[tuple[str, str], WindowState]] = {}
    for day in range(day_from, day_to + 1):
        before = evaluate_windows(
            contracts_before, carrier_events_before, scenarios, proposals, pauses, day
        )
        after = evaluate_windows(
            contracts_after, carrier_events_after, scenarios, proposals, pauses, day
        )
        after_by_day[day] = after
        for key, after_state in after.items():
            before_state = before.get(key)
            changed = before_state is None or (
                before_state.sellable != after_state.sellable
                or before_state.blocked != after_state.blocked
            )
            if changed:
                affected.add((key[0], key[1], day))
        at_risk.update(
            _at_risk_tickets(contracts_after, carrier_events_after, proposals, day)
        )
    summary = []
    for hub, window, day in sorted(affected):
        state = after_by_day[day][(hub, window)]
        summary.append(
            f"第 {day} 日 {hub}/{window}：可售 {state.sellable} 席"
            + (f"（{'；'.join(state.blocked)}）" if state.blocked else "")
        )
    return CorrectionImpact(
        affected_windows=tuple(sorted(affected)),
        at_risk=tuple(sorted(at_risk, key=lambda t: (t.day, t.hub, t.ticket, t.reason))),
        summary=tuple(summary),
    )


def _at_risk_tickets(
    contracts: list[Contract],
    carrier_events: list[CarrierEvent],
    proposals: list[Proposal],
    day: int,
) -> list[AffectedTicket]:
    """评估日内容量收紧后按优先级无法保障的出票/方案。

    confirmed 订单优先（confirmed_first），其后 offered 订单，
    最后是已接受方案；同一级别内先到期者优先保留。
    """
    effective = effective_contracts(contracts, day)
    holds = active_holds(carrier_events, day)
    active_props = [p for p in proposals if p.start <= day <= p.end]

    tickets: list[tuple[int, int, str, str, str, int, str]] = []
    # (rank, start, kind, id, hub, seats, pool)
    for h in holds:
        rank = RANK_CONFIRMED_HOLD if h.state == "confirmed" else RANK_OFFERED_HOLD
        tickets.append((rank, h.since, "hold", h.order, h.hub, h.seats, h.pool))
    for p in active_props:
        tickets.append((RANK_PROPOSAL, p.start, "proposal", p.proposal, p.hub, p.seats, p.pool))

    at_risk: list[AffectedTicket] = []
    # 枢纽上限层面
    by_hub_window: dict[tuple[str, str, str], list] = {}
    for t in tickets:
        _rank, _start, _kind, _id, hub, _seats, pool = t
        window = _window_of(contracts, proposals, hub, pool)
        by_hub_window.setdefault((pool, hub, window), []).append(t)
    for (pool, hub, window), group in by_hub_window.items():
        line = effective.get((pool, hub, window))
        cap = line.seats if line else 0
        used = 0
        for rank, start, kind, ident, _h, seats, _p in sorted(group):
            if used + seats > cap:
                at_risk.append(
                    AffectedTicket(
                        ticket=ident, kind=kind, hub=hub, seats=seats, day=day,
                        reason=f"枢纽 {hub} 合同上限 {cap} 席不足",
                    )
                )
            else:
                used += seats
    # 池保证层面（共同承运人跨枢纽扣减）
    by_pool: dict[str, list] = {}
    for t in tickets:
        by_pool.setdefault(t[6], []).append(t)
    for pool, group in by_pool.items():
        windows = {_window_of(contracts, proposals, t[4], pool) for t in group}
        guarantee = max(
            (pool_guarantee(contracts, pool, w, day) for w in windows), default=0
        )
        used = 0
        for rank, start, kind, ident, hub, seats, _p in sorted(group):
            if used + seats > guarantee:
                at_risk.append(
                    AffectedTicket(
                        ticket=ident, kind=kind, hub=hub, seats=seats, day=day,
                        reason=f"席位池 {pool} 保证 {guarantee} 席不足（跨枢纽合计）",
                    )
                )
            else:
                used += seats
    return at_risk


def _window_of(
    contracts: list[Contract], proposals: list[Proposal], hub: str, pool: str
) -> str:
    for c in contracts:
        if c.hub == hub and c.pool == pool:
            return c.window
    for p in proposals:
        if p.hub == hub and p.pool == pool:
            return p.window
    return ""
