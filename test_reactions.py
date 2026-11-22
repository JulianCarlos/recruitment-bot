import os
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import discord

from activity import compare
from core import DAY, config, connect, record, report


class ReactionTests(unittest.TestCase):
    def setUp(self):
        self.config = config(Path(__file__).with_name("config.example.toml"))
        self.db = connect(":memory:")
        self.addCleanup(self.db.close)
        self.db.execute("INSERT INTO recruit VALUES(1, 0, NULL)")
        self.member = self.db.execute("SELECT * FROM recruit").fetchone()

    def test_same_target_and_cooldown(self):
        def react(target, at):
            return record(
                self.db, 1, f"reaction:{target}", "reaction", at, cooldown=600
            )

        self.assertTrue(react(12, 10))
        self.assertFalse(react(13, 609))
        self.assertTrue(react(13, 610))
        self.assertFalse(react(12, DAY))
        self.assertTrue(record(self.db, 1, "13", "chat", 610, cooldown=600))
        result = compare(self.db, 1, self.config, DAY)
        self.assertEqual(sum(result.reactions_by_day.values()), 2)
        self.assertEqual(sum(result.chat_by_day.values()), 1)
        self.assertEqual(result.incomplete, 0)

    def test_distinct_scoring_days_and_report_only(self):
        record(self.db, 1, "reaction:1", "reaction", 10)
        record(self.db, 1, "reaction:2", "reaction", DAY + 10)
        record(self.db, 1, "3", "chat", DAY + 20)
        end = DAY + 30
        self.assertEqual(
            report(self.db, self.member, self.config, end)[0]["chat_days"], 2
        )
        self.config["reaction_activity_days"] = False
        self.assertEqual(
            report(self.db, self.member, self.config, end)[0]["chat_days"], 1
        )


class ReactionGatewayTests(unittest.IsolatedAsyncioTestCase):
    async def test_recruit_channel_and_other_human_author(self):
        with patch.dict(
            os.environ,
            {
                "RECRUIT_CONFIG": str(Path(__file__).with_name("config.example.toml")),
                "RECRUIT_DB": ":memory:",
            },
        ):
            import bot

        db = connect(":memory:")
        self.addCleanup(db.close)
        guild = Mock()
        guild.id = bot.scope.id
        author = Mock(id=23, bot=False)
        member = Mock(spec=discord.Member)
        member.id, member.bot, member.guild = 42, False, guild
        member.roles = [Mock(id=bot.c["recruit_role"])]
        guild.get_member.side_effect = {42: member, 23: author, 24: Mock(bot=True)}.get
        payload = Mock(
            guild_id=guild.id,
            channel_id=bot.c["chat_channels"][0],
            user_id=42,
            message_id=123,
            message_author_id=23,
        )
        with (
            patch.object(bot, "db", db),
            patch.object(bot.bot, "get_guild", return_value=guild),
        ):
            await bot.on_raw_reaction_add(payload)
            await bot.on_raw_reaction_add(payload)
            self.assertEqual(db.execute("SELECT count(*) FROM event").fetchone()[0], 1)
            db.execute("DELETE FROM event")
            for target in (42, 24, None, 25):
                payload.message_author_id = target
                await bot.on_raw_reaction_add(payload)
            payload.message_author_id = 23
            with patch.dict(bot.c, {"track_reactions": False}):
                await bot.on_raw_reaction_add(payload)
            payload.channel_id = 1234567
            await bot.on_raw_reaction_add(payload)
            member.roles = []
            payload.channel_id = bot.c["chat_channels"][0]
            await bot.on_raw_reaction_add(payload)
            self.assertEqual(db.execute("SELECT count(*) FROM event").fetchone()[0], 0)
