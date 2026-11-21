"""持久化：承诺台账、更正记录与运行档案。

所有状态都是追加式 JSONL 台账 + 每次运行的独立目录，保证
历史分析口径、已生效承诺与当前可售范围各有可回查的依据：

- ``ledger/proposals.jsonl``        已接受的运营售票方案（承诺）
- ``ledger/pauses.jsonl``           运营侧暂停
- ``ledger/contracts_extra.jsonl``  合同更正（含承运人缩减承诺）
- ``ledger/carrier_extra.jsonl``    承运事件更正（履约更正）
- ``runs/<run_id>/``                每次分析/评估的输入指纹、口径与结果
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from .capacity import Pause, Proposal
from .sources import CarrierEvent, Contract

LEDGER_PROPOSALS = "proposals.jsonl"
LEDGER_PAUSES = "pauses.jsonl"
LEDGER_CONTRACTS = "contracts_extra.jsonl"
LEDGER_CARRIER = "carrier_extra.jsonl"


class Store:
    def __init__(self, root: str | Path):
        self.root = Path(root)
        self.ledger_dir = self.root / "ledger"
        self.runs_dir = self.root / "runs"

    # ------------------------------------------------------------ 台账读写

    def _ledger_path(self, name: str) -> Path:
        return self.ledger_dir / name

    def _read_jsonl(self, name: str) -> list[dict]:
        path = self._ledger_path(name)
        if not path.exists():
            return []
        with path.open(encoding="utf-8") as stream:
            return [json.loads(line) for line in stream if line.strip()]

    def _append_jsonl(self, name: str, record: dict) -> None:
        self.ledger_dir.mkdir(parents=True, exist_ok=True)
        with self._ledger_path(name).open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")

    def proposals(self) -> list[Proposal]:
        return [Proposal(**r) for r in self._read_jsonl(LEDGER_PROPOSALS)]

    def pauses(self) -> list[Pause]:
        return [Pause(**r) for r in self._read_jsonl(LEDGER_PAUSES)]

    def extra_contracts(self) -> list[Contract]:
        return [Contract(**r) for r in self._read_jsonl(LEDGER_CONTRACTS)]

    def extra_carrier_events(self) -> list[CarrierEvent]:
        return [CarrierEvent(**r) for r in self._read_jsonl(LEDGER_CARRIER)]

    def add_proposal(self, proposal: Proposal) -> None:
        self._append_jsonl(LEDGER_PROPOSALS, asdict(proposal))

    def add_pause(self, pause: Pause) -> None:
        self._append_jsonl(LEDGER_PAUSES, asdict(pause))

    def add_contract(self, contract: Contract) -> None:
        self._append_jsonl(LEDGER_CONTRACTS, asdict(contract))

    def add_carrier_event(self, event: CarrierEvent) -> None:
        self._append_jsonl(LEDGER_CARRIER, asdict(event))

    # ------------------------------------------------------------ 运行档案

    def new_run_id(self, kind: str) -> str:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        return f"{stamp}-{kind}"

    def record_run(self, run_id: str, record: dict, report: str | None = None) -> Path:
        """把一次运行的口径、输入指纹与结果写入 runs/<run_id>/。"""
        run_dir = self.runs_dir / run_id
        run_dir.mkdir(parents=True, exist_ok=False)
        payload = dict(record)
        payload["recorded_at"] = datetime.now(timezone.utc).isoformat()
        (run_dir / "result.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        if report is not None:
            (run_dir / "report.md").write_text(report, encoding="utf-8")
        return run_dir

    def list_runs(self) -> list[dict]:
        if not self.runs_dir.exists():
            return []
        runs = []
        for run_dir in sorted(self.runs_dir.iterdir()):
            result = run_dir / "result.json"
            if result.exists():
                runs.append(json.loads(result.read_text(encoding="utf-8")))
        return runs


def fingerprint(sources_dir: str | Path, store: Store) -> str:
    """输入指纹：来源表与台账内容的联合散列，用于回查依据。"""
    digest = hashlib.sha256()
    sources_path = Path(sources_dir)
    for path in sorted(sources_path.glob("*.csv")):
        digest.update(path.name.encode())
        digest.update(path.read_bytes())
    for name in (LEDGER_PROPOSALS, LEDGER_PAUSES, LEDGER_CONTRACTS, LEDGER_CARRIER):
        ledger = store._ledger_path(name)
        digest.update(name.encode())
        if ledger.exists():
            digest.update(ledger.read_bytes())
    return digest.hexdigest()[:16]
