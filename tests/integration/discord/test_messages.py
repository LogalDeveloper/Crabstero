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

"""Simcord integration tests for Discord message events."""

from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING

import discord
import pytest
from simcord import run

from crabstero.bot import Crabstero
from crabstero.database import ChannelImage, StartWord, Transition
from crabstero.flags import EntityType, Flag
from crabstero.messages import ingest_message, reply_to_message

if TYPE_CHECKING:
    from pathlib import Path

    from simcord import Env

    from crabstero.database import Database
    from tests.integration.discord.conftest import (
        MakeSimcordMemberChannel,
        SimcordMemberChannel,
        StartWordsForChannel,
    )

type SeedReply = Callable[["Database", int], Awaitable[None]]


@pytest.fixture
def seed_reply() -> SeedReply:
    """Return a seeder for deterministic generated replies in one channel."""

    async def seed(db: Database, channel_id: int) -> None:
        await db.add_markov_data(
            [StartWord(channel_id, 123, "Generated")],
            [Transition(channel_id, 123, "Generated", "reply.")],
        )

    return seed


class TestMessageIngestion:
    """Normal Discord message flow populates the real database."""

    async def test_normal_guild_user_message_is_ingested(
        self,
        simcord_member_channel: SimcordMemberChannel,
        crabstero_bot: Crabstero,
        start_words_for_channel: StartWordsForChannel,
    ) -> None:
        """A default guild text message adds Markov data."""
        member = simcord_member_channel.member
        channel = simcord_member_channel.channel

        await member.send(channel, "Alpha beta.")

        assert await start_words_for_channel(crabstero_bot.db, channel.id) == ["Alpha"]
        assert (
            await crabstero_bot.db.get_random_next_word(channel.id, "Alpha") == "beta."
        )

    async def test_bot_messages_are_ignored(
        self,
        simcord_env: Env,
        simcord_member_channel: SimcordMemberChannel,
        crabstero_bot: Crabstero,
        start_words_for_channel: StartWordsForChannel,
    ) -> None:
        """A bot-authored gateway message is not ingested."""
        channel = simcord_member_channel.channel

        simcord_env.backend.create_message(
            channel.id,
            simcord_env.backend.bot_user.id,
            "Ignore bot.",
        )
        await simcord_env.settle()

        assert await start_words_for_channel(crabstero_bot.db, channel.id) == []

    async def test_dm_messages_are_ignored(
        self,
        simcord_env: Env,
        crabstero_bot: Crabstero,
        start_words_for_channel: StartWordsForChannel,
    ) -> None:
        """DM messages are outside the guild channel types Crabstero handles."""
        user = simcord_env.create_user("Ada")

        await user.send_dm("Direct message.")

        assert (
            await start_words_for_channel(
                crabstero_bot.db,
                user.dm_channel.id,
            )
            == []
        )

    async def test_thread_messages_do_not_create_separate_chain(
        self,
        simcord_env: Env,
        simcord_member_channel: SimcordMemberChannel,
        crabstero_bot: Crabstero,
        start_words_for_channel: StartWordsForChannel,
    ) -> None:
        """Messages inside threads are not ingested under the thread channel id."""
        member = simcord_member_channel.member
        channel = simcord_member_channel.channel
        simcord_env.backend.create_thread(channel.id, "thread", member.id)
        await simcord_env.settle()
        thread = channel.threads[0]

        await member.send(thread, "Thread only.")

        assert await start_words_for_channel(crabstero_bot.db, thread.id) == []
        assert await start_words_for_channel(crabstero_bot.db, channel.id) == []

    async def test_embed_message_text_and_image_are_ingested(
        self,
        simcord_env: Env,
        simcord_member_channel: SimcordMemberChannel,
        crabstero_bot: Crabstero,
        start_words_for_channel: StartWordsForChannel,
    ) -> None:
        """Embed titles, descriptions, and image URLs are ingested."""
        member = simcord_member_channel.member
        channel = simcord_member_channel.channel

        simcord_env.backend.create_message(
            channel.id,
            member.id,
            embeds=[
                {
                    "title": "Title words.",
                    "description": "Description words.",
                    "image": {"url": "https://example.com/embed.png"},
                },
            ],
        )
        await simcord_env.settle()

        assert await start_words_for_channel(crabstero_bot.db, channel.id) == [
            "Description",
            "Title",
        ]
        assert (
            await crabstero_bot.db.get_random_image(channel.id)
            == "https://example.com/embed.png"
        )

    async def test_empty_messages_are_not_ingested(
        self,
        simcord_member_channel: SimcordMemberChannel,
        crabstero_bot: Crabstero,
        start_words_for_channel: StartWordsForChannel,
    ) -> None:
        """A message with no content or embeds is ignored."""
        member = simcord_member_channel.member
        channel = simcord_member_channel.channel

        await member.send(channel)

        assert await start_words_for_channel(crabstero_bot.db, channel.id) == []

    async def test_ingest_only_mode_ingests_without_replying(
        self,
        tmp_path: Path,
        make_simcord_member_channel: MakeSimcordMemberChannel,
        start_words_for_channel: StartWordsForChannel,
    ) -> None:
        """An ingest-only bot still ingests eligible messages and never replies."""
        bot = Crabstero(str(tmp_path / "ingest-only.db"), ingest_only=True)
        try:
            async with run(bot) as env:
                context = await make_simcord_member_channel(env)
                member = context.member
                channel = context.channel

                await member.send(channel, "Ingest only.")

                assert await start_words_for_channel(bot.db, channel.id) == ["Ingest"]
                assert [message.content for message in channel.history()] == [
                    "Ingest only.",
                ]
        finally:
            bot.ws = None  # type: ignore[assignment]
            await bot.close()


class TestReplies:
    """Mentions produce replies through the real Discord message path."""

    async def test_mentioning_bot_sends_seeded_reply(
        self,
        simcord_env: Env,
        simcord_member_channel: SimcordMemberChannel,
        crabstero_bot: Crabstero,
        seed_reply: SeedReply,
    ) -> None:
        """A mention causes a deterministic Markov reply to be posted."""
        member = simcord_member_channel.member
        channel = simcord_member_channel.channel
        await seed_reply(crabstero_bot.db, channel.id)

        await member.send(channel, f"<@{simcord_env.backend.bot_user.id}> please reply")

        history = channel.history()
        assert [message.content for message in history] == [
            f"<@{simcord_env.backend.bot_user.id}> please reply",
            "Generated reply.",
        ]
        assert history[-1].reference is not None
        assert history[-1].author == simcord_env.bot.user

    async def test_mention_reply_can_include_embed_and_image(
        self,
        simcord_env: Env,
        simcord_member_channel: SimcordMemberChannel,
        crabstero_bot: Crabstero,
        monkeypatch: pytest.MonkeyPatch,
        seed_reply: SeedReply,
    ) -> None:
        """The optional reply embed path uses generated text and stored images."""
        member = simcord_member_channel.member
        channel = simcord_member_channel.channel
        await seed_reply(crabstero_bot.db, channel.id)
        await crabstero_bot.db.add_images(
            [ChannelImage(channel.id, 123, "https://example.com/reply.png")],
        )
        monkeypatch.setattr("crabstero.messages.secrets.randbelow", lambda _upper: 95)

        await member.send(channel, f"<@{simcord_env.backend.bot_user.id}> please reply")

        reply = channel.history()[-1]
        assert reply.content == "Generated reply."
        assert len(reply.embeds) == 1
        assert reply.embeds[0].title == "Generated reply."
        assert reply.embeds[0].description == "Generated reply."
        assert reply.embeds[0].image.url == "https://example.com/reply.png"

    async def test_mention_in_thread_uses_parent_channel_chain(
        self,
        simcord_env: Env,
        simcord_member_channel: SimcordMemberChannel,
        crabstero_bot: Crabstero,
        seed_reply: SeedReply,
    ) -> None:
        """A thread mention generates from the parent channel's Markov chain."""
        member = simcord_member_channel.member
        channel = simcord_member_channel.channel
        await seed_reply(crabstero_bot.db, channel.id)
        simcord_env.backend.create_thread(channel.id, "thread", member.id)
        await simcord_env.settle()
        thread = channel.threads[0]

        await member.send(thread, f"<@{simcord_env.backend.bot_user.id}> thread reply")

        assert [message.content for message in thread.history()] == [
            f"<@{simcord_env.backend.bot_user.id}> thread reply",
            "Generated reply.",
        ]

    async def test_allow_pings_flag_allows_generated_user_mentions(
        self,
        simcord_env: Env,
        simcord_member_channel: SimcordMemberChannel,
        crabstero_bot: Crabstero,
    ) -> None:
        """Generated mentions are allowed for users who opted in."""
        member = simcord_member_channel.member
        channel = simcord_member_channel.channel
        target = simcord_env.create_user("Mentioned")
        await crabstero_bot.db.add_markov_data(
            [StartWord(channel.id, 123, "Hello")],
            [Transition(channel.id, 123, "Hello", f"{target.mention}.")],
        )
        await crabstero_bot.db.set_flag(
            EntityType.USER,
            str(target.id),
            Flag.ALLOW_PINGS,
        )

        await member.send(channel, f"<@{simcord_env.backend.bot_user.id}> please reply")

        assert channel.history()[-1].content == f"Hello {target.mention}."

    async def test_no_reply_flag_suppresses_reply(
        self,
        simcord_env: Env,
        simcord_member_channel: SimcordMemberChannel,
        crabstero_bot: Crabstero,
        seed_reply: SeedReply,
    ) -> None:
        """The noReply flag prevents a mention response."""
        member = simcord_member_channel.member
        channel = simcord_member_channel.channel
        await seed_reply(crabstero_bot.db, channel.id)
        await crabstero_bot.db.set_flag(
            EntityType.CHANNEL,
            str(channel.id),
            Flag.NO_REPLY,
        )

        await member.send(channel, f"<@{simcord_env.backend.bot_user.id}> please reply")

        assert [message.content for message in channel.history()] == [
            f"<@{simcord_env.backend.bot_user.id}> please reply",
        ]

    async def test_missing_send_permission_suppresses_reply(
        self,
        simcord_env: Env,
        crabstero_bot: Crabstero,
        seed_reply: SeedReply,
    ) -> None:
        """A bot without send_messages permission does not reply."""
        guild = simcord_env.create_guild()
        bot_role = guild.roles[simcord_env.backend.bot_user.name]
        channel = guild.create_text_channel(
            "readonly",
            overwrites={
                bot_role: discord.PermissionOverwrite(send_messages=False),
            },
        )
        member = guild.add_member(simcord_env.create_user("Ada"))
        await simcord_env.settle()
        await seed_reply(crabstero_bot.db, channel.id)

        await member.send(channel, f"<@{simcord_env.backend.bot_user.id}> please reply")

        assert [message.content for message in channel.history()] == [
            f"<@{simcord_env.backend.bot_user.id}> please reply",
        ]

    async def test_direct_dm_reply_is_ignored(
        self,
        simcord_env: Env,
        crabstero_bot: Crabstero,
    ) -> None:
        """The reply helper ignores messages outside guilds."""
        user = simcord_env.create_user("Ada")
        message = await user.send_dm(f"<@{simcord_env.backend.bot_user.id}> hi")

        await reply_to_message(crabstero_bot.db, message)

        assert [message.content for message in user.dm_channel.history()] == [
            f"<@{simcord_env.backend.bot_user.id}> hi",
        ]


class TestDeletes:
    """Raw delete events reverse recent message ingestion."""

    async def test_delete_reverses_recent_ingest(
        self,
        simcord_member_channel: SimcordMemberChannel,
        crabstero_bot: Crabstero,
        start_words_for_channel: StartWordsForChannel,
    ) -> None:
        """Deleting a cached message removes its Markov rows."""
        member = simcord_member_channel.member
        channel = simcord_member_channel.channel
        message = await member.send(channel, "Delete me.")
        assert await start_words_for_channel(crabstero_bot.db, channel.id) == [
            "Delete",
        ]

        await member.delete(message)

        assert await start_words_for_channel(crabstero_bot.db, channel.id) == []

    async def test_bulk_delete_reverses_recent_ingests(
        self,
        simcord_env: Env,
        simcord_member_channel: SimcordMemberChannel,
        crabstero_bot: Crabstero,
        start_words_for_channel: StartWordsForChannel,
    ) -> None:
        """Bulk-deleting cached messages removes their Markov rows."""
        member = simcord_member_channel.member
        channel = simcord_member_channel.channel
        first = await member.send(channel, "Bulk one.")
        second = await member.send(channel, "Bulk two.")
        assert await start_words_for_channel(crabstero_bot.db, channel.id) == [
            "Bulk",
            "Bulk",
        ]

        simcord_env.backend.bulk_delete_messages(channel.id, [first.id, second.id])
        await simcord_env.settle()

        assert await start_words_for_channel(crabstero_bot.db, channel.id) == []


class TestFlagSuppression:
    """Message behavior respects persisted noIngest and noReply flags."""

    async def test_no_ingest_user_flag_suppresses_ingestion(
        self,
        simcord_member_channel: SimcordMemberChannel,
        crabstero_bot: Crabstero,
        start_words_for_channel: StartWordsForChannel,
    ) -> None:
        """The noIngest flag prevents storing a user's message."""
        member = simcord_member_channel.member
        channel = simcord_member_channel.channel
        await crabstero_bot.db.set_flag(
            EntityType.USER,
            str(member.id),
            Flag.NO_INGEST,
        )

        await member.send(channel, "Do not learn.")

        assert await start_words_for_channel(crabstero_bot.db, channel.id) == []

    async def test_direct_dm_ingest_is_ignored(
        self,
        simcord_env: Env,
        crabstero_bot: Crabstero,
        start_words_for_channel: StartWordsForChannel,
    ) -> None:
        """The ingest helper ignores messages outside guilds."""
        user = simcord_env.create_user("Ada")
        message = await user.send_dm("Direct helper call.")

        await ingest_message(crabstero_bot.db, message)

        assert (
            await start_words_for_channel(
                crabstero_bot.db,
                user.dm_channel.id,
            )
            == []
        )

    async def test_direct_bot_mention_ingest_is_ignored(
        self,
        simcord_env: Env,
        simcord_member_channel: SimcordMemberChannel,
        crabstero_bot: Crabstero,
        start_words_for_channel: StartWordsForChannel,
    ) -> None:
        """The ingest helper ignores messages that mention the bot."""
        member = simcord_member_channel.member
        channel = simcord_member_channel.channel
        message = await member.send(
            channel,
            f"<@{simcord_env.backend.bot_user.id}> do not learn",
        )

        await ingest_message(crabstero_bot.db, message)

        assert await start_words_for_channel(crabstero_bot.db, channel.id) == []
