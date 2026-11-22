import asyncio
import logging
import os
import random
import sqlite3
import time
from datetime import UTC, datetime
from pathlib import Path

import discord
from discord import app_commands
from discord.ext import tasks

from activity import compare, compare_events
from core import (
    DAY,
    connect,
    evaluation_events,
    norm,
    parse,
    record,
    report,
    score_events,
)
from presentation import card_text, comparison_text
from settings import Settings
from setup_ui import Panel
from voice import State, VoiceTracker

path = Path(os.environ.get("RECRUIT_DB", "data/recruits.sqlite3"))
path.parent.mkdir(parents=True, exist_ok=True)
db = connect(path)
voice = VoiceTracker(db)
voice_connected = False
settings = Settings(db)
c = settings.active
scope = discord.Object(id=c["guild_id"])
synced_guilds = set()

intents = discord.Intents.none()
intents.guild_reactions = True
intents.voice_states = True
intents.guilds = intents.members = intents.guild_messages = intents.message_content = (
    True
)


class Client(discord.Client):
    async def setup_hook(self):
        refresh.change_interval(minutes=c["refresh_minutes"])
        refresh.start()
        voice_checkpoint.start()


bot = Client(intents=intents, allowed_mentions=discord.AllowedMentions.none())
tree = app_commands.CommandTree(bot)
log = logging.getLogger("recruit")
health = {"last": None, "parsed": 0, "skipped": 0, "linked": 0}


rendered = {}
feeds = {
    channel: {"matched": 0, "unmatched": 0, "last_match": None}
    for channel in c["feed_channels"]
}


def board_issue(channel, candidate=None):
    cfg = c if candidate is None else candidate
    if not isinstance(channel, discord.TextChannel):
        return "Evaluation channel unavailable"
    guild = channel.guild
    if not guild.chunked or guild.unavailable:
        return "Waiting for the member list"
    role = guild.get_role(cfg["recruit_role"])
    if role is None or role == guild.default_role:
        return "Configure a dedicated recruit role"
    if (
        channel.permissions_for(guild.default_role).view_channel
        or channel.permissions_for(role).view_channel
    ):
        return "Hide the channel from @everyone and the recruit role"
    readers = set(cfg["board_reader_roles"])
    for m in guild.members:
        if m.id == bot.user.id or not channel.permissions_for(m).view_channel:
            continue
        if (not m.bot and role in m.roles) or not (
            m.guild_permissions.manage_guild or any(r.id in readers for r in m.roles)
        ):
            return "A recruit or unapproved member can view this channel"
    permissions = channel.permissions_for(guild.me)
    if not all(
        (
            permissions.view_channel,
            permissions.send_messages,
            permissions.embed_links,
            permissions.read_message_history,
        )
    ):
        return "Bot needs View Channel, Send Messages, Embed Links and Read Message History"
    return None


def now():
    return int(time.time())


def eligible(m):
    return (
        settings.revision > 0
        and isinstance(m, discord.Member)
        and m.guild.id == scope.id
        and not m.bot
        and any(r.id == c["recruit_role"] for r in m.roles)
    )


def enroll(m):
    with db:
        db.execute("INSERT OR IGNORE INTO recruit(id,since) VALUES(?,?)", (m.id, now()))


def forget(uid):
    voice.active.pop(uid, None)
    with db:
        db.execute("DELETE FROM recruit WHERE id=?", (uid,))


@bot.event
async def on_member_update(before, after):
    if eligible(after):
        enroll(after)
        if after.voice and after.voice.channel:
            sync_voice({after.voice.channel.id})
    elif eligible(before):
        forget(after.id)


@bot.event
async def on_member_join(member):
    if eligible(member):
        enroll(member)


@bot.event
async def on_raw_member_remove(payload):
    if payload.guild_id == scope.id:
        forget(payload.user.id)


def sync_voice(channel_ids, checkpoint=False):
    guild = bot.get_guild(scope.id)
    if not voice_connected or not guild or guild.unavailable or not guild.chunked:
        return
    channels = set(channel_ids).intersection(c["voice_channels"])
    seen = {}
    at = now()
    for cid in channels:
        channel = guild.get_channel(cid)
        if not isinstance(channel, (discord.VoiceChannel, discord.StageChannel)):
            continue
        humans = [m for m in channel.members if not m.bot]
        for m in humans:
            v = m.voice
            if eligible(m) and v and v.channel and v.channel.id == cid:
                seen[m.id] = State(
                    cid, v.self_mute or v.mute, len(humans) == 1, v.self_deaf or v.deaf
                )
    for uid, (_, state) in list(voice.active.items()):
        if state.channel in channels and uid not in seen:
            voice.set(uid, None, at)
    for uid, state in seen.items():
        if uid not in voice.active:
            with db:
                db.execute(
                    "INSERT OR IGNORE INTO recruit(id,since) VALUES(?,?)", (uid, at)
                )
        voice.set(uid, state, at, checkpoint)


@bot.event
async def on_voice_state_update(member, before, after):
    if member.guild.id == scope.id:
        sync_voice({v.channel.id for v in (before, after) if v.channel})


def pause_voice():
    global voice_connected
    voice_connected = False
    for uid in list(voice.active):
        voice.set(uid, None, now())


@bot.event
async def on_disconnect():
    pause_voice()


@bot.event
async def on_guild_unavailable(guild):
    if guild.id == scope.id:
        pause_voice()


def resume_voice():
    global voice_connected
    voice_connected = True
    sync_voice(c["voice_channels"])


@bot.event
async def on_guild_available(guild):
    if guild.id == scope.id and bot.is_ready():
        resume_voice()


async def register_commands(guild):
    if guild.id in synced_guilds or scope.id not in (0, guild.id):
        return
    tree.copy_global_to(guild=guild)
    await tree.sync(guild=guild)
    synced_guilds.add(guild.id)


@bot.event
async def on_guild_join(guild):
    await register_commands(guild)


@bot.event
async def on_ready():
    resume_voice()
    for guild in bot.guilds:
        await register_commands(guild)


@bot.event
async def on_resumed():
    resume_voice()


@tasks.loop(seconds=60)
async def voice_checkpoint():
    sync_voice(c["voice_channels"], checkpoint=True)


def parts(message):
    if message.content:
        yield message.content
    for embed in message.embeds:
        lines = [embed.author.name, embed.title, embed.description]
        lines.extend(f"{f.name}: {f.value}" for f in embed.fields)
        lines.append(embed.footer.text)
        yield "\n".join(x for x in lines if x)


@bot.event
async def on_message(m):
    if not m.guild or m.guild.id != scope.id:
        return
    stamp = int(m.created_at.timestamp())
    if m.channel.id in c["feed_channels"]:
        trusted = (
            m.webhook_id in c["webhook_ids"]
            if m.webhook_id
            else m.author.bot and m.author.id in c["bot_ids"]
        )
        if not trusted:
            return
        hits = set().union(*(parse(c, part, m.channel.id) for part in parts(m)))
        stats = feeds[m.channel.id]
        stats["matched" if hits else "unmatched"] += 1
        if hits:
            stats["last_match"] = stamp
        health["last"] = stamp
        health["parsed" if hits else "skipped"] += 1
        for kind, player, server in hits:
            r = db.execute(
                "SELECT id FROM recruit WHERE player=?", (player,)
            ).fetchone()
            if r and eligible(m.guild.get_member(r["id"])):
                added = record(
                    db,
                    r["id"],
                    f"{m.id}:{kind}:{norm(server)}",
                    kind,
                    stamp,
                    server[:180],
                )
                health["linked"] += int(added)
    elif m.channel.id in c["chat_channels"] and not m.webhook_id and eligible(m.author):
        enroll(m.author)
        record(
            db,
            m.author.id,
            str(m.id),
            "chat",
            stamp,
            cooldown=c["chat_cooldown_minutes"] * 60,
        )


@bot.event
async def on_raw_reaction_add(payload):
    if (
        not c["track_reactions"]
        or payload.guild_id != scope.id
        or payload.channel_id not in c["chat_channels"]
    ):
        return
    if (
        payload.message_author_id is None
        or payload.message_author_id == payload.user_id
    ):
        return
    guild = bot.get_guild(scope.id)
    if not guild:
        return
    member = guild.get_member(payload.user_id)
    author = guild.get_member(payload.message_author_id)
    if not eligible(member) or author is None or author.bot:
        return
    enroll(member)
    record(
        db,
        member.id,
        f"reaction:{payload.message_id}",
        "reaction",
        now(),
        cooldown=c["reaction_cooldown_minutes"] * 60,
    )


@bot.event
async def on_raw_message_delete(payload):
    rendered.pop(payload.message_id, None)


async def remove_card(row):
    ch = bot.get_channel(row["channel"])
    if not ch:
        try:
            ch = await bot.fetch_channel(row["channel"])
        except discord.NotFound:
            ch = None
    if ch:
        try:
            await ch.get_partial_message(row["msg"]).delete()
        except discord.NotFound:
            pass
    rendered.pop(row["msg"], None)
    with db:
        db.execute("DELETE FROM card WHERE uid=?", (row["uid"],))


@tasks.loop(minutes=5)
async def refresh():
    if not settings.revision:
        return
    revision = settings.revision
    guild = bot.get_guild(scope.id)
    if not guild or guild.unavailable or not guild.chunked:
        return
    ids = {m.id for m in guild.members if eligible(m)}
    enrolled = {row["id"] for row in db.execute("SELECT id FROM recruit")}
    stamp = now()
    cutoff = stamp - c["retention_days"] * DAY
    with db:
        db.executemany(
            "INSERT INTO recruit(id,since) VALUES(?,?)",
            ((uid, stamp) for uid in ids - enrolled),
        )
        for uid in enrolled - ids:
            forget(uid)
        db.execute("DELETE FROM voice WHERE end<?", (cutoff,))
        db.execute("DELETE FROM event WHERE at<?", (cutoff,))
    ch = guild.get_channel(c["evaluation_channel"])
    issue = board_issue(ch)
    for row in db.execute("SELECT * FROM card").fetchall():
        if issue or row["uid"] not in ids or row["channel"] != c["evaluation_channel"]:
            try:
                await remove_card(row)
            except discord.HTTPException:
                log.exception("Could not remove obsolete evaluation card")
    if issue:
        log.warning("Evaluation board paused: %s", issue)
        return
    recruits = db.execute(
        "SELECT recruit.*,card.channel,card.msg FROM recruit "
        "LEFT JOIN card ON recruit.id=card.uid ORDER BY recruit.id"
    ).fetchall()
    for index, r in enumerate(recruits):
        if index and index % 25 == 0:
            await asyncio.sleep(
                0
            )  # Let gateway events run during large board refreshes.
        if revision != settings.revision:
            return
        m = guild.get_member(r["id"])
        if not eligible(m):
            continue
        message_id = r["msg"]
        if message_id and r["channel"] != ch.id:
            continue
        end = now()
        start = end - c["window_days"] * DAY
        events = evaluation_events(db, m.id, c, end)
        evaluation = score_events(events, r, c, end)
        activity = compare_events(events, start, end, c["max_session_hours"] * 3600)
        summary = comparison_text(activity, voice.summary(m.id, start, end), c)
        embed = discord.Embed(
            title=c["board_title"],
            description=card_text(m.mention, r, c, evaluation),
            color=int(c["accent_color"], 16),
        )
        embed.add_field(name="ARK & Discord activity", value=summary, inline=False)
        embed.set_footer(
            text="Paired log times estimate connected time. Chat counts reflect selected channels."
        )
        payload = embed.to_dict()
        try:
            if message_id:
                if rendered.get(message_id) == payload:
                    continue
                try:
                    await ch.get_partial_message(message_id).edit(
                        content=None, embed=embed
                    )
                    rendered[message_id] = payload
                    continue
                except discord.NotFound:
                    rendered.pop(message_id, None)
            msg = await ch.send(embed=embed)
            rendered[msg.id] = payload
            with db:
                db.execute(
                    "INSERT OR REPLACE INTO card VALUES(?,?,?)", (m.id, ch.id, msg.id)
                )
        except discord.HTTPException:
            log.exception("Could not update recruit evaluation")


@refresh.before_loop
async def ready():
    await bot.wait_until_ready()


def staff_command(name, description):
    async def in_tribe(i):
        if i.guild_id == scope.id and (settings.revision or name == "recruit_setup"):
            return True
        if (
            not scope.id
            and name == "recruit_setup"
            and len(bot.guilds) == 1
            and i.guild_id == bot.guilds[0].id
        ):
            return True
        raise app_commands.CheckFailure(
            "Use /recruit_setup in the bot’s tribe server. For first setup, install this single-tribe bot in one server."
        )

    def register(fn):
        fn = app_commands.check(in_tribe)(fn)
        fn = app_commands.checks.has_permissions(manage_guild=True)(fn)
        fn = app_commands.default_permissions(manage_guild=True)(fn)
        return tree.command(name=name, description=description)(fn)

    return register


@staff_command(
    "recruit_link",
    "Link a current recruit to the unique player identifier printed by Overseer",
)
async def link(
    i: discord.Interaction,
    member: discord.Member,
    player: app_commands.Range[str, 1, 160],
):
    if not eligible(member) or not norm(player):
        return await i.response.send_message(
            "Choose a current recruit and a player identifier.", ephemeral=True
        )
    enroll(member)
    try:
        with db:
            old = db.execute(
                "SELECT player FROM recruit WHERE id=?", (member.id,)
            ).fetchone()[0]
            if old != norm(player):
                db.execute(
                    "DELETE FROM event WHERE uid=? AND kind IN ('join','leave')",
                    (member.id,),
                )
                db.execute(
                    "UPDATE recruit SET player=? WHERE id=?", (norm(player), member.id)
                )
    except sqlite3.IntegrityError:
        return await i.response.send_message(
            "That player is already linked to another recruit.", ephemeral=True
        )
    await i.response.send_message(
        "Player linked. New matching events will appear on the evaluation.",
        ephemeral=True,
    )


@staff_command(
    "recruit_credit",
    "Record a completed contribution with its evidence or task reference",
)
async def credit(
    i: discord.Interaction,
    member: discord.Member,
    task: app_commands.Range[str, 1, 200],
):
    if not eligible(member) or not norm(task):
        return await i.response.send_message(
            "Choose a current recruit and enter a task reference.", ephemeral=True
        )
    enroll(member)
    added = record(
        db, member.id, "task:" + norm(task), "credit", now(), f"{i.user.id} · {task}"
    )
    await i.response.send_message(
        "Contribution recorded." if added else "That task was already credited.",
        ephemeral=True,
    )


@staff_command(
    "recruit_review",
    "See the evaluation, recent ARK sightings and verified contributions",
)
async def review(i: discord.Interaction, member: discord.Member):
    if not eligible(member):
        return await i.response.send_message(
            "This member does not have the recruit role.", ephemeral=True
        )
    enroll(member)
    row = db.execute("SELECT * FROM recruit WHERE id=?", (member.id,)).fetchone()
    history = db.execute(
        "SELECT * FROM event WHERE uid=? AND kind IN ('join','leave','credit') ORDER BY at DESC LIMIT 5",
        (member.id,),
    )
    lines = [f"<t:{e['at']}:d> · {e['kind']} · {e['detail'][:90]}" for e in history]
    summary = card_text(member.mention, row, c, report(db, row, c, now()))
    history_text = "\n".join(lines)
    await i.response.send_message(
        summary,
        embed=discord.Embed(description=history_text) if lines else None,
        ephemeral=True,
    )


@staff_command(
    "recruit_feed",
    "Check receipt and parsing of allowlisted Overseer posts since startup",
)
async def feed(i: discord.Interaction):
    last = f"<t:{health['last']}:R>" if health["last"] else "none"
    await i.response.send_message(
        f"Last feed post · {last}\nMatched posts · {health['parsed']}\n"
        f"Unmatched posts · {health['skipped']}\nRecruit events saved · {health['linked']}",
        ephemeral=True,
    )


@staff_command(
    "recruit_mission",
    "Draw a configurable field assignment for an officer to give a recruit",
)
async def mission(i: discord.Interaction):
    embed = discord.Embed(
        title="Field assignment",
        description=random.choice(c["missions"]),
        color=int(c["accent_color"], 16),
    )
    embed.set_footer(
        text="After completion, record the task reference with /recruit_credit."
    )
    await i.response.send_message(embed=embed, ephemeral=True)


@staff_command(
    "recruit_compare", "Compare paired ARK session time with credited Discord messages"
)
async def comparison(i: discord.Interaction, member: discord.Member):
    if not eligible(member):
        return await i.response.send_message(
            "Choose a current recruit.", ephemeral=True
        )
    end = now()
    data = compare(db, member.id, c, end)
    summary = comparison_text(
        data, voice.summary(member.id, end - c["window_days"] * DAY, end), c
    )
    row = db.execute("SELECT player FROM recruit WHERE id=?", (member.id,)).fetchone()
    if not row or not row["player"]:
        summary = "ARK identity awaiting staff link.\n" + summary
    embed = discord.Embed(
        title="Activity comparison",
        description=f"{member.mention} · Last {c['window_days']} days\n{summary}",
        color=int(c["accent_color"], 16),
    )
    if data.hours_by_server:
        ranked = sorted(data.hours_by_server.items(), key=lambda item: -item[1])[:5]
        embed.add_field(
            name="ARK servers · top five by paired time",
            value="\n".join(
                f"{server[:100]} · {hours:.1f} h" for server, hours in ranked
            ),
        )
    days = sorted(
        data.hours_by_day.keys()
        | data.chat_by_day.keys()
        | data.reactions_by_day.keys(),
        reverse=True,
    )[:7]
    if days:
        lines = []
        for day in days:
            date = datetime.fromtimestamp(day * 86400, UTC).strftime("%d %b")
            lines.append(
                f"{date} · {data.hours_by_day[day]:.1f} h in ARK · {data.chat_by_day[day]} messages · {data.reactions_by_day[day]} reactions"
            )
        embed.add_field(name="Latest active dates · UTC", value="\n".join(lines))
    embed.set_footer(
        text="Connected time can include AFK time. Discord online duration is unknown."
    )
    await i.response.send_message(embed=embed, ephemeral=True)


def apply_settings(i, draft, revision):
    global c, scope
    if i.guild_id != draft["guild_id"] or scope.id not in (0, i.guild_id):
        raise ValueError("Open setup in the configured Discord server.")
    if not scope.id and len(bot.guilds) != 1:
        raise ValueError(
            "Finish first setup with the bot installed in one tribe server."
        )
    if (
        not draft["recruit_role"]
        or not draft["evaluation_channel"]
        or not draft["feed_channels"]
    ):
        raise ValueError(
            "Select a recruit role, evaluation board and at least one Overseer feed."
        )
    if not draft["bot_ids"] and not draft["webhook_ids"]:
        raise ValueError("Use Link Overseer post to add its sender.")
    if not draft["owned_servers"] or not draft["recruit_servers"]:
        raise ValueError("Use ARK servers to enter owned and assigned server names.")
    issue = board_issue(i.guild.get_channel(draft["evaluation_channel"]), draft)
    if issue:
        raise ValueError(issue)
    for cid in (
        draft["feed_channels"] + draft["chat_channels"] + draft["voice_channels"]
    ):
        channel = i.guild.get_channel_or_thread(cid)
        if not channel or not channel.permissions_for(i.guild.me).view_channel:
            raise ValueError(f"Give the bot View Channel in <#{cid}>.")
    for role_id in draft["board_reader_roles"]:
        if not i.guild.get_role(role_id) or role_id in (
            i.guild_id,
            draft["recruit_role"],
        ):
            raise ValueError(
                "Choose current officer roles distinct from the recruit role."
            )
    settings.save(draft, revision)
    for uid, (_, state) in list(voice.active.items()):
        if (
            state.channel not in draft["voice_channels"]
            or draft["recruit_role"] != c["recruit_role"]
        ):
            voice.set(uid, None, now())
    c = settings.active
    scope = discord.Object(id=c["guild_id"])
    old_feeds = feeds.copy()
    feeds.clear()
    feeds.update(
        {
            cid: old_feeds.get(cid, {"matched": 0, "unmatched": 0, "last_match": None})
            for cid in c["feed_channels"]
        }
    )
    if refresh.is_running():
        refresh.change_interval(minutes=c["refresh_minutes"])
    sync_voice(c["voice_channels"])


@staff_command(
    "recruit_setup", "Configure this tribe, link channels and adjust evaluations"
)
async def setup_panel(i: discord.Interaction):
    panel = Panel(i, settings, apply_settings, parts)
    await i.response.send_message(panel.summary(), view=panel, ephemeral=True)


@staff_command(
    "recruit_status",
    "Check board privacy, permissions and the status of each Overseer feed",
)
async def setup_status(i: discord.Interaction):
    issue = board_issue(i.guild.get_channel(c["evaluation_channel"]))
    lines = [f"Board · {issue or 'ready'}"]
    for channel, stats in feeds.items():
        ch = i.guild.get_channel_or_thread(channel)
        if not ch or not ch.permissions_for(i.guild.me).view_channel:
            lines.append(f"<#{channel}> · missing channel access")
            continue
        last = (
            f"<t:{stats['last_match']}:R>"
            if stats["last_match"]
            else "no match since startup"
        )
        lines.append(
            f"<#{channel}> · {last} · {stats['matched']} matched / {stats['unmatched']} unmatched"
        )
    body = "\n".join(lines)
    if len(body) > 1900:
        import io

        return await i.response.send_message(
            file=discord.File(io.BytesIO(body.encode()), filename="setup.txt"),
            ephemeral=True,
        )
    await i.response.send_message(body, ephemeral=True)


@staff_command(
    "recruit_uncredit",
    "Remove an incorrectly recorded contribution by exact task reference",
)
async def uncredit(
    i: discord.Interaction,
    member: discord.Member,
    task: app_commands.Range[str, 1, 200],
):
    if not eligible(member):
        return await i.response.send_message(
            "Choose a current recruit.", ephemeral=True
        )
    with db:
        changed = db.execute(
            "DELETE FROM event WHERE uid=? AND source=? AND kind='credit'",
            (member.id, "task:" + norm(task)),
        ).rowcount
    await i.response.send_message(
        "Credit removed." if changed else "No matching task reference.", ephemeral=True
    )


@tree.error
async def command_error(i, error):
    if isinstance(error, app_commands.MissingPermissions):
        await i.response.send_message(
            "Manage Server permission is required.", ephemeral=True
        )
    elif isinstance(error, app_commands.CheckFailure):
        await i.response.send_message(str(error), ephemeral=True)
    else:
        log.error("Recruit command failed", exc_info=error)
        if not i.response.is_done():
            await i.response.send_message(
                "Command failed. Check the host log.", ephemeral=True
            )


if __name__ == "__main__":
    bot.run(os.environ["DISCORD_TOKEN"])
