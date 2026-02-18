# Copyright 2026 Logan Fick
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""
Handles server-level events which trigger channel history ingestion.

All of these events share the same outcome: queuing all textable channels for message history
ingestion when something changes that may grant new read permissions.
"""

import contextlib
import logging
from typing import TYPE_CHECKING

import discord
from discord.ext import commands

from crabstero.tasks.ingestion import queue_channels_for_ingestion

if TYPE_CHECKING:
    from crabstero.bot import Crabstero

logger = logging.getLogger(__name__)


class ServerEventsCog(commands.Cog):
    """
    Cog for handling server-level events that trigger channel history ingestion.
    """

    def __init__(self, bot: Crabstero) -> None:
        """
        Creates a new server events handler cog.

        :param bot: The bot instance.
        """
        self.bot = bot

    @commands.Cog.listener()
    async def on_guild_join(self, guild: discord.Guild) -> None:
        """
        Queues all text channels for message history ingestion when joining a new server.
        Also logs the join and sends an embed to the bot owner.

        :param guild: The guild that was joined.
        """
        logger.info('Joined new server! (Name: "%s" | ID: %s)', guild.name, guild.id)

        embed = discord.Embed(
            title="Joined New Server", color=discord.Color.from_rgb(255, 165, 0)
        )
        embed.add_field(
            name=f"{guild.name} ({guild.id})",
            value=f"{guild.member_count} members",
        )

        if guild.icon:
            embed.set_image(url=guild.icon.url)

        embed.set_footer(text=f"{len(self.bot.guilds)} total servers")

        app_info = await self.bot.application_info()
        if app_info.owner:
            with contextlib.suppress(discord.HTTPException):
                await app_info.owner.send(embed=embed)

        queue_channels_for_ingestion(guild, self.bot)

    @commands.Cog.listener()
    async def on_guild_available(self, guild: discord.Guild) -> None:
        """
        Queues all textable channels for message history ingestion when a server becomes available.

        :param guild: The guild that became available.
        """
        queue_channels_for_ingestion(guild, self.bot)

    @commands.Cog.listener()
    async def on_guild_role_update(
        self, before: discord.Role, after: discord.Role
    ) -> None:
        """
        Queues all text channels for message history ingestion when role permissions change,
        but only if the bot is a member of the updated role.

        :param before: The role before the update.
        :param after: The role after the update.
        """
        if before.permissions == after.permissions:
            return

        if after.guild.me in after.members:
            queue_channels_for_ingestion(after.guild, self.bot)

    @commands.Cog.listener()
    async def on_guild_channel_update(
        self, before: discord.abc.GuildChannel, after: discord.abc.GuildChannel
    ) -> None:
        """
        Queues all text channels for message history ingestion when channel override permissions
        change.

        :param before: The channel before the update.
        :param after: The channel after the update.
        """
        if before.overwrites == after.overwrites:
            return

        queue_channels_for_ingestion(after.guild, self.bot)


async def setup(bot: Crabstero) -> None:
    """
    Adds the ServerEventsCog to the bot.

    :param bot: The bot instance.
    """
    await bot.add_cog(ServerEventsCog(bot))
