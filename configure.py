"""Interactive first-run configuration. Uses only the Python standard library."""

import json
import os
import re
import tempfile
from pathlib import Path

from core import config

ROOT = Path(__file__).resolve().parent


def ask(label, convert, optional=False):
    while True:
        raw = input(label + (" (Enter to skip)" if optional else "") + ": ").strip()
        if not raw and optional:
            return []
        try:
            if raw:
                return convert(raw)
        except ValueError:
            pass
        print("Please enter a valid value.")


def discord_id(text):
    value = int(text)
    if not 0 < value < 2**63:
        raise ValueError
    return value


def ids(text):
    return [discord_id(x.strip()) for x in text.split(",")]


def names(text):
    return [x.strip() for x in text.split(",") if x.strip()]


def event_template(text):
    """Turn a literal sample layout into a line-anchored event matcher."""
    if text.count("{player}") != 1 or text.count("{server}") != 1:
        raise ValueError("Use {player} and {server} exactly once")
    text = text.replace(r"\n", "\n")
    parts = re.split(r"(\{player\}|\{server\})", text)
    groups = {
        "{player}": r"(?P<player>[^\r\n]+?)",
        "{server}": r"(?P<server>[^\r\n]+?)",
    }
    return "(?im)^" + "".join(groups.get(p, re.escape(p)) for p in parts) + "$"


def main():
    dest = Path("config.toml")
    if dest.exists():
        raise SystemExit(
            "config.toml already exists. Edit it directly to change your setup."
        )
    print(
        "Tribe setup · Enable Discord Developer Mode to copy IDs. Use commas for lists."
    )
    values = {}
    for key, label in (
        ("guild_id", "Discord server ID"),
        ("recruit_role", "Recruit role ID"),
        ("evaluation_channel", "Private staff text channel ID"),
    ):
        values[key] = ask(label, discord_id)
    values["feed_channels"] = ask("Overseer feed channel IDs", ids)
    values["bot_ids"] = ask("Overseer sender bot IDs", ids, True)
    values["webhook_ids"] = ask("Overseer webhook IDs", ids, bool(values["bot_ids"]))
    values["chat_channels"] = ask("Discord channels for activity counts", ids, True)
    values["voice_channels"] = ask(
        "Voice or stage channel IDs for duration tracking", ids, True
    )
    values["owned_servers"] = ask("Owned ARK server names as shown in the feed", names)
    values["recruit_servers"] = ask("ARK servers assigned to recruits", names)
    values["board_title"] = (
        input("Board title [Tribe field report]: ").strip() or "Tribe field report"
    )
    print(
        "Optional feed layouts: replace the actual player and server with {player} and {server}."
    )
    print(
        r"For multiline embeds use literal \n between lines. Enter keeps the example layout."
    )
    for event in ("join", "leave"):
        pattern = ask(f"Layout for {event}", event_template, True)
        if pattern:
            values[event] = pattern
    template = (ROOT / "config.example.toml").read_text(encoding="utf-8")
    for key, value in values.items():
        line = f"{key} = {json.dumps(value, ensure_ascii=False)}"
        template = re.sub(
            rf"^{key} = .*$",
            lambda _, replacement=line: replacement,
            template,
            flags=re.MULTILINE,
        )
    with tempfile.TemporaryDirectory(dir=dest.parent) as temp:
        staged = Path(temp) / "config.toml"
        staged.write_text(template, encoding="utf-8")
        try:
            config(staged)
        except ValueError as e:
            raise SystemExit(
                f"Setup needs correction: {e}. Run the wizard again."
            ) from e
        os.link(staged, dest)
    print(
        "Saved config.toml. Calibrate a real join and leave with check_feed.py, then start the bot."
    )


if __name__ == "__main__":
    main()
