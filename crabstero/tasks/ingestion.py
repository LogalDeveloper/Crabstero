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
Bulk-ingests the message history of channels.

Channels are enqueued for processing by the bot's fixed ingestion worker pool.
"""

import logging
from typing import TYPE_CHECKING

import discord

from crabstero.messages import ingest_message

if TYPE_CHECKING:
    from crabstero.bot import Crabstero
    from crabstero.database import Database

MAXIMUM_MESSAGES_PER_CHANNEL = (
    50000  # The maximum amount of historical messages to ingest per channel.
)

logger = logging.getLogger(__name__)


def queue_channels_for_ingestion(guild: discord.Guild, bot: Crabstero) -> None:
    """
    Enqueues every textable channel in a guild for message history ingestion.

    :param guild: The Discord guild whose channels should be ingested.
    :param bot: The bot instance.
    """
    for channel in guild.channels:
        if isinstance(channel, (discord.TextChannel, discord.VoiceChannel)):
            bot.queue_channel_for_ingestion(channel)


async def ingest_channel(
    channel: discord.TextChannel | discord.VoiceChannel,
    db: Database,
) -> None:
    """
    Bulk-ingests the message history of a given channel. The task will be ended early if
    permissions do not allow ingesting this channel or if it has already been ingested in the past.

    :param channel: The channel to ingest.
    :param db: The database instance.
    """
    try:
        if not channel.permissions_for(channel.guild.me).read_message_history:
            logger.warning(
                "[%s] Unable to ingest textable channel history due to lacking permissions. Ignoring.",
                channel.id,
            )
            return

        if await db.is_channel_ingested(channel.id):
            return

        await db.mark_channel_ingested(channel.id)

        logger.info("[%s] Starting ingestion of textable channel history.", channel.id)

        count = 0
        async for message in channel.history(limit=MAXIMUM_MESSAGES_PER_CHANNEL):
            count += 1
            await ingest_message(db, message)

        logger.info(
            "[%s] Ingestion of textable channel history complete. %d messages ingested.",
            channel.id,
            count,
        )

    except Exception:
        logger.exception(
            "[%s] An error occurred while ingesting textable channel history!",
            channel.id,
        )
