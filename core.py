import itertools
import math
import re
import sqlite3
import tomllib

DAY = 86400
KEYS = ("tenure", "game_days", "chat_days", "contributions")


def norm(value):
    return " ".join(value.split()).casefold()


def config(path):
    with open(path, "rb") as stream:
        return validate_config(tomllib.load(stream))


def validate_config(c):
    c.setdefault("track_reactions", True)
    c.setdefault("reaction_activity_days", True)
    c.setdefault("reaction_cooldown_minutes", 10)
    for option in ("track_reactions", "reaction_activity_days"):
        if type(c[option]) is not bool:
            raise ValueError(f"{option} must be true or false")
    c.setdefault("voice_channels", [])
    c.setdefault("board_title", "Tribe field report")
    c.setdefault("accent_color", "54C9A3")
    c.setdefault("max_session_hours", 12)
    c.setdefault("board_reader_roles", [])
    c.setdefault(
        "missions",
        [
            "Supply run · Restock a base resource with a tribe lead.",
            "Field scout · Survey an assigned server and report a useful finding.",
            "Base shift · Help an officer complete a maintenance round.",
        ],
    )
    if (
        not isinstance(c["missions"], list)
        or not c["missions"]
        or not all(
            isinstance(m, str) and 1 <= len(m.strip()) <= 500 for m in c["missions"]
        )
    ):
        raise ValueError("Configure at least one mission of 1–500 characters")
    if not 1 <= len(c["board_title"]) <= 80 or not re.fullmatch(
        r"[0-9a-fA-F]{6}", c["accent_color"]
    ):
        raise ValueError("Set a board title (1–80 characters) and six-digit hex color")
    for key in ("guild_id", "recruit_role", "evaluation_channel"):
        assert_id(c[key])
    for key in (
        "feed_channels",
        "chat_channels",
        "bot_ids",
        "webhook_ids",
        "board_reader_roles",
        "voice_channels",
    ):
        for value in c[key]:
            assert_id(value)
    if not c["feed_channels"] or not (c["bot_ids"] or c["webhook_ids"]):
        raise ValueError("Configure a feed channel and its bot or webhook ID")
    if (
        set(c["chat_channels"]) & set(c["feed_channels"])
        or c["evaluation_channel"] in c["chat_channels"] + c["feed_channels"]
    ):
        raise ValueError("Use separate activity, feed and evaluation channels")
    for key in (
        "max_session_hours",
        "window_days",
        "retention_days",
        "refresh_minutes",
        "status_hours",
        "chat_cooldown_minutes",
        "reaction_cooldown_minutes",
    ):
        if type(c[key]) is not int or c[key] <= 0:
            raise ValueError(f"{key} must be a positive integer")
    if c["retention_days"] < c["window_days"]:
        raise ValueError("retention_days must cover window_days")
    if not set(map(norm, c["recruit_servers"])) <= set(map(norm, c["owned_servers"])):
        raise ValueError("Recruit servers must be listed in owned_servers")
    for k in KEYS:
        if not (0 <= c["weights"][k] <= 10 and 0 < c["targets"][k] < math.inf):
            raise ValueError(f"Invalid weight or target for {k}")
    if not math.isclose(sum(c["weights"][k] for k in KEYS), 10):
        raise ValueError("Weights must total 10")
    for kind in ("join", "leave"):
        p = re.compile(c["patterns"][kind])
        if "player" not in p.groupindex:
            raise ValueError(f"{kind} needs a named player group")
        if "server" not in p.groupindex and any(
            str(ch) not in c["channel_servers"] for ch in c["feed_channels"]
        ):
            raise ValueError(f"{kind} needs a server group or mapped feed channels")
    return c


def assert_id(value):
    if type(value) is not int or value <= 0:
        raise ValueError("Discord IDs must be positive integers")


def parse(c, body, channel):
    hits = set()
    for kind, pattern in c["patterns"].items():
        for match in re.finditer(pattern, body):
            player = norm(match["player"] or "")
            server = (
                match.groupdict().get("server")
                or c["channel_servers"].get(str(channel), "")
            ).strip()
            if player and server:
                hits.add((kind, player, server))
    return hits


def category(c, server):
    if norm(server) in map(norm, c["recruit_servers"]):
        return "assigned"
    if norm(server) in map(norm, c["owned_servers"]):
        return "owned, outside assignment"
    return "outside configured ownership"


def connect(path):
    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    db.executescript("""
        PRAGMA foreign_keys=ON;
        CREATE TABLE IF NOT EXISTS recruit(id INTEGER PRIMARY KEY, since INTEGER NOT NULL,
            player TEXT UNIQUE);
        CREATE TABLE IF NOT EXISTS event(uid INTEGER REFERENCES recruit(id) ON DELETE CASCADE,
            source TEXT, kind TEXT, at INTEGER, detail TEXT, PRIMARY KEY(uid,source));
        CREATE INDEX IF NOT EXISTS event_time ON event(uid,at);
        CREATE INDEX IF NOT EXISTS event_kind_time ON event(uid,kind,at);
        CREATE TABLE IF NOT EXISTS card(uid INTEGER PRIMARY KEY, channel INTEGER, msg INTEGER);
    """)
    return db


def record(db, uid, source, kind, at, detail="", cooldown=0):
    recruit = db.execute("SELECT since FROM recruit WHERE id=?", (uid,)).fetchone()
    if not recruit or at < recruit["since"]:
        return False
    if (
        cooldown
        and db.execute(
            "SELECT 1 FROM event WHERE uid=? AND kind=? AND at>? AND at<? LIMIT 1",
            (uid, kind, at - cooldown, at + cooldown),
        ).fetchone()
    ):
        return False
    with db:
        return bool(
            db.execute(
                "INSERT OR IGNORE INTO event VALUES(?,?,?,?,?)",
                (uid, source, kind, at, detail),
            ).rowcount
        )


def evaluation_events(db, uid, c, end):
    # Game sightings establish the last known status and previous server. Other
    # activity has no bearing on the evaluation before the rolling window.
    return db.execute(
        "SELECT kind,detail,at,source FROM event WHERE uid=? AND at<=? AND "
        "(at>=? OR kind IN ('join','leave')) ORDER BY at,source",
        (uid, end, end - c["window_days"] * DAY),
    ).fetchall()


def report(db, member, c, now):
    return score_events(evaluation_events(db, member["id"], c, now), member, c, now)


def score_events(events, member, c, now):
    recent = [e for e in events if e["at"] >= now - c["window_days"] * DAY]
    game = [e for e in events if e["kind"] in ("join", "leave")]
    last = game[-1] if game else None
    status = "Unknown"
    if last:
        status = f"Last {last['kind']} · {last['detail']} · <t:{last['at']}:R>"
        if now - last["at"] <= c["status_hours"] * 3600:
            # A late departure from the previous server cannot close a newer join.
            current = None
            for e in game:
                if e["kind"] == "join":
                    current = e
                elif current and norm(current["detail"]) == norm(e["detail"]):
                    current = None
            if current and now - current["at"] <= c["status_hours"] * 3600:
                status += f"\nLast seen online · {current['detail']}"
            elif not current:
                status += "\nNo open observed session"
    discord_kinds = {"chat", "reaction"} if c["reaction_activity_days"] else {"chat"}
    vals = {
        "tenure": max(0, (now - member["since"]) / DAY),
        "game_days": len(
            {
                e["at"] // DAY
                for e in recent
                if e["kind"] in ("join", "leave")
                and category(c, e["detail"]) == "assigned"
            }
        ),
        "chat_days": len(
            {e["at"] // DAY for e in recent if e["kind"] in discord_kinds}
        ),
        "contributions": sum(e["kind"] == "credit" for e in recent),
    }
    points = {k: c["weights"][k] * min(1, vals[k] / c["targets"][k]) for k in KEYS}
    joins = [e for e in game if e["kind"] == "join"]
    switches = [
        (a["detail"], b["detail"])
        for a, b in itertools.pairwise(joins)
        if b["at"] >= now - c["window_days"] * DAY
        and norm(a["detail"]) != norm(b["detail"])
    ]
    flags = [
        e
        for e in recent
        if e["kind"] == "join" and category(c, e["detail"]) != "assigned"
    ]
    return vals, points, status, switches, flags
