from bisect import bisect_left
from collections import defaultdict
from dataclasses import dataclass, field

from core import DAY, norm


@dataclass
class Comparison:
    sessions: int = 0
    incomplete: int = 0
    hours_by_day: dict = field(default_factory=lambda: defaultdict(float))
    hours_by_server: dict = field(default_factory=lambda: defaultdict(float))
    chat_by_day: dict = field(default_factory=lambda: defaultdict(int))
    reactions_by_day: dict = field(default_factory=lambda: defaultdict(int))
    overlap: int = 0


def compare(db, uid, c, end):
    """Pair sightings conservatively and clip durations to the requested window."""
    start = end - c["window_days"] * DAY
    limit = c["max_session_hours"] * 3600
    rows = db.execute(
        "SELECT kind,detail,at FROM event WHERE uid=? AND at BETWEEN ? AND ? "
        "AND kind IN ('join','leave','chat','reaction') ORDER BY at,source",
        (uid, start - limit, end),
    )
    return compare_events(rows, start, end, limit)


def compare_events(rows, start, end, limit):
    result = Comparison()
    current = None
    intervals = []
    chat = []
    for row in rows:
        at = row["at"]
        if at < start - limit or at > end:
            continue
        if row["kind"] == "reaction":
            if at >= start:
                result.reactions_by_day[at // DAY] += 1
        elif row["kind"] == "chat":
            if at >= start:
                chat.append(at)
                result.chat_by_day[at // DAY] += 1
        elif row["kind"] == "join":
            if current and at >= start:
                result.incomplete += 1
            current = row
        elif row["kind"] != "leave":
            continue
        elif current and norm(current["detail"]) == norm(row["detail"]):
            duration = at - current["at"]
            if at > start and 0 < duration <= limit:
                intervals.append((max(start, current["at"]), at, current["detail"]))
            elif at >= start:
                result.incomplete += 1
            current = None
        elif at >= start:
            result.incomplete += 1
    if current and current["at"] >= start:
        result.incomplete += 1
    for left, right, server in intervals:
        result.hours_by_server[server] += (right - left) / 3600
        result.sessions += 1
        result.overlap += bisect_left(chat, right) - bisect_left(chat, left)
        while left < right:
            day = left // DAY
            boundary = min(right, (day + 1) * DAY)
            result.hours_by_day[day] += (boundary - left) / 3600
            left = boundary
    return result
