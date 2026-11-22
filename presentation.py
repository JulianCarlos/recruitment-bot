"""Formatting shared by the staff board and private review commands."""

from core import KEYS, category


def card_text(mention, row, c, evaluation):
    vals, points, status, switches, flags = evaluation
    score = sum(points.values())
    blocks = int(score + 0.5)
    lines = [
        f"**Recruit evaluation · {mention}**",
        f"`{'█' * blocks}{'░' * (10 - blocks)}` **{score:.1f}/10**",
        f"Observed since <t:{row['since']}:d> · {c['window_days']}-day activity window",
        status if row["player"] else "ARK identity awaiting staff link",
    ]
    labels = (
        "Recruit days",
        "Assigned-server days",
        "Discord days",
        "Verified contributions",
    )
    for key, label in zip(KEYS, labels):
        lines.append(
            f"{label} · {int(vals[key])}/{c['targets'][key]} → {points[key]:.1f} pts"
        )
    if switches:
        a, b = switches[-1]
        lines.append(f"Observed switches · {len(switches)} · latest {a} → {b}")
    if flags:
        f = flags[-1]
        lines.append(
            f"Review · {len(flags)} off-assignment joins · latest {f['detail']} "
            f"({category(c, f['detail'])}) <t:{f['at']}:R>"
        )
    return "\n".join(lines)


def comparison_text(data, v, c):
    voice_line = (
        f"Voice · {v['total'] / 3600:.1f} h total · {v['solo'] / 3600:.1f} h solo · "
        f"{v['muted'] / 3600:.1f} h muted · {v['deafened'] / 3600:.1f} h deafened"
        if c["voice_channels"]
        else "Voice · no channels selected"
    )
    hours = sum(data.hours_by_day.values())
    chats = sum(data.chat_by_day.values())
    reactions = sum(data.reactions_by_day.values())
    return (
        f"ARK · {hours:.1f} h across {data.sessions} paired sessions\n"
        f"Discord · {chats} credited messages across {len(data.chat_by_day)} UTC days\n"
        f"Reactions to other members · {reactions} credited\n"
        f"{voice_line}\n"
        f"During paired sessions · {data.overlap} credited messages\n"
        f"Unpaired or excluded observations · {data.incomplete}"
    )
