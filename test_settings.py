import copy
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import discord

from core import config, connect
from settings import Settings
from setup_ui import FORMS, Form, Panel, decode

EXAMPLE = Path(__file__).with_name("config.example.toml")


class PersistenceTests(unittest.TestCase):
    def test_bootstrap_save_restart_and_stale_draft(self):
        with (
            tempfile.TemporaryDirectory() as tmp,
            patch.dict(
                os.environ,
                {
                    "RECRUIT_CONFIG": str(Path(tmp) / "absent"),
                    "RECRUIT_CONFIG_TOML": "",
                },
            ),
        ):
            path = Path(tmp) / "data.sqlite3"
            db = connect(path)
            state = Settings(db)
            self.assertEqual((state.revision, state.active["guild_id"]), (0, 0))
            candidate = config(EXAMPLE)
            state.save(candidate, 0)
            changed = copy.deepcopy(candidate)
            changed["board_title"] = "Officer station"
            state.save(changed, 1)
            with self.assertRaisesRegex(ValueError, "another panel"):
                state.save(candidate, 1)
            db.close()
            db = connect(path)
            self.addCleanup(db.close)
            restored = Settings(db)
            self.assertEqual(restored.active["board_title"], "Officer station")
            self.assertEqual(restored.revision, 2)
            with self.assertRaisesRegex(ValueError, "another Discord server"):
                restored.save(dict(changed, guild_id=12), 2)

    def test_legacy_import_preserves_recruits(self):
        with patch.dict(
            os.environ, {"RECRUIT_CONFIG": str(EXAMPLE), "RECRUIT_CONFIG_TOML": ""}
        ):
            db = connect(":memory:")
            self.addCleanup(db.close)
            db.execute("INSERT INTO recruit VALUES(123,456,'example')")
            c = config(EXAMPLE)
            db.execute("CREATE TABLE scope(guild INTEGER,role INTEGER)")
            db.execute(
                "INSERT INTO scope VALUES(?,?)", (c["guild_id"], c["recruit_role"])
            )
            self.assertEqual(Settings(db).active, c)
            self.assertEqual(
                tuple(db.execute("SELECT * FROM recruit").fetchone()),
                (123, 456, "example"),
            )


class PanelTests(unittest.IsolatedAsyncioTestCase):
    async def test_picker_and_modal_forms(self):
        c = config(EXAMPLE)
        i = Mock(guild_id=c["guild_id"])
        i.user.id = 42
        i.user.guild_permissions.manage_guild = True
        i.response.edit_message = AsyncMock()
        panel = Panel(i, SimpleNamespace(active=c, revision=1), Mock(), Mock())
        self.assertTrue(await panel.interaction_check(i))
        self.assertLessEqual(len(panel.menu.options), 25)
        for section in FORMS:
            self.assertLessEqual(len(Form(panel, section).children), 5)
        panel.menu._values = ["Tracked voice channels"]
        await panel.choose(i)
        self.assertIsInstance(panel.section, discord.ui.ChannelSelect)
        self.assertIn(discord.ChannelType.voice, panel.section.channel_types)
        self.assertNotIn(discord.ChannelType.text, panel.section.channel_types)
        panel.stop()

    def test_literal_and_advanced_parser(self):
        import re

        for template, sample in (
            ("{player} arrived", "Ada arrived"),
            ("{player} arrived at {server}", "Ada arrived at Home"),
            (r"(?P<player>.+?) arrived", "Ada arrived"),
        ):
            self.assertEqual(
                re.match(decode("pattern", template), sample).group("player"), "Ada"
            )
