import unittest
from dataclasses import replace

from activity import compare
from core import config, connect, parse, record, report
from voice import State, VoiceTracker


class TrackingTests(unittest.TestCase):
    def setUp(self):
        self.db = connect(":memory:")
        self.addCleanup(self.db.close)
        self.db.execute("INSERT INTO recruit VALUES(1,0,'test')")
        self.voice = VoiceTracker(self.db)

    def test_solo_muted_can_overlap_and_offline_gap_is_excluded(self):
        solo = State(10, True, True, False)
        group = replace(solo, solo=False)
        self.voice.set(1, solo, 10)
        self.voice.set(1, solo, 20, checkpoint=True)
        self.voice.set(1, group, 30)
        self.voice.set(1, None, 50)
        self.voice.set(1, replace(group, muted=False, deafened=True), 70)
        self.assertEqual(
            self.voice.summary(1, 15, 80),
            {"total": 45, "solo": 15, "muted": 35, "deafened": 10},
        )

    def test_crash_discards_uncheckpointed_time_and_deletion_cascades(self):
        state = State(10, False, True, False)
        self.voice.set(1, state, 10)
        self.voice.set(1, state, 20, checkpoint=True)
        restarted = VoiceTracker(self.db)
        self.assertEqual(restarted.summary(1, 0, 200)["total"], 10)
        self.db.execute("DELETE FROM recruit WHERE id=1")
        self.assertEqual(restarted.summary(1, 0, 200)["total"], 0)

    def test_two_feeds_with_channel_mapped_server_and_travel(self):
        c = config("config.example.toml")
        c["patterns"] = {
            "join": r"Join (?P<player>\w+)",
            "leave": r"Leave (?P<player>\w+)",
        }
        c["channel_servers"] = {"11": "The Island 101", "22": "Elsewhere"}
        for at, channel, body in [
            (1, 11, "Join Test"),
            (3601, 11, "Leave Test"),
            (3700, 22, "Join Test"),
            (5500, 22, "Leave Test"),
        ]:
            for kind, player, server in parse(c, body, channel):
                self.assertEqual(player, "test")
                record(self.db, 1, str(at), kind, at, server)
        data = compare(self.db, 1, c, 6000)
        self.assertEqual(
            data.hours_by_server, {"The Island 101": 1.0, "Elsewhere": 0.5}
        )
        row = self.db.execute("SELECT * FROM recruit").fetchone()
        _, _, _, switches, flags = report(self.db, row, c, 6000)
        self.assertEqual(switches, [("The Island 101", "Elsewhere")])
        self.assertEqual(len(flags), 1)
