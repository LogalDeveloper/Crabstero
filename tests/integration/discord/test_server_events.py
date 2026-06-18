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

"""Simcord integration tests for server event ingestion triggers."""

from typing import TYPE_CHECKING

import discord
import pytest
from simcord.backend.models import Overwrite
from simcord.enums import OverwriteType

from crabstero.listeners.server_events import ServerEventsCog
from crabstero.tasks.ingestion import ingest_channel

if TYPE_CHECKING:
    from simcord import Env

    from crabstero.bot import Crabstero
    from tests.integration.discord.conftest import (
        SimcordMemberChannel,
        SimcordTextChannel,
        StartWordsForChannel,
    )


@pytest.fixture
def server_events_cog(crabstero_bot: Crabstero) -> ServerEventsCog:
    """Return the loaded server-events cog from the Simcord-backed bot."""
    cog = crabstero_bot.get_cog("ServerEventsCog")
    assert isinstance(cog, ServerEventsCog)
    return cog


class TestGuildEvents:
    """Guild availability and joins queue local channel history ingestion."""

    async def test_guild_available_queues_text_and_voice_channels(
        self,
        simcord_env: Env,
        server_events_cog: ServerEventsCog,
    ) -> None:
        """Available guilds enqueue all textable channels."""
        guild = simcord_env.create_guild()
        await simcord_env.settle()
        text = guild.create_text_channel("general")
        voice = guild.create_voice_channel("voice")
        await simcord_env.settle()
        cached_guild = simcord_env.bot.get_guild(guild.id)
        assert cached_guild is not None

        await server_events_cog.on_guild_available(cached_guild)
        await simcord_env.settle()

        assert await server_events_cog.bot.db.is_channel_ingested(text.id)
        assert await server_events_cog.bot.db.is_channel_ingested(voice.id)

    async def test_guild_join_queues_ingestion_and_attempts_owner_notification(
        self,
        simcord_env: Env,
        simcord_text_channel: SimcordTextChannel,
        server_events_cog: ServerEventsCog,
    ) -> None:
        """Joining a guild enqueues ingestion and uses the fake owner DM path."""
        guild = simcord_text_channel.guild
        channel = simcord_text_channel.channel
        cached_guild = simcord_env.bot.get_guild(guild.id)
        assert cached_guild is not None

        await server_events_cog.on_guild_join(cached_guild)
        await simcord_env.settle()

        assert await server_events_cog.bot.db.is_channel_ingested(channel.id)
        http_routes = [f"{method} {path}" for method, path, _ in simcord_env.http_log]
        assert "GET /oauth2/applications/@me" in http_routes
        assert "POST /users/@me/channels" in http_routes
        assert any(route.endswith("/messages") for route in http_routes)


class TestPermissionUpdateEvents:
    """Permission-changing events trigger ingestion only when relevant."""

    async def test_role_update_queues_only_when_permissions_change_for_bot_role(
        self,
        simcord_env: Env,
        simcord_text_channel: SimcordTextChannel,
        crabstero_bot: Crabstero,
    ) -> None:
        """Role updates require changed permissions and bot membership."""
        guild = simcord_text_channel.guild
        channel = simcord_text_channel.channel
        role = guild.create_role("reader", permissions=discord.Permissions.none())
        await simcord_env.settle()

        simcord_env.backend.edit_role(
            guild.id,
            role.id,
            {"permissions": discord.Permissions(view_channel=True).value},
        )
        await simcord_env.settle()
        assert not await crabstero_bot.db.is_channel_ingested(channel.id)

        bot_role = guild.roles[simcord_env.backend.bot_user.name]
        simcord_env.backend.edit_role(
            guild.id,
            bot_role.id,
            {
                "permissions": discord.Permissions(
                    view_channel=True,
                    read_message_history=True,
                ).value,
            },
        )
        await simcord_env.settle()

        assert await crabstero_bot.db.is_channel_ingested(channel.id)

    async def test_role_update_same_permissions_does_not_queue(
        self,
        simcord_env: Env,
        simcord_text_channel: SimcordTextChannel,
        crabstero_bot: Crabstero,
    ) -> None:
        """A role update without permission changes is ignored."""
        guild = simcord_text_channel.guild
        channel = simcord_text_channel.channel
        role = guild.create_role(
            "reader",
            permissions=discord.Permissions(read_message_history=True),
        )
        simcord_env.backend.add_member_role(
            guild.id,
            simcord_env.backend.bot_user.id,
            role.id,
        )
        await simcord_env.settle()

        simcord_env.backend.edit_role(
            guild.id,
            role.id,
            {"permissions": discord.Permissions(read_message_history=True).value},
        )
        await simcord_env.settle()

        assert not await crabstero_bot.db.is_channel_ingested(channel.id)

    async def test_channel_update_queues_only_when_overwrites_change(
        self,
        simcord_env: Env,
        simcord_text_channel: SimcordTextChannel,
        crabstero_bot: Crabstero,
    ) -> None:
        """Channel updates without overwrite changes do not enqueue ingestion."""
        guild = simcord_text_channel.guild
        channel = simcord_text_channel.channel

        simcord_env.backend.edit_channel(channel.id, {"topic": "no permission change"})
        await simcord_env.settle()
        assert not await crabstero_bot.db.is_channel_ingested(channel.id)

        simcord_env.backend.set_overwrite(
            channel.id,
            Overwrite(
                target_id=guild.default_role.id,
                type=OverwriteType.ROLE,
                allow=discord.Permissions(read_message_history=True).value,
                deny=0,
            ),
        )
        await simcord_env.settle()

        assert await crabstero_bot.db.is_channel_ingested(channel.id)


class TestIngestionTasks:
    """Server-triggered ingestion task behavior stays local and deterministic."""

    async def test_duplicate_channel_queue_requests_share_one_task(
        self,
        simcord_env: Env,
        simcord_text_channel: SimcordTextChannel,
        crabstero_bot: Crabstero,
    ) -> None:
        """Queuing the same channel twice before the loop runs creates one task."""
        channel = simcord_text_channel.channel
        cached_channel = simcord_env.bot.get_channel(channel.id)
        assert isinstance(cached_channel, discord.TextChannel)

        crabstero_bot.queue_channel_for_ingestion(cached_channel)
        crabstero_bot.queue_channel_for_ingestion(cached_channel)

        assert list(crabstero_bot._ingestion_tasks) == [channel.id]
        await simcord_env.settle()

    async def test_channel_history_ingestion_requires_read_history_permission(
        self,
        simcord_env: Env,
        crabstero_bot: Crabstero,
    ) -> None:
        """A channel missing read history permission is skipped."""
        guild = simcord_env.create_guild()
        channel = guild.create_text_channel(
            "hidden-history",
            overwrites={
                guild.default_role: discord.PermissionOverwrite(
                    read_message_history=False,
                ),
            },
        )
        await simcord_env.settle()
        cached_channel = simcord_env.bot.get_channel(channel.id)
        assert isinstance(cached_channel, discord.TextChannel)

        await ingest_channel(cached_channel, crabstero_bot.db)

        assert not await crabstero_bot.db.is_channel_ingested(channel.id)

    async def test_channel_history_ingestion_reads_existing_messages(
        self,
        simcord_env: Env,
        simcord_member_channel: SimcordMemberChannel,
        crabstero_bot: Crabstero,
        start_words_for_channel: StartWordsForChannel,
    ) -> None:
        """Bulk channel ingestion reads historical messages through Discord."""
        member = simcord_member_channel.member
        channel = simcord_member_channel.channel
        cached_channel = simcord_env.bot.get_channel(channel.id)
        assert isinstance(cached_channel, discord.TextChannel)
        simcord_env.backend.create_message(
            channel.id,
            member.id,
            "Historical message.",
            broadcast=False,
        )

        await ingest_channel(cached_channel, crabstero_bot.db)

        assert await start_words_for_channel(crabstero_bot.db, channel.id) == [
            "Historical",
        ]
        assert await crabstero_bot.db.is_channel_ingested(channel.id)

    async def test_already_ingested_channel_is_skipped(
        self,
        simcord_env: Env,
        simcord_member_channel: SimcordMemberChannel,
        crabstero_bot: Crabstero,
        start_words_for_channel: StartWordsForChannel,
    ) -> None:
        """A channel marked ingested is not read again."""
        member = simcord_member_channel.member
        channel = simcord_member_channel.channel
        await crabstero_bot.db.mark_channel_ingested(channel.id)
        cached_channel = simcord_env.bot.get_channel(channel.id)
        assert isinstance(cached_channel, discord.TextChannel)
        simcord_env.backend.create_message(
            channel.id,
            member.id,
            "Historical message.",
            broadcast=False,
        )

        await ingest_channel(cached_channel, crabstero_bot.db)

        assert await start_words_for_channel(crabstero_bot.db, channel.id) == []
