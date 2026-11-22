from dataclasses import dataclass


@dataclass(frozen=True)
class State:
    channel: int
    muted: bool
    solo: bool
    deafened: bool


class VoiceTracker:
    """Only observed, connected intervals are persisted. Nothing resumes from disk."""

    def __init__(self, db):
        self.db = db
        self.active = {}
        db.executescript("""
            CREATE TABLE IF NOT EXISTS voice(
                uid INTEGER REFERENCES recruit(id) ON DELETE CASCADE,
                start INTEGER, end INTEGER, channel INTEGER,
                muted INTEGER, solo INTEGER, deafened INTEGER,
                PRIMARY KEY(uid,start));
            CREATE INDEX IF NOT EXISTS voice_end ON voice(uid,end);
        """)

    def set(self, uid, state, at, checkpoint=False):
        old = self.active.get(uid)
        if old:
            start, previous = old
            if at < start or (state == previous and not checkpoint):
                return
            if at > start:
                with self.db:
                    # Extend the last checkpoint when the observed state is unchanged.
                    updated = self.db.execute(
                        "UPDATE voice SET end=? WHERE uid=? AND end=? AND channel=? "
                        "AND muted=? AND solo=? AND deafened=?",
                        (
                            at,
                            uid,
                            start,
                            previous.channel,
                            previous.muted,
                            previous.solo,
                            previous.deafened,
                        ),
                    )
                    if not updated.rowcount:
                        self.db.execute(
                            "INSERT INTO voice VALUES(?,?,?,?,?,?,?)",
                            (
                                uid,
                                start,
                                at,
                                previous.channel,
                                previous.muted,
                                previous.solo,
                                previous.deafened,
                            ),
                        )
        if state is None:
            self.active.pop(uid, None)
        else:
            self.active[uid] = (at, state)

    def summary(self, uid, start, end):
        result = {"total": 0, "solo": 0, "muted": 0, "deafened": 0}

        def add(a, b, solo, muted, deafened):
            duration = max(0, min(end, b) - max(start, a))
            result["total"] += duration
            for key, flag in (("solo", solo), ("muted", muted), ("deafened", deafened)):
                if flag:
                    result[key] += duration

        rows = self.db.execute(
            "SELECT * FROM voice WHERE uid=? AND start<? AND end>?", (uid, end, start)
        )
        for r in rows:
            add(r["start"], r["end"], r["solo"], r["muted"], r["deafened"])
        if uid in self.active:
            a, s = self.active[uid]
            add(a, end, s.solo, s.muted, s.deafened)
        return result
