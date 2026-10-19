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

"""Simcord integration tests for Crabstero slash commands."""

from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING

import pytest

from crabstero.database import ChannelImage, StartWord, Transition
from crabstero.flags import Flag

if TYPE_CHECKING:
    from simcord import Env

    from crabstero.bot import Crabstero
    from crabstero.database import Database
    from tests.integration.discord.conftest import SimcordMemberChannel

type SeedForgetmeData = Callable[["Database", int], Awaitable[None]]
type ForgetmeRowsForUser = Callable[["Database", int], Awaitable[tuple[int, int, int]]]


@pytest.fixture
def seed_forgetme_data() -> SeedForgetmeData:
    """Return a seeder for user-owned and unrelated /forgetme database rows."""

    async def seed(db: Database, user_id: int) -> None:
        await db.add_markov_data(
            [
                StartWord(10, user_id, "delete"),
                StartWord(10, 999, "keep"),
            ],
            [
                Transition(10, user_id, "delete", "me."),
                Transition(10, 999, "keep", "me."),
            ],
        )
        await db.add_images(
            [
                ChannelImage(10, user_id, "https://example.com/delete.png"),
                ChannelImage(10, 999, "https://example.com/keep.png"),
            ],
        )

    return seed


@pytest.fixture
def forgetme_rows_for_user() -> ForgetmeRowsForUser:
    """Return a reader for Markov/image row counts owned by one user."""

    async def read(db: Database, user_id: int) -> tuple[int, int, int]:
        async with db._connection.execute(
            "SELECT COUNT(*) FROM markov_start_words WHERE user_id = ?",
            (user_id,),
        ) as cursor:
            row = await cursor.fetchone()
            assert row is not None
            start_words = row[0]
        async with db._connection.execute(
            "SELECT COUNT(*) FROM markov_transitions WHERE user_id = ?",
            (user_id,),
        ) as cursor:
            row = await cursor.fetchone()
            assert row is not None
            transitions = row[0]
        async with db._connection.execute(
            "SELECT COUNT(*) FROM channel_images WHERE user_id = ?",
            (user_id,),
        ) as cursor:
            row = await cursor.fetchone()
            assert row is not None
            images = row[0]
        return int(start_words), int(transitions), int(images)

    return read


class TestPingMe:
    """The /pingme command toggles persisted user opt-in state."""

    async def test_first_call_opts_in(
        self,
        simcord_member_channel: SimcordMemberChannel,
        crabstero_bot: Crabstero,
    ) -> None:
        """The first /pingme call sets allowPings and responds ephemerally."""
        member = simcord_member_channel.member
        channel = simcord_member_channel.channel

        result = await member.slash(channel, "pingme")

        assert result.response is not None
        assert result.response.ephemeral is True
        assert "I will now ping you" in result.response.content
        assert await crabstero_bot.db.is_flag_set(
            "user",
            str(member.id),
            Flag.ALLOW_PINGS,
        )

    async def test_second_call_opts_out(
        self,
        simcord_env: Env,
        simcord_member_channel: SimcordMemberChannel,
        crabstero_bot: Crabstero,
    ) -> None:
        """The second /pingme call clears allowPings and responds ephemerally."""
        member = simcord_member_channel.member
        channel = simcord_member_channel.channel
        await member.slash(channel, "pingme")

        result = await member.slash(channel, "pingme")
        await simcord_env.settle(idle=1)

        assert result.response is not None
        assert result.response.ephemeral is True
        assert "I will no longer ping you" in result.response.content
        assert not await crabstero_bot.db.is_flag_set(
            "user",
            str(member.id),
            Flag.ALLOW_PINGS,
        )


class TestForgetMe:
    """The /forgetme command confirms, cancels, and times out hermetically."""

    async def test_initial_response_has_confirmation_buttons(
        self,
        simcord_member_channel: SimcordMemberChannel,
    ) -> None:
        """The first /forgetme response is ephemeral and asks for confirmation."""
        member = simcord_member_channel.member
        channel = simcord_member_channel.channel

        result = await member.slash(channel, "forgetme")

        assert result.response is not None
        assert result.response.ephemeral is True
        assert "Would you like to proceed?" in result.response.content
        labels = [
            component["label"]
            for row in result.response.components
            for component in row["components"]
        ]
        assert labels == ["Confirm", "Cancel"]

    async def test_confirm_deletes_user_data_and_sets_no_ingest(
        self,
        simcord_member_channel: SimcordMemberChannel,
        crabstero_bot: Crabstero,
        seed_forgetme_data: SeedForgetmeData,
        forgetme_rows_for_user: ForgetmeRowsForUser,
    ) -> None:
        """Confirming /forgetme deletes user data and persists noIngest."""
        member = simcord_member_channel.member
        channel = simcord_member_channel.channel
        await seed_forgetme_data(crabstero_bot.db, member.id)
        await crabstero_bot.db.set_flag("user", str(member.id), Flag.ALLOW_PINGS)
        await crabstero_bot.db.set_flag("user", str(member.id), Flag.NO_REPLY)
        prompt = (await member.slash(channel, "forgetme")).response
        assert prompt is not None

        result = await member.click(prompt, label="Confirm")

        assert result.response is not None
        assert result.response.ephemeral is True
        assert "I have deleted your data" in result.response.content
        assert result.response.components == []
        assert await forgetme_rows_for_user(crabstero_bot.db, member.id) == (0, 0, 0)
        assert await forgetme_rows_for_user(crabstero_bot.db, 999) == (1, 1, 1)
        assert await crabstero_bot.db.is_flag_set(
            "user",
            str(member.id),
            Flag.NO_INGEST,
        )
        assert not await crabstero_bot.db.is_flag_set(
            "user",
            str(member.id),
            Flag.ALLOW_PINGS,
        )
        assert not await crabstero_bot.db.is_flag_set(
            "user",
            str(member.id),
            Flag.NO_REPLY,
        )

    async def test_cancel_leaves_user_data_and_flags(
        self,
        simcord_member_channel: SimcordMemberChannel,
        crabstero_bot: Crabstero,
        seed_forgetme_data: SeedForgetmeData,
        forgetme_rows_for_user: ForgetmeRowsForUser,
    ) -> None:
        """Cancelling /forgetme leaves data and user flags unchanged."""
        member = simcord_member_channel.member
        channel = simcord_member_channel.channel
        await seed_forgetme_data(crabstero_bot.db, member.id)
        await crabstero_bot.db.set_flag("user", str(member.id), Flag.ALLOW_PINGS)
        await crabstero_bot.db.set_flag("user", str(member.id), Flag.NO_REPLY)
        prompt = (await member.slash(channel, "forgetme")).response
        assert prompt is not None

        result = await member.click(prompt, label="Cancel")

        assert result.response is not None
        assert result.response.ephemeral is True
        assert result.response.content == (
            "Action cancelled. I have not modified your data."
        )
        assert result.response.components == []
        assert await forgetme_rows_for_user(crabstero_bot.db, member.id) == (1, 1, 1)
        assert await crabstero_bot.db.is_flag_set(
            "user",
            str(member.id),
            Flag.ALLOW_PINGS,
        )
        assert await crabstero_bot.db.is_flag_set(
            "user",
            str(member.id),
            Flag.NO_REPLY,
        )
        assert not await crabstero_bot.db.is_flag_set(
            "user",
            str(member.id),
            Flag.NO_INGEST,
        )

    async def test_already_forgotten_user_gets_terminal_response(
        self,
        simcord_member_channel: SimcordMemberChannel,
        crabstero_bot: Crabstero,
    ) -> None:
        """A noIngest user does not get another confirmation view."""
        member = simcord_member_channel.member
        channel = simcord_member_channel.channel
        await crabstero_bot.db.set_flag("user", str(member.id), Flag.NO_INGEST)

        result = await member.slash(channel, "forgetme")

        assert result.response is not None
        assert result.response.ephemeral is True
        assert result.response.content == (
            "I have already removed your data and I am not using your messages."
        )
        assert result.response.components == []

    async def test_confirmation_timeout_removes_view(
        self,
        simcord_env: Env,
        simcord_member_channel: SimcordMemberChannel,
    ) -> None:
        """The /forgetme view timeout edits the original response without sleeping."""
        member = simcord_member_channel.member
        channel = simcord_member_channel.channel
        prompt = (await member.slash(channel, "forgetme")).response
        assert prompt is not None

        await simcord_env.advance_time(181)

        assert prompt.content == (
            "This timed out. Run `/forgetme` again if you still want to."
        )
        assert prompt.components == []
