import tempfile
import unittest
from pathlib import Path

from pilot.capacity import Proposal
from pilot.store import Store, fingerprint
from pilot.sources import Contract

ROOT = Path(__file__).resolve().parents[1]


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name) / "var")

    def tearDown(self):
        self.tmp.cleanup()

    def test_ledger_roundtrip(self):
        proposal = Proposal("PR1", "west", "W7", "P7", 2, 50, 56, 50, "试售")
        self.store.add_proposal(proposal)
        contract = Contract("C9", "P7", "west", "W7", 12, "confirmed_first", 50)
        self.store.add_contract(contract)
        self.assertEqual(self.store.proposals(), [proposal])
        self.assertEqual(self.store.extra_contracts(), [contract])
        self.assertEqual(self.store.pauses(), [])

    def test_run_record_persists_caliber(self):
        record = {"kind": "analyze", "caliber": {"observation_end": 44}, "inputs": "abc"}
        run_dir = self.store.record_run("20260101T000000Z-analyze", record, "# 报告")
        self.assertTrue((run_dir / "result.json").exists())
        self.assertTrue((run_dir / "report.md").exists())
        runs = self.store.list_runs()
        self.assertEqual(len(runs), 1)
        self.assertEqual(runs[0]["caliber"]["observation_end"], 44)

    def test_fingerprint_tracks_ledger_changes(self):
        before = fingerprint(ROOT / "sources", self.store)
        self.store.add_proposal(Proposal("PR1", "west", "W7", "P7", 2, 50, 56, 50))
        after = fingerprint(ROOT / "sources", self.store)
        self.assertNotEqual(before, after)


if __name__ == "__main__":
    unittest.main()
