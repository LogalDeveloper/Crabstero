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

"""Generate Discord reply messages and ingest raw messages.

Orchestrates Markov chain generation and ingestion in the context of
Discord messages, handling reply logic, embed generation, mention
filtering, and message ingestion with flag checks.
"""

import re
import secrets
from typing import TYPE_CHECKING

import discord

from crabstero import flags, markov, metrics
from crabstero.cache import CachedMessage, IngestCache
from crabstero.database import ChannelImage
from crabstero.flags import EntityType, Flag

if TYPE_CHECKING:
    from crabstero.database import Database

# Copied from discordjs/discord-api-types:
# https://github.com/discordjs/discord-api-types/blob/662cb0cb0ac9c6f9ad93e180849476714bfceb0c/globals.ts#L39
_MENTION_PATTERN = re.compile(r"<@!?(\d{17,20})>")

_EMBED_CHANCE_THRESHOLD = 95  # Out of 100; sends an embed ~5% of the time.


async def reply_to_message(db: Database, message: discord.Message) -> None:
    """Send a new message in Discord in response to a given message.

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

    body = await markov.generate(db, channel_id)

    embed = None

    # 5% chance to include an embed, if the bot has permission.
    if (
        secrets.randbelow(100) >= _EMBED_CHANCE_THRESHOLD
        and channel.permissions_for(guild.me).embed_links
    ):
        embed = discord.Embed(
            title=await markov.generate(db, channel_id, soft_limit=200, hard_limit=300),
            description=await markov.generate(
                db,
                channel_id,
                soft_limit=300,
                hard_limit=500,
            ),
        )

        random_image = await db.get_random_image(channel_id)
        if random_image is not None:
            embed.set_image(url=random_image)

    # Suppress all mentions by default; only ping users who opted in.
    allowed_user_ids: set[int] = set()

    for user_id in {int(uid) for uid in _MENTION_PATTERN.findall(body)}:
        if await flags.is_flag_set(db, user_id, EntityType.USER, Flag.ALLOW_PINGS):
            allowed_user_ids.add(user_id)

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
        metrics.EMBEDS_GENERATED.inc()
    else:
        await message.reply(
            content=body,
            allowed_mentions=allowed_mentions,
            mention_author=False,
        )
    metrics.REPLIES_SENT.inc()


def _extract_embed_data(
    embeds: list[discord.Embed],
) -> tuple[list[str], list[str]]:
    """Extract text and image URLs from message embeds.

    :param embeds: The embeds to process.
    :return: A tuple of (texts, image_urls).
    """
    texts: list[str] = []
    image_urls: list[str] = []
    for embed in embeds:
        if embed.title:
            texts.append(embed.title)
        if embed.description:
            texts.append(embed.description)
        if embed.image and embed.image.url:
            image_urls.append(embed.image.url)
    return texts, image_urls


async def ingest_message(
    db: Database,
    message: discord.Message,
    cache: IngestCache | None = None,
) -> None:
    """Ingest a given message into its channel's Markov chain.

    If a cache is provided, the ingested data is recorded so it can be
    reversed by :func:`uningest_message` if the message is deleted shortly
    after.

    :param db: The database instance.
    :param message: The message to ingest.
    :param cache: Optional ingest cache for tracking recently ingested data.
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
    user_id = message.author.id

    if not message.content and not message.embeds:
        return

    embed_texts, image_urls = _extract_embed_data(message.embeds)

    with metrics.MESSAGE_INGESTION_DURATION.time():
        if message.content:
            await markov.ingest(db, channel_id, user_id, message.content)

        for text in embed_texts:
            await markov.ingest(db, channel_id, user_id, text)

        if image_urls:
            await db.add_images(
                [ChannelImage(channel_id, user_id, url) for url in image_urls],
            )

    if cache is not None:
        cache.put(
            message.id,
            CachedMessage(
                channel_id=channel_id,
                user_id=user_id,
                content=message.content or None,
                embed_texts=embed_texts,
                image_urls=image_urls,
            ),
        )


async def uningest_message(db: Database, cache: IngestCache, message_id: int) -> None:
    """Reverse ingestion for a recently deleted message.

    Looks up the message in the cache. If found, removes all Markov data
    and images that were added during ingestion.

    :param db: The database instance.
    :param cache: The ingest cache.
    :param message_id: The Discord message ID that was deleted.
    """
    entry = cache.pop(message_id)
    if entry is None:
        return

    if entry.content:
        await markov.uningest(db, entry.channel_id, entry.user_id, entry.content)

    for text in entry.embed_texts:
        await markov.uningest(db, entry.channel_id, entry.user_id, text)

    if entry.image_urls:
        await db.remove_images(
            [
                ChannelImage(entry.channel_id, entry.user_id, url)
                for url in entry.image_urls
            ],
        )

    metrics.MESSAGES_UNINGESTED.inc()
