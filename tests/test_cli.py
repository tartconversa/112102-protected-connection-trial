import json
import tempfile
import unittest
from pathlib import Path

from pilot.cli import main

ROOT = Path(__file__).resolve().parents[1]


class CliEndToEndTests(unittest.TestCase):
    """CLI 全流程：分析 → 扩容更正 → 提交 → 重复占用被拒 → 审计。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.state = str(Path(self.tmp.name) / "var")
        self.base = ["--sources", str(ROOT / "sources"), "--state", self.state]

    def tearDown(self):
        self.tmp.cleanup()

    def run_cli(self, *argv):
        return main(list(self.base) + list(argv))

    def test_full_workflow(self):
        self.assertEqual(self.run_cli("analyze"), 0)
        self.assertEqual(self.run_cli("windows", "--day", "47"), 0)

        # 直接提交：被并发情景阻塞。
        self.assertEqual(
            self.run_cli(
                "submit", "--hub", "west", "--window", "W7",
                "--seats", "1", "--start", "50", "--end", "56",
                "--proposal-id", "PR0",
            ),
            1,
        )
        # 扩容更正后提交成功。
        self.assertEqual(
            self.run_cli(
                "correct", "contract", "--contract-id", "C9", "--pool", "P7",
                "--hub", "west", "--window", "W7", "--seats", "12",
                "--available-day", "50",
            ),
            0,
        )
        self.assertEqual(
            self.run_cli(
                "submit", "--hub", "west", "--window", "W7",
                "--seats", "2", "--start", "50", "--end", "56",
                "--proposal-id", "PR1",
            ),
            0,
        )
        # 同一空余量不能再生效给下一个方案。
        self.assertEqual(
            self.run_cli(
                "submit", "--hub", "west", "--window", "W7",
                "--seats", "1", "--start", "50", "--end", "56",
                "--proposal-id", "PR2",
            ),
            1,
        )
        self.assertEqual(self.run_cli("audit", "--day", "50"), 0)

        # 台账与运行档案都可回查。
        ledger = Path(self.state) / "ledger" / "proposals.jsonl"
        self.assertTrue(ledger.exists())
        entries = [json.loads(line) for line in ledger.read_text().splitlines()]
        self.assertEqual([e["proposal"] for e in entries], ["PR1"])
        runs = list((Path(self.state) / "runs").iterdir())
        kinds = {json.loads((r / "result.json").read_text())["kind"] for r in runs}
        self.assertEqual(kinds, {"analyze", "windows", "correct"})

    def test_pause_then_windows(self):
        self.assertEqual(
            self.run_cli(
                "pause", "--hub", "west", "--window", "W7",
                "--from-day", "50", "--reason", "检修",
            ),
            0,
        )
        self.assertEqual(self.run_cli("windows", "--day", "50"), 0)


if __name__ == "__main__":
    unittest.main()
