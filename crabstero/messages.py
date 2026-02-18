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
Assists with generating Discord messages in response to other users and ingesting raw messages.

Orchestrates Markov chain generation and ingestion in the context of Discord messages, handling
reply logic, embed generation, mention filtering, and message ingestion with flag checks.
"""

import re
import secrets
from typing import TYPE_CHECKING

import discord

from crabstero import flags, markov
from crabstero.flags import EntityType, Flag

if TYPE_CHECKING:
    from crabstero.database import Database

# Copied from discordjs/discord-api-types:
# https://github.com/discordjs/discord-api-types/blob/7fe434114e91c80ed79f0204ae6c73047672d55d/globals.ts#L30
MENTION_PATTERN = re.compile(r"<@!?(?P<id>\d{17,20})>")

_EMBED_CHANCE_THRESHOLD = 95  # Out of 100; sends an embed ~5% of the time.


async def reply_to_message(db: Database, message: discord.Message) -> None:
    """
    Sends a new message in Discord in response to a given message.

    :param db: The database instance.
    :param message: The message prompting the response.
    """
    channel = message.channel
    guild = message.guild
    if guild is None:
        return

    if not channel.permissions_for(guild.me).send_messages:
        return

    if (
        await flags.is_flag_set(db, channel, EntityType.CHANNEL, Flag.NO_REPLY)
        or await flags.is_flag_set(db, guild, EntityType.SERVER, Flag.NO_REPLY)
        or await flags.is_flag_set(db, message.author, EntityType.USER, Flag.NO_REPLY)
    ):
        return

    # Threads share their parent channel's Markov chain.
    if isinstance(channel, discord.Thread):
        channel_id = channel.parent_id
    else:
        channel_id = channel.id

    body = await markov.generate(db, channel_id, 750, 1000)

    embed = None

    # 5% chance to include an embed, if the bot has permission.
    if (
        secrets.randbelow(100) >= _EMBED_CHANCE_THRESHOLD
        and channel.permissions_for(guild.me).embed_links
    ):
        embed = discord.Embed(
            title=await markov.generate(db, channel_id, 200, 300),
            description=await markov.generate(db, channel_id, 300, 500),
        )

        random_image = await db.get_random_image(channel_id)
        if random_image is not None:
            embed.set_image(url=random_image)

    # Suppress all mentions by default; only ping users who opted in.
    allowed_user_ids: list[int] = []

    for match in MENTION_PATTERN.finditer(body):
        user_id = int(match.group("id"))

        if await flags.is_flag_set(db, user_id, EntityType.USER, Flag.ALLOW_PINGS):
            allowed_user_ids.append(user_id)

    allowed_mentions = discord.AllowedMentions(
        everyone=False,
        roles=False,
        users=[discord.Object(id=uid) for uid in allowed_user_ids],
    )

    if embed is not None:
        await message.reply(
            content=body,
            embed=embed,
            allowed_mentions=allowed_mentions,
            mention_author=False,
        )
    else:
        await message.reply(
            content=body,
            allowed_mentions=allowed_mentions,
            mention_author=False,
        )


async def ingest_message(db: Database, message: discord.Message) -> None:
    """
    Ingests a given message into its channel's Markov chain.

    :param db: The database instance.
    :param message: The message to ingest.
    """
    guild = message.guild
    if guild is None:
        return

    if message.author.bot or guild.me in message.mentions:
        return

    if (
        await flags.is_flag_set(db, message.channel, EntityType.CHANNEL, Flag.NO_INGEST)
        or await flags.is_flag_set(db, guild, EntityType.SERVER, Flag.NO_INGEST)
        or await flags.is_flag_set(db, message.author, EntityType.USER, Flag.NO_INGEST)
    ):
        return

    channel_id = message.channel.id

    if message.content:
        await markov.ingest(db, channel_id, message.content)

    for embed in message.embeds:
        await _ingest_embed(db, channel_id, embed)


async def _ingest_embed(db: Database, channel_id: int, embed: discord.Embed) -> None:
    """
    Ingests a given embed into a given channel's Markov chain.

    :param db: The database instance.
    :param channel_id: The ID of the channel to use for the Markov chain.
    :param embed: The embed to ingest.
    """
    if embed.title:
        await markov.ingest(db, channel_id, embed.title)

    if embed.description:
        await markov.ingest(db, channel_id, embed.description)

    if embed.image and embed.image.url:
        await db.add_image(channel_id, embed.image.url)
