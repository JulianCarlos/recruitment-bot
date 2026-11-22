import unittest
from pathlib import Path

from core import DAY, category, config, connect, parse, record, report


class EvaluationTests(unittest.TestCase):
    def setUp(self):
        self.c = config(Path(__file__).with_name("config.example.toml"))
        self.db = connect(":memory:")
        self.addCleanup(self.db.close)
        self.db.execute("INSERT INTO recruit VALUES(1,0,'eos-a')")
        self.member = self.db.execute("SELECT * FROM recruit").fetchone()

    def test_parser_and_server_mapping(self):
        hits = parse(self.c, "Player: EOS-A joined The Island 101", 5)
        self.assertEqual(hits, {("join", "eos-a", "The Island 101")})
        self.assertFalse(parse(self.c, "Just a conversation", 5))
        self.c["patterns"]["leave"] = r"Left: (?P<player>\w+)"
        self.c["channel_servers"]["5"] = "Home"
        self.assertEqual(parse(self.c, "Left: Bob", 5), {("leave", "bob", "Home")})

    def test_role_scope_idempotence_and_deletion(self):
        self.assertFalse(record(self.db, 2, "m", "join", 1))
        self.assertFalse(record(self.db, 1, "m", "join", -1))
        self.assertTrue(record(self.db, 1, "m", "join", 1))
        self.assertFalse(record(self.db, 1, "m", "join", 1))
        self.db.execute("DELETE FROM recruit")
        self.assertEqual(self.db.execute("SELECT count(*) FROM event").fetchone()[0], 0)

    def test_score_window_and_cooldown(self):
        at = 30 * DAY
        for j in range(6):
            record(self.db, 1, f"g{j}", "join", at - j * DAY, "The Island 101")
            record(self.db, 1, f"c{j}", "chat", at - j * DAY, cooldown=600)
            self.assertFalse(
                record(self.db, 1, f"spam{j}", "chat", at - j * DAY + 1, cooldown=600)
            )
        for j in range(4):
            record(self.db, 1, f"t{j}", "credit", at)
        record(self.db, 1, "ancient", "credit", 0)
        vals, points, *_ = report(self.db, self.member, self.c, at)
        self.assertEqual(vals["contributions"], 4)
        self.assertEqual(sum(points.values()), 10)

    def test_switch_and_stale_sighting(self):
        record(self.db, 1, "1", "join", 1, "The Island 101")
        record(self.db, 1, "2", "join", 2, "Elsewhere")
        record(self.db, 1, "3", "leave", 3, "The Island 101")
        vals, _, status, switches, flags = report(self.db, self.member, self.c, 4)
        self.assertEqual(switches, [("The Island 101", "Elsewhere")])
        self.assertEqual(len(flags), 1)
        self.assertEqual(vals["game_days"], 1)
        self.assertIn("online · Elsewhere", status)
        self.assertNotIn("online", report(self.db, self.member, self.c, DAY)[2])
        self.assertEqual(
            category(self.c, "Scorched Earth 202"), "owned, outside assignment"
        )


if __name__ == "__main__":
    unittest.main()
