import csv
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1] / "sources"
HEADERS = {'randomization': ['block', 'hub', 'week', 'group', 'pool'], 'invitations': ['invite', 'block', 'party', 'invited', 'offered', 'booked'], 'journey_events': ['event', 'invite', 'day', 'leg', 'state', 'adults', 'replaces'], 'ordinary_passenger': ['observation', 'hub', 'week', 'pool', 'guests', 'missed', 'complete'], 'rescue_contracts': ['contract', 'pool', 'hub', 'window', 'seats', 'priority', 'available_day'], 'disruption_scenarios': ['scenario', 'window', 'hub', 'simultaneous_group', 'missed_parties', 'seats'], 'carrier_events': ['event', 'order', 'pool', 'hub', 'seats', 'state', 'day']}
REFERENCES = [['invitations', 'block', 'randomization', 'block'], ['journey_events', 'invite', 'invitations', 'invite'], ['journey_events', 'replaces', 'journey_events', 'event'], ['carrier_events', 'pool', 'rescue_contracts', 'pool']]
COUNTS = [['randomization', 'group', 'protection', 6], ['ordinary_passenger', 'complete', 'no', 1], ['journey_events', 'replaces', 'T1-2b', 1]]


class SourceContractTests(unittest.TestCase):
    def setUp(self):
        self.records = {}
        for name, header in HEADERS.items():
            with (ROOT / f"{name}.csv").open(encoding="utf-8", newline="") as stream:
                reader = csv.DictReader(stream)
                self.assertEqual(reader.fieldnames, header)
                self.records[name] = list(reader)

    def test_record_shape(self):
        for name, records in self.records.items():
            with self.subTest(source=name):
                self.assertTrue(records)
                for row in records:
                    self.assertEqual(set(row), set(HEADERS[name]))
                    self.assertNotIn(None, row.values())
                    self.assertTrue(row[HEADERS[name][0]])
                self.assertEqual(len(records), len({tuple(row.values()) for row in records}))

    def test_cross_source_references(self):
        for child, fk, parent, pk in REFERENCES:
            with self.subTest(source=child, field=fk):
                keys = {row[pk] for row in self.records[parent]}
                values = {row[fk] for row in self.records[child] if row[fk]}
                self.assertTrue(values)
                self.assertFalse(values - keys)

    def test_source_has_relevant_observations(self):
        for source, field, value, minimum in COUNTS:
            with self.subTest(source=source, field=field, value=value):
                self.assertGreaterEqual(sum(row[field] == value for row in self.records[source]), minimum)
