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

"""Handles responding to interactions.

Provides the /pingme slash command as a Cog with an app command.
"""

import logging
from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands

from crabstero import flags
from crabstero.flags import EntityType, Flag

if TYPE_CHECKING:
    from crabstero.bot import Crabstero

logger = logging.getLogger(__name__)


class InteractionCog(commands.Cog):
    """Cog for handling slash command interactions."""

    def __init__(self, bot: Crabstero) -> None:
        """Create a new interaction handler cog.

        :param bot: The bot instance.
        """
        self.bot = bot

    @app_commands.command(
        name="pingme",
        description=(
            "Opts into (or back out of) receiving pings"
            " for generated message which mention you."
        ),
    )
    async def pingme(self, interaction: discord.Interaction) -> None:
        """Toggle the allowPings flag for the user who ran the command.

        :param interaction: The interaction event.
        """
        # Wrap in try/except to always send a response even if the database fails.
        try:
            if await flags.is_flag_set(
                self.bot.db, interaction.user, EntityType.USER, Flag.ALLOW_PINGS
            ):
                await flags.clear_flag(
                    self.bot.db, interaction.user, EntityType.USER, Flag.ALLOW_PINGS
                )
                await interaction.response.send_message(
                    "I will no longer ping you for messages which"
                    " mention you. If you decide to opt back in,"
                    " run `/pingme` any time.",
                    ephemeral=True,
                )
            else:
                await flags.set_flag(
                    self.bot.db, interaction.user, EntityType.USER, Flag.ALLOW_PINGS
                )
                await interaction.response.send_message(
                    "I will now ping you for messages which mention"
                    " you. If you change your mind, run"
                    " `/pingme` any time.",
                    ephemeral=True,
                )

        except Exception:
            logger.exception(
                "An exception occurred while attempting to execute"
                " slash command responder for command 'pingme'."
            )
            await interaction.response.send_message(
                "An error occurred while executing your command."
                " Please try again later.",
                ephemeral=True,
            )


async def setup(bot: Crabstero) -> None:
    """Add the InteractionCog to the bot.

    :param bot: The bot instance.
    """
    await bot.add_cog(InteractionCog(bot))
