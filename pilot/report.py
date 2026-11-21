"""报告渲染：试验分析与窗口决策的中文 Markdown 报告。"""
from __future__ import annotations

from .analysis import EffectEstimate, TrialAssessment
from .capacity import WindowState


def _pct(x: float) -> str:
    return f"{x * 100:.1f}%"


def _pp(x: float) -> str:
    return f"{x * 100:+.1f}pp"


def _effect_line(name: str, e: EffectEstimate) -> str:
    ci = f"[{_pct(e.ci_lo)}, {_pct(e.ci_hi)}]" if e.se == e.se else "[无法计算]"
    flags = []
    if e.degenerate:
        flags.append("集群间变异为零，区间偏窄")
    if e.n_protection < 2 or e.n_ordinary < 2:
        flags.append("单臂观测过少，区间不可信")
    flag = f"（{'；'.join(flags)}）" if flags else ""
    return (
        f"| {name} | {_pct(e.rate_protection)}（{e.n_protection}）"
        f" | {_pct(e.rate_ordinary)}（{e.n_ordinary}）"
        f" | {_pp(e.diff)} | {ci}{flag} |"
    )


def render_trial_report(assessment: TrialAssessment, *, run_id: str, inputs_hash: str) -> str:
    itt = assessment.itt
    cov = assessment.coverage
    lines: list[str] = []
    lines.append(f"# 接驳保护试验分析报告（{run_id}）")
    lines.append("")
    lines.append(f"- 输入指纹：`{inputs_hash}`")
    lines.append(f"- 观察期截止：第 {cov.observation_end} 日（试验相对日）")
    lines.append(
        "- 口径：按发出邀约时的分组（ITT）；聚类单位 = 席位池；"
        "未购票计入主分析分母且视为未完成（敏感性分析剔除）；"
        "改签事件去重；跨出观察期未决行程不计成败"
    )
    lines.append("")

    lines.append("## 数据覆盖")
    lines.append("")
    lines.append("| 组别 | 受邀 | 购票 | 未购票 | 完成 | 失败 | 未决 | 改走地面 | 改签 |")
    lines.append("| --- | --- | --- | --- | --- | --- | --- | --- | --- |")
    for group, label in (("protection", "保护组"), ("ordinary", "普通组")):
        c = itt.counts[group]
        lines.append(
            f"| {label} | {c.invited} | {c.booked} | {c.not_booked} | {c.completed}"
            f" | {c.failed} | {c.censored} | {c.switched_to_ground} | {c.rebooked} |"
        )
    lines.append("")
    if cov.offer_mismatches:
        lines.append(f"- ⚠ 实发与分配组不一致的邀约：{', '.join(cov.offer_mismatches)}")
    else:
        lines.append("- 实发权益与分配组全部一致。")
    if cov.journey_censored:
        lines.append(f"- 跨出观察期未决的受邀行程：{', '.join(cov.journey_censored)}")
    if cov.ordinary_censored:
        lines.append(f"- 未查明去向的普通旅客观测：{', '.join(cov.ordinary_censored)}")
    lines.append("")

    lines.append("## 整趟行程完成率（ITT）")
    lines.append("")
    lines.append("| 口径 | 保护组完成率（n） | 普通组完成率（n） | 差值 | 95% 区间 |")
    lines.append("| --- | --- | --- | --- | --- |")
    lines.append(_effect_line("主分析（未购票计未完成）", itt.primary))
    lines.append(_effect_line("敏感性（仅可判定行程）", itt.sensitivity))
    lines.append("")
    lines.append(
        f"- 随机化推断：在与设计一致的 {itt.ri_permutations} 种分配下，"
        f"单侧 p = {itt.ri_p_primary:.4f}（α = {assessment.alpha}）。"
    )
    lines.append(
        "- 注：聚类单位少时聚类稳健区间仅作参考，以随机化推断为准。"
    )
    lines.append("")

    sp = assessment.spillover
    lines.append("## 普通旅客（未受邀）外溢影响")
    lines.append("")
    lines.append(
        f"- 可用观测 {sp.observations} 条，未查明去向 {sp.censored} 条"
        + (f"（{', '.join(sp.censored_ids)}）" if sp.censored_ids else "")
    )
    if sp.overall is not None:
        lines.append("")
        lines.append("| 范围 | 保护周错过率（n） | 普通周错过率（n） | 差值 | 95% 区间 |")
        lines.append("| --- | --- | --- | --- | --- |")
        lines.append(_effect_line("合计", sp.overall))
        for hub, est in sorted(sp.per_hub.items()):
            lines.append(_effect_line(f"{hub} 枢纽", est))
        lines.append("")
        if sp.ri_p_overall is not None:
            lines.append(f"- 合计差值随机化检验 p = {sp.ri_p_overall:.4f}。")
        lines.append("- 差值为正表示保护周普通旅客错过率更高（接驳席位被优先占用）。")
    lines.append("")

    lines.append("## 各枢纽扩围评估")
    lines.append("")
    lines.append("| 枢纽 | 区块（保护/普通） | 效果差值 | 结论 | 阻塞原因 |")
    lines.append("| --- | --- | --- | --- | --- |")
    for hub, a in sorted(assessment.hubs.items()):
        effect = _pp(a.effect.diff) if a.effect else "—"
        verdict = "支持扩围" if a.supported else "暂不支持"
        reasons = "；".join(a.reasons) if a.reasons else "—"
        lines.append(
            f"| {hub} | {a.blocks_protection}/{a.blocks_ordinary} | {effect}"
            f" | {verdict} | {reasons} |"
        )
    lines.append("")
    supported = assessment.supported_hubs
    if supported:
        lines.append(f"试验证据支持的枢纽：{', '.join(supported)}（仍需通过可兑现核对）。")
    else:
        lines.append("当前没有枢纽仅凭试验证据即可扩围。")
    lines.append("")
    return "\n".join(lines)


def render_windows_report(
    states: dict[tuple[str, str], WindowState],
    *,
    day: int,
    assessment: TrialAssessment,
    run_id: str,
    inputs_hash: str,
) -> str:
    lines: list[str] = []
    lines.append(f"# 窗口可出售评估（{run_id}）")
    lines.append("")
    lines.append(f"- 评估日：第 {day} 日（试验相对日）；输入指纹：`{inputs_hash}`")
    lines.append("- 口径：池级保证不可跨枢纽相加；已出票保留至承运接手或旅客解除；")
    lines.append("  可售量须在每个并发延误情景下仍可兑现；confirmed_first 优先保障。")
    lines.append("")
    lines.append("| 枢纽 | 窗口 | 池 | 枢纽上限 | 池保证 | 已保留 | 已接受方案 | 可售 | 阻塞原因 |")
    lines.append("| --- | --- | --- | --- | --- | --- | --- | --- | --- |")
    for (hub, window), s in sorted(states.items()):
        blocked = "；".join(s.blocked) if s.blocked else "—"
        lines.append(
            f"| {hub} | {window} | {s.pool or '—'} | {s.cap} | {s.guarantee}"
            f" | {s.retained} | {s.committed} | {s.sellable} | {blocked} |"
        )
    lines.append("")
    for (hub, window), s in sorted(states.items()):
        if s.holds:
            holds = "、".join(
                f"{h.order}（{h.state}，{h.seats} 席，自第 {h.since} 日）" for h in s.holds
            )
            lines.append(f"- {hub}/{window} 在保留的出票：{holds}")
    lines.append("")

    lines.append("## 可扩围名单（试验证据 ∩ 可兑现）")
    lines.append("")
    rows = []
    for (hub, window), s in sorted(states.items()):
        a = assessment.hubs.get(hub)
        trial_ok = a.supported if a else False
        capacity_ok = s.sellable > 0
        rows.append((hub, window, trial_ok, capacity_ok, s, a))
    expandable = [r for r in rows if r[2] and r[3]]
    lines.append("| 枢纽 | 窗口 | 试验证据 | 可兑现 | 结论 |")
    lines.append("| --- | --- | --- | --- | --- |")
    for hub, window, trial_ok, capacity_ok, s, a in rows:
        verdict = "可扩围" if (trial_ok and capacity_ok) else "不可扩围"
        lines.append(
            f"| {hub} | {window} | {'支持' if trial_ok else '不支持'}"
            f" | {'可兑现' if capacity_ok else '不可兑现'} | {verdict} |"
        )
    lines.append("")
    if expandable:
        names = "、".join(f"{h}/{w}" for h, w, *_ in expandable)
        lines.append(f"可扩围：{names}")
    else:
        lines.append("当前无可扩围窗口。")
    for hub, window, trial_ok, capacity_ok, s, a in rows:
        if a and a.effect and a.effect.diff > 0 and not capacity_ok:
            lines.append(
                f"- 注意：{hub} 试验显示改善但窗口不可兑现"
                "（改善若只能靠挤占已承诺席位实现，不得列入扩围）。"
            )
    lines.append("")
    return "\n".join(lines)
