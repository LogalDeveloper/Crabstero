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

Provides the /pingme and /forgetme slash commands as a Cog with app commands.
"""

import logging
from typing import TYPE_CHECKING, override

import discord
from discord import app_commands
from discord.ext import commands

from crabstero import flags, metrics
from crabstero.bot import TrackedView
from crabstero.flags import EntityType, Flag

if TYPE_CHECKING:
    from crabstero.bot import Crabstero
    from crabstero.database import Database

logger = logging.getLogger(__name__)


class ForgetMeView(TrackedView):
    """Confirmation view with Confirm/Cancel buttons for /forgetme."""

    def __init__(
        self,
        user_id: int,
        db: Database,
        interaction: discord.Interaction,
    ) -> None:
        """Create a new ForgetMeView.

        :param user_id: The Discord user ID who invoked the command.
        :param db: The database instance.
        :param interaction: The original interaction for editing on timeout.
        """
        super().__init__()
        self._user_id = user_id
        self._db = db
        self._responded = False
        self._interaction = interaction

    @override
    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        """Only allow the invoking user to interact with the buttons.

        :param interaction: The interaction event.
        :return: True if the user matches, False otherwise.
        """
        return interaction.user.id == self._user_id

    @discord.ui.button(label="Confirm", style=discord.ButtonStyle.danger)
    async def confirm(
        self,
        interaction: discord.Interaction,
        _button: discord.ui.Button[ForgetMeView],
    ) -> None:
        """Delete all user data, clear flags, and set noIngest.

        :param interaction: The interaction event.
        :param _button: The button that was pressed.
        """
        self._responded = True
        await self._db.forget_user(self._user_id, Flag.NO_INGEST)
        metrics.FORGETME.labels(outcome="completed").inc()
        await interaction.response.edit_message(
            content=(
                "I have deleted your data and I will not use"
                " your messages going forward."
            ),
            view=None,
        )
        self.stop()

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(
        self,
        interaction: discord.Interaction,
        _button: discord.ui.Button[ForgetMeView],
    ) -> None:
        """Cancel the /forgetme action.

        :param interaction: The interaction event.
        :param _button: The button that was pressed.
        """
        self._responded = True
        metrics.FORGETME.labels(outcome="cancelled").inc()
        await interaction.response.edit_message(
            content="Action cancelled. I have not modified your data.",
            view=None,
        )
        self.stop()

    @override
    async def on_timeout(self) -> None:
        """Remove buttons and increment timeout metric."""
        if self._responded:
            return
        metrics.FORGETME.labels(outcome="timeout").inc()
        try:
            await self._interaction.edit_original_response(
                content="This timed out. Run `/forgetme` again if you still want to.",
                view=None,
            )
        except discord.NotFound:
            logger.debug(
                "Original /forgetme response for user %s was already deleted.",
                self._user_id,
            )
        except discord.HTTPException:
            logger.debug(
                "Failed to edit timed-out /forgetme response for user %s.",
                self._user_id,
            )


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
            "Toggle whether you receive pings for generated messages that mention you."
        ),
    )
    async def pingme(self, interaction: discord.Interaction) -> None:
        """Toggle the allowPings flag for the user who ran the command.

        :param interaction: The interaction event.
        """
        if await flags.is_flag_set(
            self.bot.db,
            interaction.user,
            EntityType.USER,
            Flag.ALLOW_PINGS,
        ):
            await flags.clear_flag(
                self.bot.db,
                interaction.user,
                EntityType.USER,
                Flag.ALLOW_PINGS,
            )
            metrics.PINGME.labels(outcome="opted_out").inc()
            await interaction.response.send_message(
                "I will no longer ping you for messages which"
                " mention you. If you decide to opt back in,"
                " run `/pingme` any time.",
                ephemeral=True,
            )
        else:
            await flags.set_flag(
                self.bot.db,
                interaction.user,
                EntityType.USER,
                Flag.ALLOW_PINGS,
            )
            metrics.PINGME.labels(outcome="opted_in").inc()
            await interaction.response.send_message(
                "I will now ping you for messages which mention"
                " you. If you change your mind, run"
                " `/pingme` any time.",
                ephemeral=True,
            )

    @app_commands.command(
        name="forgetme",
        description="Delete all your data and stop your messages from being used.",
    )
    async def forgetme(self, interaction: discord.Interaction) -> None:
        """Delete all user data and set the noIngest flag.

        :param interaction: The interaction event.
        """
        if await flags.is_flag_set(
            self.bot.db,
            interaction.user,
            EntityType.USER,
            Flag.NO_INGEST,
        ):
            metrics.FORGETME.labels(outcome="already_forgotten").inc()
            await interaction.response.send_message(
                "I have already removed your data and I am not using your messages.",
                ephemeral=True,
            )
            return

        view = ForgetMeView(interaction.user.id, self.bot.db, interaction)
        await interaction.response.send_message(
            "I will delete everything I have learned from your"
            " messages and I will not use them going forward."
            " Would you like to proceed?",
            view=view,
            ephemeral=True,
        )


async def setup(bot: Crabstero) -> None:
    """Add the InteractionCog to the bot.

    :param bot: The bot instance.
    """
    await bot.add_cog(InteractionCog(bot))
