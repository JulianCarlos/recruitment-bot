import os
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import discord


# Each test imports the application with isolated configuration and in-memory state.
class VisibilityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with patch.dict(
            os.environ,
            {
                "RECRUIT_CONFIG": str(Path(__file__).with_name("config.example.toml")),
                "RECRUIT_DB": ":memory:",
            },
        ):
            import bot
        cls.app = bot

    def test_board_access(self):
        app = self.app
        ch = Mock(spec=discord.TextChannel)
        g = ch.guild
        g.id, g.chunked, g.unavailable = app.scope.id, True, False
        role = g.get_role.return_value
        role.id = app.c["recruit_role"]
        role_perms = discord.Permissions.none()
        reader = Mock(spec=discord.Member)
        reader.guild, reader.id, reader.roles, reader.bot = g, 17, [], False
        reader.guild_permissions = discord.Permissions.none()
        g.members = [reader]
        ch.permissions_for.side_effect = lambda x: (
            role_perms if x in (role, g.default_role) else discord.Permissions.all()
        )
        with patch.object(app, "bot") as client:
            client.user.id = 42
            self.assertIn("unapproved", app.board_issue(ch))
            reader.guild_permissions.manage_guild = True
            self.assertIsNone(app.board_issue(ch))
            reader.roles = [role]
            self.assertIn("recruit", app.board_issue(ch))
            role_perms.view_channel = True
            self.assertIn("@everyone", app.board_issue(ch))
