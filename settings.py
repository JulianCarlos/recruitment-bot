"""Store the active single-guild configuration alongside its recruit records."""

import copy
import json
import os
import tomllib
from pathlib import Path

from core import config, validate_config


class Settings:
    def __init__(self, db):
        self.db = db
        db.executescript("""
            CREATE TABLE IF NOT EXISTS scope(guild INTEGER, role INTEGER);
            CREATE TABLE IF NOT EXISTS settings(
                id INTEGER PRIMARY KEY CHECK(id=1), revision INTEGER, body TEXT);
        """)
        saved = db.execute("SELECT revision,body FROM settings WHERE id=1").fetchone()
        if saved:
            self.revision, body = saved
            self.active = validate_config(json.loads(body))
            return
        self.revision = 0
        self.active = config(Path(__file__).with_name("config.example.toml"))
        self.active.update(
            guild_id=0,
            recruit_role=0,
            evaluation_channel=0,
            feed_channels=[],
            chat_channels=[],
            voice_channels=[],
            bot_ids=[],
            webhook_ids=[],
            recruit_servers=[],
            owned_servers=[],
        )
        binding = db.execute("SELECT guild,role FROM scope").fetchone()
        if binding:
            self.active.update(guild_id=binding[0], recruit_role=binding[1])
        legacy = Path(os.environ.get("RECRUIT_CONFIG", "config.toml"))
        if os.environ.get("RECRUIT_CONFIG_TOML"):
            imported = validate_config(tomllib.loads(os.environ["RECRUIT_CONFIG_TOML"]))
        elif legacy.exists():
            imported = config(legacy)
        else:
            return
        if binding and tuple(binding) != (
            imported["guild_id"],
            imported["recruit_role"],
        ):
            raise ValueError(
                "Existing recruit database belongs to a different guild or role"
            )
        self.save(imported, 0)

    def save(self, candidate, revision):
        if revision != self.revision:
            raise ValueError(
                "Settings changed in another panel. Reopen /recruit_setup."
            )
        if self.active["guild_id"] not in (0, candidate["guild_id"]):
            raise ValueError("This database belongs to another Discord server.")
        validate_config(candidate)
        with self.db:
            self.db.execute("DELETE FROM scope")
            self.db.execute(
                "INSERT INTO scope VALUES(?,?)",
                (candidate["guild_id"], candidate["recruit_role"]),
            )
            self.db.execute(
                "INSERT OR REPLACE INTO settings VALUES(1,?,?)",
                (revision + 1, json.dumps(candidate)),
            )
        self.active = copy.deepcopy(candidate)
        self.revision += 1
