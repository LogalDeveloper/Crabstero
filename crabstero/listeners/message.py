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
Handles created messages.

Responds to mentions and ingests normal text messages as a Cog with an on_message listener.
"""

from typing import TYPE_CHECKING

import discord
from discord.ext import commands

from crabstero.messages import ingest_message, reply_to_message

if TYPE_CHECKING:
    from crabstero.bot import Crabstero


class MessageCog(commands.Cog):
    """
    Cog for handling message creation events.
    """

    def __init__(self, bot: Crabstero) -> None:
        """
        Creates a new message creation handler cog.

        :param bot: The bot instance.
        """
        self.bot = bot

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        """
        Responds to mentions and ingests normal text messages.

        :param message: The message event.
        """
        channel = message.channel

        if not isinstance(
            channel, (discord.TextChannel, discord.Thread, discord.VoiceChannel)
        ):
            return

        if message.author.bot or message.author == self.bot.user:
            return

        if self.bot.user in message.mentions:
            await reply_to_message(self.bot.db, message)
            return

        # Only ingest non-thread messages; threads share their parent channel's chain.
        if message.type == discord.MessageType.default and not isinstance(
            channel, discord.Thread
        ):
            await ingest_message(self.bot.db, message)


async def setup(bot: Crabstero) -> None:
    """
    Adds the MessageCog to the bot.

    :param bot: The bot instance.
    """
    await bot.add_cog(MessageCog(bot))
