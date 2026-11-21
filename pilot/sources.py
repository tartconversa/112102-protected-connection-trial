"""来源资料加载与解析。

所有表均为合成数据，时间以试验相对日表示。本模块只做解析与
最小校验（必填、取值域），业务规则在 journeys / analysis / capacity 中。
"""
from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path

GROUPS = ("protection", "ordinary")
LEGS = ("air", "ground")
EVENT_STATES = ("arrived", "cancelled", "missed")
CARRIER_STATES = ("offered", "confirmed", "carrier_accepted", "released")
# 承运订单的存续状态：offered/confirmed 期间席位被保留，
# carrier_accepted（承运侧接手）或 released（旅客解除权利）后释放。
HOLDING_STATES = ("offered", "confirmed")
TERMINAL_CARRIER_STATES = ("carrier_accepted", "released")


@dataclass(frozen=True)
class Block:
    """随机化区块：枢纽 × 周，分配的试验组与所属席位池。"""

    block: str
    hub: str
    week: int
    group: str
    pool: str


@dataclass(frozen=True)
class Invitation:
    """邀约记录。invited 为邀约发出的试验相对日。"""

    invite: str
    block: str
    party: int
    invited: int
    offered: str
    booked: bool


@dataclass(frozen=True)
class JourneyEvent:
    """行程事件。replaces 指向被本事件替代（改签）的事件。"""

    event: str
    invite: str
    day: int
    leg: str
    state: str
    adults: int
    replaces: str


@dataclass(frozen=True)
class OrdinaryObservation:
    """未参与邀约的普通旅客观测。complete=no 表示跨出观察期仍未查明，
    此时 missed 无核验值（None）。"""

    observation: str
    hub: str
    week: int
    pool: str
    guests: int
    missed: int | None
    complete: bool


@dataclass(frozen=True)
class Contract:
    """联运合同行。seats 为池级保证：同一池被多个枢纽引用时不可相加。
    available_day 为该行生效日；同一 (pool, hub, window) 以最新生效行为准，
    可用于表示承运人缩减承诺。"""

    contract: str
    pool: str
    hub: str
    window: str
    seats: int
    priority: str
    available_day: int


@dataclass(frozen=True)
class Scenario:
    """并发延误情景。同一 simultaneous_group 的需求可能同时出现，
    seats 为该情景下所需承接人数。"""

    scenario: str
    window: str
    hub: str
    simultaneous_group: str
    missed_parties: int
    seats: int


@dataclass(frozen=True)
class CarrierEvent:
    """承运订单事件。保留各次独立事实；订单在 offered/confirmed 期间
    占用席位，直至 carrier_accepted 或 released。"""

    event: str
    order: str
    pool: str
    hub: str
    seats: int
    state: str
    day: int


@dataclass(frozen=True)
class Sources:
    blocks: tuple[Block, ...]
    invitations: tuple[Invitation, ...]
    journey_events: tuple[JourneyEvent, ...]
    ordinary: tuple[OrdinaryObservation, ...]
    contracts: tuple[Contract, ...]
    scenarios: tuple[Scenario, ...]
    carrier_events: tuple[CarrierEvent, ...]

    def block_map(self) -> dict[str, Block]:
        return {b.block: b for b in self.blocks}


def _bool(value: str, *, field: str, row: str) -> bool:
    if value in ("true", "yes"):
        return True
    if value in ("false", "no"):
        return False
    raise ValueError(f"{row}: 字段 {field} 无法解析为布尔值: {value!r}")


def _int(value: str, *, field: str, row: str) -> int:
    try:
        return int(value)
    except ValueError as exc:
        raise ValueError(f"{row}: 字段 {field} 无法解析为整数: {value!r}") from exc


def _optional_int(value: str, *, field: str, row: str) -> int | None:
    if value == "":
        return None
    return _int(value, field=field, row=row)


def _read(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as stream:
        return list(csv.DictReader(stream))


def load(directory: str | Path) -> Sources:
    """从目录加载全部来源表。"""
    root = Path(directory)

    blocks = tuple(
        Block(
            block=r["block"],
            hub=r["hub"],
            week=_int(r["week"], field="week", row=r["block"]),
            group=r["group"],
            pool=r["pool"],
        )
        for r in _read(root / "randomization.csv")
    )
    for b in blocks:
        if b.group not in GROUPS:
            raise ValueError(f"{b.block}: 未知试验组 {b.group!r}")

    invitations = tuple(
        Invitation(
            invite=r["invite"],
            block=r["block"],
            party=_int(r["party"], field="party", row=r["invite"]),
            invited=_int(r["invited"], field="invited", row=r["invite"]),
            offered=r["offered"],
            booked=_bool(r["booked"], field="booked", row=r["invite"]),
        )
        for r in _read(root / "invitations.csv")
    )

    events = tuple(
        JourneyEvent(
            event=r["event"],
            invite=r["invite"],
            day=_int(r["day"], field="day", row=r["event"]),
            leg=r["leg"],
            state=r["state"],
            adults=_int(r["adults"], field="adults", row=r["event"]),
            replaces=r["replaces"],
        )
        for r in _read(root / "journey_events.csv")
    )
    for e in events:
        if e.leg not in LEGS or e.state not in EVENT_STATES:
            raise ValueError(f"{e.event}: 未知 leg/state: {e.leg}/{e.state}")

    ordinary = tuple(
        OrdinaryObservation(
            observation=r["observation"],
            hub=r["hub"],
            week=_int(r["week"], field="week", row=r["observation"]),
            pool=r["pool"],
            guests=_int(r["guests"], field="guests", row=r["observation"]),
            missed=_optional_int(r["missed"], field="missed", row=r["observation"]),
            complete=_bool(r["complete"], field="complete", row=r["observation"]),
        )
        for r in _read(root / "ordinary_passenger.csv")
    )

    contracts = tuple(
        Contract(
            contract=r["contract"],
            pool=r["pool"],
            hub=r["hub"],
            window=r["window"],
            seats=_int(r["seats"], field="seats", row=r["contract"]),
            priority=r["priority"],
            available_day=_int(r["available_day"], field="available_day", row=r["contract"]),
        )
        for r in _read(root / "rescue_contracts.csv")
    )

    scenarios = tuple(
        Scenario(
            scenario=r["scenario"],
            window=r["window"],
            hub=r["hub"],
            simultaneous_group=r["simultaneous_group"],
            missed_parties=_int(r["missed_parties"], field="missed_parties", row=r["scenario"]),
            seats=_int(r["seats"], field="seats", row=r["scenario"]),
        )
        for r in _read(root / "disruption_scenarios.csv")
    )

    carrier_events = tuple(
        CarrierEvent(
            event=r["event"],
            order=r["order"],
            pool=r["pool"],
            hub=r["hub"],
            seats=_int(r["seats"], field="seats", row=r["event"]),
            state=r["state"],
            day=_int(r["day"], field="day", row=r["event"]),
        )
        for r in _read(root / "carrier_events.csv")
    )
    for c in carrier_events:
        if c.state not in CARRIER_STATES:
            raise ValueError(f"{c.event}: 未知承运状态 {c.state!r}")

    return Sources(
        blocks=blocks,
        invitations=invitations,
        journey_events=events,
        ordinary=ordinary,
        contracts=contracts,
        scenarios=scenarios,
        carrier_events=carrier_events,
    )
