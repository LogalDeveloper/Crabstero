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

"""Provides async convenience wrappers around the database flag methods.

Accepts discord.py objects or raw integer IDs and translates them into the
entity_type/entity_id pairs used by the database layer.
"""

import enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import discord

    from crabstero.database import Database


class Flag(enum.Enum):
    """Enum of all supported flag names."""

    NO_REPLY = "noReply"
    NO_INGEST = "noIngest"
    ALLOW_PINGS = "allowPings"


class EntityType(enum.Enum):
    """Enum of entity types that can have flags."""

    CHANNEL = "channel"
    SERVER = "server"
    USER = "user"


def _entity_id(entity: discord.abc.Snowflake | int) -> str:
    """Extract a string entity ID from a Discord object or raw integer ID.

    :param entity: A Discord entity or raw integer ID.
    :return: The entity ID as a string.
    """
    return str(entity) if isinstance(entity, int) else str(entity.id)


async def set_flag(
    db: Database,
    entity: discord.abc.Snowflake | int,
    entity_type: EntityType,
    flag: Flag,
) -> None:
    """Set a flag on a given entity.

    :param db: The database instance.
    :param entity: The Discord entity or raw integer ID to set the flag on.
    :param entity_type: The type of the entity.
    :param flag: The flag to set.
    """
    await db.set_flag(entity_type.value, _entity_id(entity), flag.value)


async def clear_flag(
    db: Database,
    entity: discord.abc.Snowflake | int,
    entity_type: EntityType,
    flag: Flag,
) -> None:
    """Clear a flag on a given entity.

    :param db: The database instance.
    :param entity: The Discord entity or raw integer ID to clear the flag on.
    :param entity_type: The type of the entity.
    :param flag: The flag to clear.
    """
    await db.clear_flag(entity_type.value, _entity_id(entity), flag.value)


async def is_flag_set(
    db: Database,
    entity: discord.abc.Snowflake | int,
    entity_type: EntityType,
    flag: Flag,
) -> bool:
    """Check whether a flag is set on a given entity.

    :param db: The database instance.
    :param entity: The Discord entity or raw integer ID to check.
    :param entity_type: The type of the entity.
    :param flag: The flag to check for.
    :return: True if the flag is set, False otherwise.
    """
    return await db.is_flag_set(entity_type.value, _entity_id(entity), flag.value)
