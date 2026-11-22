import unittest

from activity import compare
from core import DAY, connect, record


class ComparisonTests(unittest.TestCase):
    def setUp(self):
        self.db = connect(":memory:")
        self.addCleanup(self.db.close)
        self.db.execute("INSERT INTO recruit VALUES(1,0,'test')")
        self.cfg = {"max_session_hours": 12, "window_days": 1}

    def events(self, *rows):
        for i, (kind, at, detail) in enumerate(rows):
            record(self.db, 1, str(i), kind, at, detail)

    def test_clipping_midnight_and_half_open_overlap(self):
        self.events(
            ("join", DAY - 3600, "Home"),
            ("leave", DAY + 3600, "Home"),
            ("chat", DAY - 1800, ""),
            ("chat", DAY + 3600, ""),
        )
        data = compare(self.db, 1, self.cfg, 2 * DAY - 1800)
        self.assertEqual(data.hours_by_day, {0: 0.5, 1: 1.0})
        self.assertEqual(
            (data.sessions, data.overlap, sum(data.chat_by_day.values())), (1, 1, 2)
        )

    def test_switch_unpaired_and_excess_duration(self):
        self.events(
            ("join", 10, "A"),
            ("join", 20, "B"),
            ("leave", 30, "A"),
            ("leave", 3620, "B"),
            ("join", 4000, "B"),
            ("leave", 55000, "B"),
            ("join", 56000, "C"),
        )
        result = compare(self.db, 1, self.cfg, 60000)
        self.assertEqual(result.sessions, 1)
        self.assertEqual(result.hours_by_day, {0: 1})
        self.assertEqual(result.incomplete, 4)
