"""行程结果构建：去重改签、观察期截断与删失。

规则（分析口径的一部分，随每次运行记录）：

- 被 ``replaces`` 指向的事件被替代事件取代，改签后的到达不重复计数；
- 观察期截止日之后才发生的事件视为尚未观察到；若取代事件落在观察期外，
  该行程记为未决（删失），不回退到被替代的旧事件；
- 地面段为最终目的地接驳：地面 arrived 记为完成，missed/cancelled 记为失败；
- 只有航段事件、没有可判定的地面事件时，行程去向未明，记为删失；
- 受邀但未购票（booked=false）没有行程，单列 not_booked，
  在主分析中计入邀约组（整趟行程未完成），敏感性分析中按删失处理；
- 跨出观察期仍未查明去向的行程不计入成功也不计入失败。
"""
from __future__ import annotations

from dataclasses import dataclass

from .sources import Invitation, JourneyEvent, Sources

STATUS_COMPLETED = "completed"
STATUS_FAILED = "failed"
STATUS_CENSORED = "censored"
STATUS_NOT_BOOKED = "not_booked"

# 地面段终态到行程结果的映射。
_GROUND_OUTCOME = {
    "arrived": STATUS_COMPLETED,
    "missed": STATUS_FAILED,
    "cancelled": STATUS_FAILED,
}


@dataclass(frozen=True)
class Journey:
    """一个邀约对应的一趟行程（ITT 分析单元）。"""

    invite: str
    block: str
    hub: str
    week: int
    pool: str
    group: str  # 发出邀约时的分配组（ITT 分组依据）
    party: int
    invited: int
    booked: bool
    status: str
    completion_day: int | None
    air_state: str | None
    ground_state: str | None
    rebooked: bool  # 是否存在被替代的改签事件
    switched_to_ground: bool  # 航段取消后改走地面到达
    observed_events: tuple[str, ...]  # 观察期内、去重后的事件

    @property
    def completed(self) -> bool:
        return self.status == STATUS_COMPLETED


def _resolve_events(
    events: list[JourneyEvent], observation_end: int
) -> tuple[list[JourneyEvent], bool]:
    """应用替代关系并截断观察期，返回（可见事件, 是否发生改签）。"""
    replaced = {e.replaces for e in events if e.replaces}
    surviving = [e for e in events if e.event not in replaced]
    visible = [e for e in surviving if e.day <= observation_end]
    return visible, bool(replaced)


def build_journeys(sources: Sources, observation_end: int) -> list[Journey]:
    """按邀约构建行程结果。observation_end 为观察期截止日（含）。"""
    blocks = sources.block_map()
    by_invite: dict[str, list[JourneyEvent]] = {}
    for event in sources.journey_events:
        by_invite.setdefault(event.invite, []).append(event)

    journeys: list[Journey] = []
    for inv in sources.invitations:
        block = blocks[inv.block]
        visible, rebooked = _resolve_events(by_invite.get(inv.invite, []), observation_end)
        journeys.append(_journey_from_events(inv, block, visible, rebooked))
    return journeys


def _journey_from_events(inv: Invitation, block, visible: list[JourneyEvent], rebooked: bool) -> Journey:
    base = dict(
        invite=inv.invite,
        block=inv.block,
        hub=block.hub,
        week=block.week,
        pool=block.pool,
        group=block.group,
        party=inv.party,
        invited=inv.invited,
        booked=inv.booked,
        rebooked=rebooked,
    )
    if not inv.booked:
        return Journey(
            **base, status=STATUS_NOT_BOOKED, completion_day=None,
            air_state=None, ground_state=None, switched_to_ground=False,
            observed_events=(),
        )

    air = [e for e in visible if e.leg == "air"]
    ground = [e for e in visible if e.leg == "ground"]
    air_state = air[-1].state if air else None

    if not ground:
        # 已购票但观察期内没有可判定的地面事件：去向未明。
        return Journey(
            **base, status=STATUS_CENSORED, completion_day=None,
            air_state=air_state, ground_state=None, switched_to_ground=False,
            observed_events=tuple(e.event for e in visible),
        )

    final_ground = max(ground, key=lambda e: (e.day, e.event))
    status = _GROUND_OUTCOME[final_ground.state]
    switched = air_state == "cancelled" and final_ground.state == "arrived"
    return Journey(
        **base,
        status=status,
        completion_day=final_ground.day if status == STATUS_COMPLETED else None,
        air_state=air_state,
        ground_state=final_ground.state,
        switched_to_ground=switched,
        observed_events=tuple(e.event for e in visible),
    )


def default_observation_end(sources: Sources) -> int:
    """默认观察期截止日：行程事件的最大相对日。"""
    return max(e.day for e in sources.journey_events)
