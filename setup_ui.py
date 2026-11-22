"""Private Discord configuration panel with one draft per officer."""

import copy
import re

import discord

from configure import event_template
from core import KEYS, parse

TEXT = [discord.ChannelType.text, discord.ChannelType.news]
CHAT = TEXT + [
    discord.ChannelType.public_thread,
    discord.ChannelType.private_thread,
    discord.ChannelType.news_thread,
]
PICKERS = {
    "Recruit role": ("recruit_role", "role", None, 1),
    "Evaluation board": ("evaluation_channel", "channel", TEXT, 1),
    "Overseer feeds": ("feed_channels", "channel", CHAT, 25),
    "Tracked text channels": ("chat_channels", "channel", CHAT, 25),
    "Tracked voice channels": (
        "voice_channels",
        "channel",
        [discord.ChannelType.voice, discord.ChannelType.stage_voice],
        25,
    ),
    "Officer reader roles": ("board_reader_roles", "role", None, 25),
}
# Label, config key, input type. Each modal supports up to five fields.
FORMS = {
    "ARK servers": [
        ("Owned servers · one per line", "owned_servers", "lines"),
        ("Recruit servers · one per line", "recruit_servers", "lines"),
    ],
    "Score weights": [(key, key, "weight") for key in KEYS],
    "Score targets": [(key, key, "target") for key in KEYS],
    "Time windows": [
        (k.replace("_", " "), k, "int")
        for k in (
            "window_days",
            "retention_days",
            "status_hours",
            "max_session_hours",
            "refresh_minutes",
        )
    ],
    "Discord participation": [
        (
            k.replace("_", " "),
            k,
            "bool" if k in ("track_reactions", "reaction_activity_days") else "int",
        )
        for k in (
            "track_reactions",
            "reaction_activity_days",
            "chat_cooldown_minutes",
            "reaction_cooldown_minutes",
        )
    ],
    "Board and missions": [
        ("Board title", "board_title", "text"),
        ("Accent color · six hex digits", "accent_color", "text"),
        ("Officer missions · one per line", "missions", "lines"),
    ],
    "Feed patterns": [
        ("Join · layout or regex", "join", "pattern"),
        ("Leave · layout or regex", "leave", "pattern"),
    ],
    "Trusted senders": [
        ("Bot IDs · one per line", "bot_ids", "ids"),
        ("Webhook IDs · one per line", "webhook_ids", "ids"),
    ],
}


def decode(kind, text):
    if kind == "pattern":
        if "{player}" in text:
            if "{server}" in text:
                return event_template(text)
            if text.count("{player}") != 1:
                raise ValueError("Use one {player} placeholder")
            a, b = text.replace(r"\n", "\n").split("{player}")
            return (
                "(?im)^" + re.escape(a) + r"(?P<player>[^\r\n]+?)" + re.escape(b) + "$"
            )
        if "player" not in re.compile(text).groupindex:
            raise ValueError(
                "Include {player} or a named player capture in each pattern"
            )
    if kind in ("lines", "ids"):
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        return [int(x) for x in lines] if kind == "ids" else lines
    if kind == "bool":
        if text.lower() not in ("true", "false"):
            raise ValueError("Enter true or false")
        return text.lower() == "true"
    if kind in ("weight", "target"):
        return float(text)
    if kind == "int":
        return int(text)
    return text


def field_group(draft, kind):
    section = {"weight": "weights", "target": "targets", "pattern": "patterns"}.get(
        kind
    )
    return draft[section] if section else draft


class Form(discord.ui.Modal):
    def __init__(self, panel, section):
        super().__init__(title=section)
        self.panel = panel
        self.fields = []
        for label, key, kind in FORMS[section]:
            value = field_group(panel.draft, kind)[key]
            default = (
                "\n".join(map(str, value)) if isinstance(value, list) else str(value)
            )
            field = discord.ui.TextInput(
                label=label,
                default=default,
                required=kind not in ("ids", "lines"),
                style=discord.TextStyle.paragraph
                if kind in ("ids", "lines", "pattern")
                else discord.TextStyle.short,
            )
            self.fields.append((key, kind, field))
            self.add_item(field)

    async def on_submit(self, i):
        if not await self.panel.interaction_check(i):
            return
        try:
            edits = [
                (key, kind, decode(kind, field.value.strip()))
                for key, kind, field in self.fields
            ]
        except (ValueError, re.error) as e:
            return await i.response.send_message(str(e)[:1000], ephemeral=True)
        for key, kind, value in edits:
            field_group(self.panel.draft, kind)[key] = value
        await self.panel.redraw(i, "Draft updated. Use Save settings when ready.")


class FeedSample(discord.ui.Modal, title="Link an Overseer post"):
    link = discord.ui.TextInput(label="Message link · right-click a real feed post")
    server = discord.ui.TextInput(
        label="ARK server for this feed (optional)", required=False, max_length=180
    )

    def __init__(self, panel):
        super().__init__()
        self.panel = panel

    async def on_submit(self, i):
        if not await self.panel.interaction_check(i):
            return
        match = re.fullmatch(
            r"https://(?:(?:canary|ptb)\.)?discord(?:app)?\.com/channels/(\d+)/(\d+)/(\d+)/?",
            self.link.value.strip(),
        )
        if not match or int(match[1]) != i.guild_id:
            return await i.response.send_message(
                "Copy a message link from this Discord server.", ephemeral=True
            )
        ch = i.guild.get_channel_or_thread(int(match[2]))
        if (
            not ch
            or ch.id not in self.panel.draft["feed_channels"]
            or not ch.permissions_for(i.user).view_channel
        ):
            return await i.response.send_message(
                "Select that Overseer feed in this draft first.", ephemeral=True
            )
        await i.response.defer(ephemeral=True)
        try:
            msg = await ch.fetch_message(int(match[3]))
        except discord.HTTPException:
            return await i.followup.send(
                "Could not read the post. Check View Channel and Read Message History.",
                ephemeral=True,
            )
        if not msg.webhook_id and not msg.author.bot:
            return await i.followup.send(
                "Select a post sent by the Overseer bot or webhook.", ephemeral=True
            )
        draft = self.panel.draft
        key = "webhook_ids" if msg.webhook_id else "bot_ids"
        sender = msg.webhook_id or msg.author.id
        if sender not in draft[key]:
            draft[key].append(sender)
        if self.server.value.strip():
            draft["channel_servers"][str(ch.id)] = self.server.value.strip()
        else:
            draft["channel_servers"].pop(str(ch.id), None)
        hits = set().union(
            *(parse(draft, part, ch.id) for part in self.panel.parts(msg))
        )
        preview = "\n".join(
            f"{kind} · {player} · {server}" for kind, player, server in sorted(hits)
        )[:900]
        await i.followup.send(
            "Sender linked in draft. "
            + (preview or "No event matched. Edit Feed patterns and test again.")
            + "\nUse Save settings to apply.",
            ephemeral=True,
        )


class Panel(discord.ui.View):
    def __init__(self, i, settings, apply, parts):
        super().__init__(timeout=900)
        self.owner = i.user.id
        self.guild = i.guild_id
        self.revision = settings.revision
        self.draft = copy.deepcopy(settings.active)
        self.draft["guild_id"] = i.guild_id
        self.apply = apply
        self.parts = parts
        self.section = None
        self.menu = discord.ui.Select(
            placeholder="Choose a setting",
            row=0,
            options=[
                discord.SelectOption(label=label)
                for label in [*PICKERS, "Link Overseer post", *FORMS]
            ],
        )
        self.menu.callback = self.choose
        self.add_item(self.menu)

    async def interaction_check(self, i):
        if (
            i.guild_id == self.guild
            and i.user.id == self.owner
            and i.user.guild_permissions.manage_guild
            and not self.is_finished()
        ):
            return True
        await i.response.send_message(
            "Open your own setup panel with Manage Server permission.", ephemeral=True
        )
        return False

    def summary(self, note=""):
        d = self.draft

        def links(ids):
            return (
                ", ".join(f"<#{cid}>" for cid in ids[:5])
                + (f" +{len(ids) - 5}" if len(ids) > 5 else "")
                if ids
                else "None"
            )

        role = f"<@&{d['recruit_role']}>" if d["recruit_role"] else "Choose a role"
        return (
            f"**Recruit setup** · private draft\nRole · {role}\nBoard · {links([d['evaluation_channel']] if d['evaluation_channel'] else [])}"
            f"\nOverseer · {links(d['feed_channels'])}\nText · {links(d['chat_channels'])}\nVoice · {links(d['voice_channels'])}"
            f"\nTrusted senders · {len(d['bot_ids']) + len(d['webhook_ids'])}\n"
            "Choose the role, board and feeds. Link a real Overseer post, set your ARK servers, then save.\n"
            + note
        )

    async def redraw(self, i, note):
        await i.response.edit_message(content=self.summary(note), view=self)

    async def choose(self, i):
        label = self.menu.values[0]
        if label in FORMS:
            return await i.response.send_modal(Form(self, label))
        if label == "Link Overseer post":
            return await i.response.send_modal(FeedSample(self))
        key, kind, types, count = PICKERS[label]
        if self.section:
            self.remove_item(self.section)
        current = self.draft[key]
        ids = ([current] if current else []) if count == 1 else current
        value_type = (
            discord.SelectDefaultValueType.role
            if kind == "role"
            else discord.SelectDefaultValueType.channel
        )
        options = {
            "default_values": [
                discord.SelectDefaultValue(id=cid, type=value_type)
                for cid in ids[:count]
            ],
            "placeholder": label,
            "row": 1,
            "min_values": 1 if count == 1 else 0,
            "max_values": count,
        }
        self.section = (
            discord.ui.RoleSelect(**options)
            if kind == "role"
            else discord.ui.ChannelSelect(channel_types=types, **options)
        )
        selector = self.section

        async def selected(interaction):
            ids = [obj.id for obj in selector.values]
            if interaction.guild_id in ids and kind == "role":
                return await interaction.response.send_message(
                    "Choose a dedicated role instead of @everyone.", ephemeral=True
                )
            old = self.draft[key]
            self.draft[key] = ids[0] if count == 1 else ids
            if key == "feed_channels":
                self.draft["channel_servers"] = {
                    k: v
                    for k, v in self.draft["channel_servers"].items()
                    if int(k) in ids
                }
            self.remove_item(selector)
            self.section = None
            note = "Selection updated in draft."
            if key == "recruit_role" and old and old != ids[0]:
                note = "Saving a new recruit role removes records for members outside that role at the next board refresh."
            await self.redraw(interaction, note)

        selector.callback = selected
        self.add_item(selector)
        await self.redraw(
            i,
            f"Select the full list for {label.lower()}. This replaces its previous selection.",
        )

    @discord.ui.button(label="Save settings", style=discord.ButtonStyle.success, row=2)
    async def save(self, i, button):
        try:
            self.apply(i, self.draft, self.revision)
        except (ValueError, re.error) as e:
            return await i.response.send_message(str(e)[:1500], ephemeral=True)
        self.stop()
        await i.response.edit_message(
            content="Settings saved. Use /recruit_status to check feed activity and board access.",
            view=None,
        )
