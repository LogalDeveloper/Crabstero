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

"""Unit tests for the flag convenience wrappers and enums.

Tests cover _entity_id, Flag and EntityType enums, and the high-level
set_flag/clear_flag/is_flag_set wrappers from crabstero.flags.
"""

from typing import TYPE_CHECKING

import pytest

from crabstero.flags import (
    EntityType,
    Flag,
    _entity_id,
    clear_flag,
    is_flag_set,
    set_flag,
)

if TYPE_CHECKING:
    from crabstero.database import Database


class _StubSnowflake:
    """Minimal stand-in for discord.abc.Snowflake."""

    def __init__(self, *, entity_id: int = 123456789) -> None:
        self.id = entity_id


class TestEntityId:
    """Entity ID extraction from Discord objects and integers."""

    @pytest.mark.parametrize(
        ("entity", "expected"),
        [
            pytest.param(42, "42", id="raw-integer"),
            pytest.param(
                _StubSnowflake(entity_id=99),
                "99",
                id="snowflake-object",
            ),
        ],
    )
    def test_extraction(self, entity: object, expected: str) -> None:
        """Converts the entity to the expected string ID."""
        assert _entity_id(entity) == expected  # type: ignore[arg-type]


class TestFlagEnums:
    """Flag and EntityType enum values."""

    @pytest.mark.parametrize(
        ("member", "expected"),
        [
            pytest.param(Flag.NO_REPLY, "noReply", id="no-reply"),
            pytest.param(Flag.NO_INGEST, "noIngest", id="no-ingest"),
            pytest.param(Flag.ALLOW_PINGS, "allowPings", id="allow-pings"),
        ],
    )
    def test_flag_values(self, member: Flag, expected: str) -> None:
        """Flag enum value matches the expected database string."""
        assert member.value == expected

    @pytest.mark.parametrize(
        ("member", "expected"),
        [
            pytest.param(EntityType.CHANNEL, "channel", id="channel"),
            pytest.param(EntityType.SERVER, "server", id="server"),
            pytest.param(EntityType.USER, "user", id="user"),
        ],
    )
    def test_entity_type_values(self, member: EntityType, expected: str) -> None:
        """EntityType enum value matches the expected database string."""
        assert member.value == expected


class TestSetClearCheck:
    """High-level flag set/clear/check cycle through the flags module."""

    @pytest.mark.parametrize(
        "flag",
        [
            pytest.param(Flag.NO_REPLY, id="no-reply"),
            pytest.param(Flag.NO_INGEST, id="no-ingest"),
            pytest.param(Flag.ALLOW_PINGS, id="allow-pings"),
        ],
    )
    async def test_set_then_check(self, db: Database, flag: Flag) -> None:
        """A set flag is reported as set."""
        await set_flag(db, 1, EntityType.CHANNEL, flag)
        await db.commit()
        assert await is_flag_set(db, 1, EntityType.CHANNEL, flag) is True

    async def test_unset_returns_false(self, db: Database) -> None:
        """An unset flag is reported as not set."""
        assert await is_flag_set(db, 1, EntityType.CHANNEL, Flag.NO_REPLY) is False

    async def test_clear_removes_flag(self, db: Database) -> None:
        """A cleared flag is no longer reported as set."""
        await set_flag(db, 1, EntityType.CHANNEL, Flag.NO_REPLY)
        await db.commit()
        await clear_flag(db, 1, EntityType.CHANNEL, Flag.NO_REPLY)
        await db.commit()
        assert await is_flag_set(db, 1, EntityType.CHANNEL, Flag.NO_REPLY) is False
