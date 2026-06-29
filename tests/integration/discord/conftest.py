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

"""Fixtures used only by Simcord-backed Discord integration tests."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

import pytest

from crabstero.bot import Crabstero
from crabstero.database import Database

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator
    from pathlib import Path

    from simcord import ChannelHandle, Env, GuildHandle, MemberActor

type StartWordsForChannel = Callable[[Database, int], Awaitable[list[str]]]
type MakeSimcordTextChannel = Callable[["Env"], Awaitable["SimcordTextChannel"]]
type MakeSimcordMemberChannel = Callable[["Env"], Awaitable["SimcordMemberChannel"]]


@dataclass(frozen=True, slots=True)
class SimcordTextChannel:
    """Guild and text channel created inside a running Simcord environment."""

    guild: GuildHandle
    channel: ChannelHandle


@dataclass(frozen=True, slots=True)
class SimcordMemberChannel:
    """Guild, text channel, and human member for Discord flow tests."""

    guild: GuildHandle
    member: MemberActor
    channel: ChannelHandle


@pytest.fixture
async def crabstero_bot(tmp_path: Path) -> AsyncGenerator[Crabstero]:
    """Yield the Crabstero bot instance inspected by Discord integration tests."""
    bot = Crabstero(str(tmp_path / "crabstero.db"))
    try:
        yield bot
    finally:
        bot.ws = None  # type: ignore[assignment]
        await bot.close()


@pytest.fixture
def simcord_bot(crabstero_bot: Crabstero) -> Crabstero:
    """Expose Crabstero under the fixture name required by Simcord."""
    return crabstero_bot


@pytest.fixture
def make_simcord_text_channel() -> MakeSimcordTextChannel:
    """Return a factory for guild text channels in any Simcord environment."""

    async def make(env: Env) -> SimcordTextChannel:
        guild = env.create_guild()
        await env.settle()
        channel = guild.create_text_channel("general")
        await env.settle()
        return SimcordTextChannel(guild, channel)

    return make


@pytest.fixture
async def simcord_text_channel(
    simcord_env: Env,
    make_simcord_text_channel: MakeSimcordTextChannel,
) -> SimcordTextChannel:
    """Create one guild text channel in the default Simcord environment."""
    return await make_simcord_text_channel(simcord_env)


@pytest.fixture
def make_simcord_member_channel() -> MakeSimcordMemberChannel:
    """Return a factory for guild/member/channel triples in any Simcord env."""

    async def make(env: Env) -> SimcordMemberChannel:
        guild = env.create_guild()
        await env.settle()
        channel = guild.create_text_channel("general")
        member = guild.add_member(env.create_user("Ada"))
        await env.settle()
        return SimcordMemberChannel(guild, member, channel)

    return make


@pytest.fixture
async def simcord_member_channel(
    simcord_env: Env,
    make_simcord_member_channel: MakeSimcordMemberChannel,
) -> SimcordMemberChannel:
    """Create one guild text channel and human member in the default Simcord env."""
    return await make_simcord_member_channel(simcord_env)


@pytest.fixture
def start_words_for_channel() -> StartWordsForChannel:
    """Return persisted Markov start-word occurrences for one Discord channel."""

    async def read(db: Database, channel_id: int) -> list[str]:
        async with db._connection.execute(
            "SELECT word, occurrences"
            " FROM markov_start_words"
            " WHERE channel_id = ?"
            " ORDER BY word",
            (channel_id,),
        ) as cursor:
            rows = await cursor.fetchall()
        return [str(row[0]) for row in rows for _ in range(int(row[1]))]

    return read
