"""试验分析：ITT 效果、聚类稳健不确定性、随机化推断、外溢与覆盖。

设计事实（来自 README 与 randomization.csv）：

- 分配在区块（枢纽 × 周）层面；每周的东西枢纽共用一个地面席位池；
- 周级先定池内保护比例（本资料中第 3、6 周为 0%，其余周 50%），
  中间比例的周再分配枢纽 —— 随机化推断只重排这些周内的枢纽归属；
- 同一席位池覆盖多个班次，池内旅客不独立：聚类单位取席位池。
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from .journeys import (
    STATUS_CENSORED,
    STATUS_COMPLETED,
    STATUS_FAILED,
    STATUS_NOT_BOOKED,
    Journey,
    build_journeys,
)
from .sources import Block, OrdinaryObservation, Sources

# 95% 双侧 t 临界值（df=1..30），超出按 1.96 近似。
_T95 = [
    12.706, 4.303, 3.182, 2.776, 2.571, 2.447, 2.365, 2.306, 2.262, 2.228,
    2.201, 2.179, 2.160, 2.145, 2.131, 2.120, 2.110, 2.101, 2.093, 2.086,
    2.080, 2.074, 2.069, 2.064, 2.060, 2.056, 2.052, 2.048, 2.045, 2.042,
]


def t_critical_95(df: int) -> float:
    if df < 1:
        return float("nan")
    return _T95[df - 1] if df <= 30 else 1.96


# ---------------------------------------------------------------- 通用估计


@dataclass(frozen=True)
class EffectEstimate:
    """两组比例差（protection − ordinary）及聚类稳健不确定性。"""

    diff: float
    se: float
    ci_lo: float
    ci_hi: float
    n_protection: int
    n_ordinary: int
    rate_protection: float
    rate_ordinary: float
    clusters: int
    degenerate: bool  # 集群间变异为零，区间偏窄，需以随机化推断为准


def ols_cr1(y: list[float], x: list[int], clusters: list[str]) -> tuple[float, float, bool]:
    """y ~ 1 + x 的最小二乘斜率与 CR1 聚类稳健标准误。

    返回 (斜率, 标准误, 是否退化)。集群数 < 2 时标准误为 nan。
    """
    n = len(y)
    n1 = sum(x)
    n0 = n - n1
    if n1 == 0 or n0 == 0:
        raise ValueError("两组都需要至少一个观测")
    sum_y1 = sum(v for v, g in zip(y, x) if g)
    sum_y0 = sum(y) - sum_y1
    mean1, mean0 = sum_y1 / n1, sum_y0 / n0
    slope = mean1 - mean0
    intercept = mean0

    # 每个集群的得分和 s_g = Σ (u_i, u_i * x_i)
    scores: dict[str, list[float]] = {}
    for v, g, c in zip(y, x, clusters):
        u = v - (intercept + slope * g)
        s = scores.setdefault(c, [0.0, 0.0])
        s[0] += u
        s[1] += u * g
    g_count = len(scores)
    if g_count < 2:
        return slope, float("nan"), True

    # (X'X)^-1，X = [1, x]
    det = n * n1 - n1 * n1
    xtx_inv = [[n1 / det, -n1 / det], [-n1 / det, n / det]]
    # meat = Σ s_g s_g'
    m00 = sum(s[0] * s[0] for s in scores.values())
    m01 = sum(s[0] * s[1] for s in scores.values())
    m11 = sum(s[1] * s[1] for s in scores.values())
    # V = (X'X)^-1 meat (X'X)^-1
    a, b = xtx_inv[0]
    c, d = xtx_inv[1]
    t00 = a * m00 + b * m01
    t01 = a * m01 + b * m11
    t10 = c * m00 + d * m01
    t11 = c * m01 + d * m11
    v11 = t10 * b + t11 * d
    # CR1 小样本修正
    correction = (g_count / (g_count - 1)) * ((n - 1) / (n - 2)) if n > 2 else 1.0
    var = max(v11 * correction, 0.0)
    se = math.sqrt(var)
    # 结果量为 O(1)：标准误低于容差即视为集群间无变异（区间退化）。
    return slope, se, se < 1e-9


def estimate_effect(
    rows: list[tuple[float, int, str]],
) -> EffectEstimate:
    """rows 为 (结果值, 是否保护组, 聚类 id)。"""
    y = [r[0] for r in rows]
    x = [r[1] for r in rows]
    c = [r[2] for r in rows]
    slope, se, degenerate = ols_cr1(y, x, c)
    n1 = sum(x)
    mean1 = sum(v for v, g in zip(y, x) if g) / n1
    mean0 = sum(v for v, g in zip(y, x) if not g) / (len(y) - n1)
    crit = t_critical_95(len(set(c)) - 1)
    half = se * crit if not math.isnan(se) else float("nan")
    return EffectEstimate(
        diff=slope,
        se=se,
        ci_lo=slope - half,
        ci_hi=slope + half,
        n_protection=n1,
        n_ordinary=len(y) - n1,
        rate_protection=mean1,
        rate_ordinary=mean0,
        clusters=len(set(c)),
        degenerate=degenerate,
    )


# ---------------------------------------------------------------- 随机化推断


def assignment_space(blocks: list[Block]) -> list[dict[str, str]]:
    """枚举与设计一致的全部分配：固定周级保护比例，只重排
    “一个保护 + 一个普通”周内的枢纽归属。"""
    by_week: dict[int, list[Block]] = {}
    for b in blocks:
        by_week.setdefault(b.week, []).append(b)
    flippable = [
        week
        for week, bs in sorted(by_week.items())
        if {b.group for b in bs} == {"protection", "ordinary"}
    ]
    assignments: list[dict[str, str]] = []
    for mask in range(1 << len(flippable)):
        labels: dict[str, str] = {}
        for week, bs in by_week.items():
            if week in flippable:
                flip = (mask >> flippable.index(week)) & 1
                ordered = sorted(bs, key=lambda b: b.block)
                labels[ordered[0].block] = "protection" if flip else "ordinary"
                labels[ordered[1].block] = "ordinary" if flip else "protection"
            else:
                for b in bs:
                    labels[b.block] = b.group
        assignments.append(labels)
    return assignments


def randomization_pvalue(
    observed: float,
    stats_under_null: list[float],
) -> tuple[float, int]:
    """单侧（越大越支持保护有效）精确 p 值与置换总数。"""
    hits = sum(1 for s in stats_under_null if s >= observed - 1e-12)
    return hits / len(stats_under_null), len(stats_under_null)


# ---------------------------------------------------------------- ITT 分析


@dataclass(frozen=True)
class GroupCounts:
    invited: int
    booked: int
    not_booked: int
    completed: int
    failed: int
    censored: int
    switched_to_ground: int
    rebooked: int


@dataclass(frozen=True)
class IttResult:
    """按发出邀约时分组的 ITT 结果。

    primary：受邀未购票计入分母且视为整趟行程未完成；
    sensitivity：未购票与删失一并剔除，仅在可判定行程中比较。
    """

    counts: dict[str, GroupCounts]
    primary: EffectEstimate
    sensitivity: EffectEstimate
    ri_p_primary: float
    ri_permutations: int


def _group_counts(journeys: list[Journey], group: str) -> GroupCounts:
    rows = [j for j in journeys if j.group == group]
    return GroupCounts(
        invited=len(rows),
        booked=sum(j.booked for j in rows),
        not_booked=sum(j.status == STATUS_NOT_BOOKED for j in rows),
        completed=sum(j.status == STATUS_COMPLETED for j in rows),
        failed=sum(j.status == STATUS_FAILED for j in rows),
        censored=sum(j.status == STATUS_CENSORED for j in rows),
        switched_to_ground=sum(j.switched_to_ground for j in rows),
        rebooked=sum(j.rebooked for j in rows),
    )


def _primary_rows(journeys: list[Journey]) -> list[tuple[float, int, str]]:
    return [
        (1.0 if j.status == STATUS_COMPLETED else 0.0, int(j.group == "protection"), j.pool)
        for j in journeys
        if j.status != STATUS_CENSORED
    ]


def _sensitivity_rows(journeys: list[Journey]) -> list[tuple[float, int, str]]:
    return [
        (1.0 if j.status == STATUS_COMPLETED else 0.0, int(j.group == "protection"), j.pool)
        for j in journeys
        if j.status in (STATUS_COMPLETED, STATUS_FAILED)
    ]


def _diff_stat(journeys: list[Journey], labels: dict[str, str]) -> float:
    totals = {"protection": [0, 0], "ordinary": [0, 0]}
    for j in journeys:
        if j.status == STATUS_CENSORED:
            continue
        group = labels[j.block]
        totals[group][0] += 1
        totals[group][1] += int(j.status == STATUS_COMPLETED)
    return (
        totals["protection"][1] / totals["protection"][0]
        - totals["ordinary"][1] / totals["ordinary"][0]
    )


def run_itt(journeys: list[Journey], blocks: list[Block]) -> IttResult:
    counts = {
        group: _group_counts(journeys, group) for group in ("protection", "ordinary")
    }
    primary = estimate_effect(_primary_rows(journeys))
    sensitivity = estimate_effect(_sensitivity_rows(journeys))

    assignments = assignment_space(blocks)
    observed_labels = {b.block: b.group for b in blocks}
    observed = _diff_stat(journeys, observed_labels)
    null_stats = [_diff_stat(journeys, labels) for labels in assignments]
    ri_p, n_perm = randomization_pvalue(observed, null_stats)
    return IttResult(
        counts=counts,
        primary=primary,
        sensitivity=sensitivity,
        ri_p_primary=ri_p,
        ri_permutations=n_perm,
    )


# ---------------------------------------------------------------- 外溢分析


@dataclass(frozen=True)
class SpilloverResult:
    """普通旅客（未受邀）在保护周与普通周的错过率差。"""

    overall: EffectEstimate | None
    per_hub: dict[str, EffectEstimate]
    ri_p_overall: float | None
    observations: int
    censored: int
    censored_ids: tuple[str, ...]


def run_spillover(
    ordinary: list[OrdinaryObservation], blocks: list[Block]
) -> SpilloverResult:
    label = {(b.hub, b.week): b.group for b in blocks}
    usable = [o for o in ordinary if o.complete and o.missed is not None]
    censored = [o for o in ordinary if not (o.complete and o.missed is not None)]

    def rows_for(obs: list[OrdinaryObservation]) -> list[tuple[float, int, str]]:
        return [
            (o.missed / o.guests, int(label[(o.hub, o.week)] == "protection"), o.pool)
            for o in obs
        ]

    overall = estimate_effect(rows_for(usable)) if usable else None
    per_hub: dict[str, EffectEstimate] = {}
    for hub in sorted({o.hub for o in ordinary}):
        hub_obs = [o for o in usable if o.hub == hub]
        groups = {label[(o.hub, o.week)] for o in hub_obs}
        if len(groups) == 2:
            per_hub[hub] = estimate_effect(rows_for(hub_obs))

    ri_p = None
    if usable:
        by_week_block = {(b.hub, b.week): b.block for b in blocks}
        assignments = assignment_space(blocks)

        def stat(labels: dict[str, str]) -> float:
            sums = {"protection": [0.0, 0], "ordinary": [0.0, 0]}
            for o in usable:
                block_id = by_week_block[(o.hub, o.week)]
                group = labels[block_id]
                sums[group][0] += o.missed / o.guests
                sums[group][1] += 1
            return (
                sums["protection"][0] / sums["protection"][1]
                - sums["ordinary"][0] / sums["ordinary"][1]
            )

        observed_labels = {b.block: b.group for b in blocks}
        observed = stat(observed_labels)
        null_stats = [stat(a) for a in assignments]
        ri_p, _ = randomization_pvalue(observed, null_stats)

    return SpilloverResult(
        overall=overall,
        per_hub=per_hub,
        ri_p_overall=ri_p,
        observations=len(usable),
        censored=len(censored),
        censored_ids=tuple(o.observation for o in censored),
    )


# ---------------------------------------------------------------- 覆盖与口径核对


@dataclass(frozen=True)
class Coverage:
    per_group: dict[str, GroupCounts]
    offer_mismatches: tuple[str, ...]  # 实发与分配组不一致的邀约
    journey_censored: tuple[str, ...]
    ordinary_censored: tuple[str, ...]
    observation_end: int


def run_coverage(
    journeys: list[Journey], sources: Sources, observation_end: int
) -> Coverage:
    blocks = sources.block_map()
    mismatches = tuple(
        inv.invite
        for inv in sources.invitations
        if inv.offered != blocks[inv.block].group
    )
    return Coverage(
        per_group={g: _group_counts(journeys, g) for g in ("protection", "ordinary")},
        offer_mismatches=mismatches,
        journey_censored=tuple(j.invite for j in journeys if j.status == STATUS_CENSORED),
        ordinary_censored=tuple(
            o.observation for o in sources.ordinary if not (o.complete and o.missed is not None)
        ),
        observation_end=observation_end,
    )


# ---------------------------------------------------------------- 枢纽扩围评估


@dataclass(frozen=True)
class HubAssessment:
    hub: str
    blocks_protection: int
    blocks_ordinary: int
    effect: EffectEstimate | None
    invited_censored: int
    ordinary_censored: int
    coverage_ok: bool
    statistical_ok: bool
    supported: bool
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class TrialAssessment:
    itt: IttResult
    spillover: SpilloverResult
    coverage: Coverage
    hubs: dict[str, HubAssessment]
    alpha: float
    min_blocks_per_arm: int = 2

    @property
    def supported_hubs(self) -> list[str]:
        return [h for h, a in self.hubs.items() if a.supported]


def assess_hubs(
    journeys: list[Journey],
    sources: Sources,
    itt: IttResult,
    alpha: float,
    min_blocks_per_arm: int = 2,
) -> dict[str, HubAssessment]:
    """逐枢纽评估试验证据是否足以支持扩围。

    统计门槛：该枢纽聚类稳健区间不含 0（有益方向）且整体随机化
    p 值不超过 alpha；覆盖门槛：每组至少 min_blocks_per_arm 个区块、
    受邀行程与普通旅客观测均无删失。
    """
    blocks = sources.block_map()
    hubs: dict[str, HubAssessment] = {}
    for hub in sorted({b.hub for b in sources.blocks}):
        hub_journeys = [j for j in journeys if j.hub == hub]
        n_prot = sum(1 for b in sources.blocks if b.hub == hub and b.group == "protection")
        n_ord = sum(1 for b in sources.blocks if b.hub == hub and b.group == "ordinary")
        groups_present = {j.group for j in hub_journeys if j.status != STATUS_CENSORED}
        effect = None
        if groups_present == {"protection", "ordinary"}:
            effect = estimate_effect(_primary_rows(hub_journeys))

        invited_censored = sum(j.status == STATUS_CENSORED for j in hub_journeys)
        ordinary_censored = sum(
            1
            for o in sources.ordinary
            if o.hub == hub and not (o.complete and o.missed is not None)
        )

        reasons: list[str] = []
        coverage_ok = True
        if n_prot < min_blocks_per_arm or n_ord < min_blocks_per_arm:
            coverage_ok = False
            reasons.append(
                f"区块数不足（保护 {n_prot} / 普通 {n_ord}，需各 ≥ {min_blocks_per_arm}）"
            )
        if invited_censored:
            coverage_ok = False
            reasons.append(f"{invited_censored} 个受邀行程跨出观察期未决")
        if ordinary_censored:
            coverage_ok = False
            reasons.append(f"{ordinary_censored} 条普通旅客观测未查明去向")

        statistical_ok = True
        if effect is None:
            statistical_ok = False
            reasons.append("可判定行程不足以估计组间差异")
        else:
            if not (effect.ci_lo > 0):
                statistical_ok = False
                reasons.append(
                    f"聚类稳健 95% 区间含 0（[{effect.ci_lo:.3f}, {effect.ci_hi:.3f}]）"
                )
            if effect.degenerate:
                reasons.append("集群间变异为零，区间偏窄，需以随机化推断为准")
            if itt.ri_p_primary > alpha:
                statistical_ok = False
                reasons.append(
                    f"整体随机化检验 p={itt.ri_p_primary:.4f} 未过 α={alpha}"
                    f"（当前设计最多 {itt.ri_permutations} 种分配）"
                )

        supported = coverage_ok and statistical_ok
        hubs[hub] = HubAssessment(
            hub=hub,
            blocks_protection=n_prot,
            blocks_ordinary=n_ord,
            effect=effect,
            invited_censored=invited_censored,
            ordinary_censored=ordinary_censored,
            coverage_ok=coverage_ok,
            statistical_ok=statistical_ok,
            supported=supported,
            reasons=tuple(reasons),
        )
    return hubs


def run_trial(sources: Sources, observation_end: int, alpha: float = 0.05) -> TrialAssessment:
    """完整试验分析：ITT + 外溢 + 覆盖 + 枢纽评估。"""
    journeys = build_journeys(sources, observation_end)
    itt = run_itt(journeys, list(sources.blocks))
    spillover = run_spillover(list(sources.ordinary), list(sources.blocks))
    coverage = run_coverage(journeys, sources, observation_end)
    hubs = assess_hubs(journeys, sources, itt, alpha)
    return TrialAssessment(
        itt=itt, spillover=spillover, coverage=coverage, hubs=hubs, alpha=alpha
    )
