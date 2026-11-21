"""命令行入口：分析、窗口评估、方案提交、更正核对与审计。

    python -m pilot analyze  [--observation-end N] [--alpha 0.05]
    python -m pilot windows  [--day N]
    python -m pilot submit   --hub east --window W7 --seats 2 --start 50 --end 56
    python -m pilot correct  --kind contract ... | --kind carrier ...
    python -m pilot pause    --hub east --window W7 --from-day 50 --reason ...
    python -m pilot audit    [--day N]

所有命令把口径、输入指纹与结果写入状态目录（默认 var/），
历史分析口径、已生效承诺与当前可售范围均可回查。
"""
from __future__ import annotations

import argparse
import sys
from dataclasses import asdict

from . import __version__
from .analysis import run_trial
from .capacity import (
    Pause,
    Proposal,
    check_proposal,
    evaluate_windows,
    impact_of_change,
)
from .journeys import default_observation_end
from .report import render_trial_report, render_windows_report
from .sources import CarrierEvent, Contract, load
from .store import Store, fingerprint

ANALYSIS_CALIBER_VERSION = 1
CAPACITY_CALIBER_VERSION = 1


def _trial_record(assessment, observation_end: int, alpha: float) -> dict:
    """分析口径与结果的可序列化摘要（随运行存档）。"""
    itt = assessment.itt
    return {
        "kind": "analyze",
        "caliber": {
            "version": ANALYSIS_CALIBER_VERSION,
            "grouping": "itt_by_invitation_block",
            "cluster": "pool",
            "observation_end": observation_end,
            "not_booked": "counted_not_completed_primary",
            "replaced_events": "deduped",
            "unresolved": "censored",
            "alpha": alpha,
        },
        "primary_diff": itt.primary.diff,
        "primary_ci": [itt.primary.ci_lo, itt.primary.ci_hi],
        "sensitivity_diff": itt.sensitivity.diff,
        "ri_p": itt.ri_p_primary,
        "ri_permutations": itt.ri_permutations,
        "hubs": {
            hub: {"supported": a.supported, "reasons": list(a.reasons)}
            for hub, a in assessment.hubs.items()
        },
    }


def cmd_analyze(args) -> int:
    sources = load(args.sources)
    store = Store(args.state)
    observation_end = (
        args.observation_end
        if args.observation_end is not None
        else default_observation_end(sources)
    )
    assessment = run_trial(sources, observation_end, alpha=args.alpha)
    run_id = store.new_run_id("analyze")
    inputs = fingerprint(args.sources, store)
    record = _trial_record(assessment, observation_end, args.alpha)
    record["inputs"] = inputs
    report = render_trial_report(assessment, run_id=run_id, inputs_hash=inputs)
    run_dir = store.record_run(run_id, record, report)
    print(report)
    print(f"运行档案：{run_dir}")
    return 0


def _load_capacity_inputs(args, store: Store):
    sources = load(args.sources)
    contracts = list(sources.contracts) + store.extra_contracts()
    carrier_events = list(sources.carrier_events) + store.extra_carrier_events()
    proposals = store.proposals()
    pauses = store.pauses()
    return sources, contracts, carrier_events, proposals, pauses


def cmd_windows(args) -> int:
    store = Store(args.state)
    sources, contracts, carrier_events, proposals, pauses = _load_capacity_inputs(
        args, store
    )
    day = args.day
    if day is None:
        day = max((c.available_day for c in contracts), default=0)
    observation_end = default_observation_end(sources)
    assessment = run_trial(sources, observation_end, alpha=args.alpha)
    states = evaluate_windows(
        contracts, carrier_events, list(sources.scenarios), proposals, pauses, day
    )
    run_id = store.new_run_id("windows")
    inputs = fingerprint(args.sources, store)
    report = render_windows_report(
        states, day=day, assessment=assessment, run_id=run_id, inputs_hash=inputs
    )
    record = {
        "kind": "windows",
        "caliber": {"version": CAPACITY_CALIBER_VERSION, "day": day},
        "inputs": inputs,
        "analysis_run_basis": _trial_record(assessment, observation_end, args.alpha),
        "windows": {
            f"{hub}/{window}": {
                "sellable": s.sellable,
                "blocked": list(s.blocked),
                "retained": s.retained,
                "committed": s.committed,
            }
            for (hub, window), s in states.items()
        },
    }
    run_dir = store.record_run(run_id, record, report)
    print(report)
    print(f"运行档案：{run_dir}")
    return 0


def cmd_submit(args) -> int:
    store = Store(args.state)
    sources, contracts, carrier_events, proposals, pauses = _load_capacity_inputs(
        args, store
    )
    pool = next(
        (c.pool for c in contracts if c.hub == args.hub and c.window == args.window),
        None,
    )
    proposal = Proposal(
        proposal=args.proposal_id or store.new_run_id("proposal"),
        hub=args.hub,
        window=args.window,
        pool=pool or "",
        seats=args.seats,
        start=args.start,
        end=args.end,
        day=args.day if args.day is not None else args.start,
        note=args.note or "",
    )
    ok, reasons = check_proposal(
        proposal,
        contracts,
        carrier_events,
        list(sources.scenarios),
        proposals,
        pauses,
    )
    if not ok:
        print(f"方案 {proposal.proposal} 被阻塞：")
        for reason in reasons:
            print(f"- {reason}")
        return 1
    store.add_proposal(proposal)
    print(
        f"方案 {proposal.proposal} 已接受：{proposal.hub}/{proposal.window} "
        f"{proposal.seats} 席，第 {proposal.start}–{proposal.end} 日。"
    )
    states = evaluate_windows(
        contracts, carrier_events, list(sources.scenarios),
        store.proposals(), pauses, proposal.start,
    )
    state = states.get((proposal.hub, proposal.window))
    if state:
        print(f"接受后第 {proposal.start} 日可售余量：{state.sellable} 席。")
    return 0


def cmd_correct(args) -> int:
    store = Store(args.state)
    sources, contracts, carrier_events, proposals, pauses = _load_capacity_inputs(
        args, store
    )
    if args.kind == "contract":
        correction = Contract(
            contract=args.contract_id,
            pool=args.pool,
            hub=args.hub,
            window=args.window,
            seats=args.seats,
            priority=args.priority,
            available_day=args.available_day,
        )
        day_from = args.available_day
        contracts_after = contracts + [correction]
        carrier_after = carrier_events
        store.add_contract(correction)
        description = (
            f"合同更正 {correction.contract}：{correction.hub}/{correction.window} "
            f"{correction.seats} 席，自第 {correction.available_day} 日起"
        )
    else:
        correction = CarrierEvent(
            event=args.event_id,
            order=args.order,
            pool=args.pool,
            hub=args.hub,
            seats=args.seats,
            state=args.state_value,
            day=args.event_day,
        )
        day_from = args.event_day
        contracts_after = contracts
        carrier_after = carrier_events + [correction]
        store.add_carrier_event(correction)
        description = (
            f"履约更正 {correction.event}：订单 {correction.order} "
            f"{correction.state}，第 {correction.day} 日"
        )

    day_to = args.to_day
    if day_to is None:
        candidates = [day_from]
        candidates += [p.end for p in proposals]
        candidates += [e.day for e in carrier_after]
        candidates += [c.available_day for c in contracts_after]
        day_to = max(candidates)
    impact = impact_of_change(
        contracts,
        contracts_after,
        carrier_events,
        carrier_after,
        list(sources.scenarios),
        proposals,
        pauses,
        day_from,
        day_to,
    )
    run_id = store.new_run_id("correct")
    inputs = fingerprint(args.sources, store)
    lines = [f"# 更正影响核对（{run_id}）", "", f"- {description}", f"- 输入指纹：`{inputs}`", ""]
    lines.append("## 受影响窗口")
    lines.append("")
    if impact.summary:
        lines.extend(f"- {s}" for s in impact.summary)
    else:
        lines.append("- 无")
    lines.append("")
    lines.append("## 无法保障的既有出票/方案")
    lines.append("")
    if impact.at_risk:
        for t in impact.at_risk:
            lines.append(
                f"- 第 {t.day} 日 {t.ticket}（{t.kind}，{t.hub}，{t.seats} 席）：{t.reason}"
            )
    else:
        lines.append("- 无（既有承诺均可保障）")
    lines.append("")
    report = "\n".join(lines)
    record = {
        "kind": "correct",
        "correction": asdict(correction),
        "correction_kind": args.kind,
        "inputs": inputs,
        "affected_windows": [list(w) for w in impact.affected_windows],
        "at_risk": [asdict(t) for t in impact.at_risk],
    }
    run_dir = store.record_run(run_id, record, report)
    print(report)
    print(f"运行档案：{run_dir}")
    return 0


def cmd_pause(args) -> int:
    store = Store(args.state)
    pause = Pause(
        hub=args.hub,
        window=args.window,
        from_day=args.from_day,
        until=args.until,
        reason=args.reason,
    )
    store.add_pause(pause)
    until = f"至第 {pause.until} 日" if pause.until is not None else "直至恢复"
    print(f"已登记暂停：{pause.hub}/{pause.window} 自第 {pause.from_day} 日起{until}（{pause.reason}）")
    return 0


def cmd_audit(args) -> int:
    store = Store(args.state)
    sources, contracts, carrier_events, proposals, pauses = _load_capacity_inputs(
        args, store
    )
    inputs = fingerprint(args.sources, store)
    day = args.day
    if day is None:
        day = max((c.available_day for c in contracts), default=0)
    states = evaluate_windows(
        contracts, carrier_events, list(sources.scenarios), proposals, pauses, day
    )
    print(f"输入指纹：{inputs}")
    print(f"评估日：第 {day} 日")
    print("")
    print("已生效承诺（台账）：")
    if proposals:
        for p in proposals:
            print(
                f"- {p.proposal}：{p.hub}/{p.window} {p.seats} 席，"
                f"第 {p.start}–{p.end} 日（第 {p.day} 日提交）{p.note}"
            )
    else:
        print("- 无已接受方案")
    if pauses:
        for p in pauses:
            print(f"- 暂停 {p.hub}/{p.window} 自第 {p.from_day} 日起：{p.reason}")
    print("")
    print(f"第 {day} 日当前可售范围：")
    for (hub, window), s in sorted(states.items()):
        blocked = "；".join(s.blocked) if s.blocked else "—"
        print(f"- {hub}/{window}：可售 {s.sellable} 席（保留 {s.retained}，方案 {s.committed}；{blocked}）")
    print("")
    print("历史运行（各有独立口径与输入指纹）：")
    runs = store.list_runs()
    if not runs:
        print("- 无")
    for run in runs:
        caliber = run.get("caliber") or run.get("correction_kind") or "—"
        print(
            f"- {run.get('recorded_at', '?')} [{run.get('kind', '?')}] "
            f"输入 {run.get('inputs', '?')} 口径 {caliber}"
        )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pilot",
        description="接驳保护试点分析与可兑现决策（合成资料）",
    )
    parser.add_argument("--sources", default="sources", help="来源资料目录")
    parser.add_argument("--state", default="var", help="状态/台账目录")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("analyze", help="按原始邀约时间复核试验比较（ITT）")
    p.add_argument("--observation-end", type=int, default=None, help="观察期截止日（默认取行程事件最大日）")
    p.add_argument("--alpha", type=float, default=0.05)
    p.set_defaults(func=cmd_analyze)

    p = sub.add_parser("windows", help="查看各窗口可出售范围与阻塞原因")
    p.add_argument("--day", type=int, default=None, help="评估日（默认取合同最晚生效日）")
    p.add_argument("--alpha", type=float, default=0.05)
    p.set_defaults(func=cmd_windows)

    p = sub.add_parser("submit", help="提交下一期窗口售票方案")
    p.add_argument("--hub", required=True)
    p.add_argument("--window", required=True)
    p.add_argument("--seats", type=int, required=True)
    p.add_argument("--start", type=int, required=True)
    p.add_argument("--end", type=int, required=True)
    p.add_argument("--day", type=int, default=None, help="提交日（默认取 start）")
    p.add_argument("--proposal-id", default=None)
    p.add_argument("--note", default="")
    p.set_defaults(func=cmd_submit)

    p = sub.add_parser("correct", help="登记履约更正或承诺缩减并核对影响")
    kind = p.add_subparsers(dest="kind", required=True)
    c = kind.add_parser("contract", help="合同更正（含承运人缩减承诺）")
    c.add_argument("--contract-id", required=True)
    c.add_argument("--pool", required=True)
    c.add_argument("--hub", required=True)
    c.add_argument("--window", required=True)
    c.add_argument("--seats", type=int, required=True)
    c.add_argument("--priority", default="confirmed_first")
    c.add_argument("--available-day", type=int, required=True)
    c.add_argument("--to-day", type=int, default=None)
    e = kind.add_parser("carrier", help="承运事件更正（履约更正）")
    e.add_argument("--event-id", required=True)
    e.add_argument("--order", required=True)
    e.add_argument("--pool", required=True)
    e.add_argument("--hub", required=True)
    e.add_argument("--seats", type=int, required=True)
    e.add_argument("--state-value", required=True, choices=["offered", "confirmed", "carrier_accepted", "released"])
    e.add_argument("--event-day", type=int, required=True)
    e.add_argument("--to-day", type=int, default=None)
    p.set_defaults(func=cmd_correct)

    p = sub.add_parser("pause", help="登记运营侧暂停")
    p.add_argument("--hub", required=True)
    p.add_argument("--window", required=True)
    p.add_argument("--from-day", type=int, required=True)
    p.add_argument("--until", type=int, default=None)
    p.add_argument("--reason", required=True)
    p.set_defaults(func=cmd_pause)

    p = sub.add_parser("audit", help="查看承诺、当前可售与历史运行依据")
    p.add_argument("--day", type=int, default=None)
    p.set_defaults(func=cmd_audit)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
