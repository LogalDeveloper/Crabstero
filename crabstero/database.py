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

"""Async SQLite database access for Crabstero's persistent storage."""

from typing import Self

import aiosqlite

# SQL statements for creating the database schema.
_SCHEMA = """
-- Markov chain starting words.
-- Each row represents one occurrence. Duplicates represent frequency weight.
CREATE TABLE IF NOT EXISTS markov_start_words (
    channel_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    word TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_start_channel ON markov_start_words(channel_id, word);
CREATE INDEX IF NOT EXISTS idx_start_user ON markov_start_words(user_id);

-- Markov chain word transitions.
-- Each row represents one occurrence. Duplicates represent frequency weight.
CREATE TABLE IF NOT EXISTS markov_transitions (
    channel_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    word TEXT NOT NULL,
    next_word TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_transitions_channel_word
    ON markov_transitions(channel_id, word);
CREATE INDEX IF NOT EXISTS idx_transitions_user ON markov_transitions(user_id);

-- Image URLs per channel.
CREATE TABLE IF NOT EXISTS channel_images (
    channel_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    url TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_images_channel ON channel_images(channel_id);
CREATE INDEX IF NOT EXISTS idx_images_user ON channel_images(user_id);

-- Flags for channels, servers, and users.
CREATE TABLE IF NOT EXISTS flags (
    entity_type TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    flag_name TEXT NOT NULL,
    PRIMARY KEY (entity_type, entity_id, flag_name)
);

-- Tracks which channels have been bulk-ingested.
CREATE TABLE IF NOT EXISTS ingested_channels (
    channel_id INTEGER NOT NULL PRIMARY KEY
);
"""


class Database:
    """Manages all SQLite database operations for Crabstero.

    Uses aiosqlite for native async access. A single connection is held
    open for the lifetime of the bot process.
    """

    def __init__(self, connection: aiosqlite.Connection) -> None:
        """Initialize the Database wrapper with an already-opened aiosqlite connection.

        :param connection: An open aiosqlite connection.
        """
        self._connection = connection

    @classmethod
    async def connect(cls, path: str) -> Self:
        """Open a SQLite database, configure it, and create the schema.

        Configure the database for performance and create the schema if it
        does not already exist.

        :param path: The file path to the SQLite database.
        :return: A new Database instance ready for use.
        """
        connection = await aiosqlite.connect(path)

        # Set synchronous to NORMAL for a balance between safety and speed.
        await connection.execute("PRAGMA synchronous=NORMAL")

        # Create the schema tables and indexes if they do not already exist.
        # executescript auto-commits, so no explicit commit is needed.
        await connection.executescript(_SCHEMA)

        return cls(connection)

    async def close(self) -> None:
        """Close the database connection."""
        await self._connection.close()

    async def add_markov_data(
        self,
        start_words: list[tuple[int, int, str]],
        transitions: list[tuple[int, int, str, str]],
    ) -> None:
        """Insert Markov start words and transitions, then commit.

        Both inserts happen in a single transaction.

        :param start_words: A list of (channel_id, user_id, word) tuples.
        :param transitions: A list of (channel_id, user_id, word, next_word) tuples.
        """
        if start_words:
            await self._connection.executemany(
                "INSERT INTO markov_start_words"
                " (channel_id, user_id, word)"
                " VALUES (?, ?, ?)",
                start_words,
            )
        if transitions:
            await self._connection.executemany(
                "INSERT INTO markov_transitions"
                " (channel_id, user_id, word, next_word)"
                " VALUES (?, ?, ?, ?)",
                transitions,
            )
        if start_words or transitions:
            await self._connection.commit()

    async def get_random_start_word(self, channel_id: int) -> str | None:
        """Return a random starting word for a channel.

        Weighted by occurrence frequency.

        :param channel_id: The Discord channel ID.
        :return: A random starting word, or None if none exist.
        """
        async with self._connection.execute(
            "SELECT word FROM markov_start_words"
            " WHERE channel_id = ?"
            " ORDER BY RANDOM() LIMIT 1",
            (channel_id,),
        ) as cursor:
            row = await cursor.fetchone()
            return row[0] if row else None

    async def get_random_next_word(self, channel_id: int, word: str) -> str | None:
        """Return a random next word for a given word in a channel.

        Weighted by occurrence frequency.

        :param channel_id: The Discord channel ID.
        :param word: The current word to find a transition for.
        :return: A random next word, or None if no transitions exist.
        """
        async with self._connection.execute(
            "SELECT next_word FROM markov_transitions"
            " WHERE channel_id = ? AND word = ?"
            " ORDER BY RANDOM() LIMIT 1",
            (channel_id, word),
        ) as cursor:
            row = await cursor.fetchone()
            return row[0] if row else None

    async def get_random_completing_next_word(
        self, channel_id: int, word: str
    ) -> str | None:
        """Return a random sentence-ending next word for a given word in a channel.

        Weighted by occurrence frequency. A completing word is one whose last
        character is '.', '!', '?', or '§'.

        :param channel_id: The Discord channel ID.
        :param word: The current word to find a completing transition for.
        :return: A random completing next word, or None if none exist.
        """
        async with self._connection.execute(
            "SELECT next_word FROM markov_transitions"
            " WHERE channel_id = ? AND word = ?"
            " AND SUBSTR(next_word, -1, 1)"
            " IN ('.', '!', '?', '§')"
            " ORDER BY RANDOM() LIMIT 1",
            (channel_id, word),
        ) as cursor:
            row = await cursor.fetchone()
            return row[0] if row else None

    async def add_image(self, channel_id: int, user_id: int, url: str) -> None:
        """Store an image URL for a given channel.

        :param channel_id: The Discord channel ID.
        :param user_id: The Discord user ID of the contributor.
        :param url: The image URL to store.
        """
        await self._connection.execute(
            "INSERT INTO channel_images (channel_id, user_id, url) VALUES (?, ?, ?)",
            (channel_id, user_id, url),
        )
        await self._connection.commit()

    async def get_random_image(self, channel_id: int) -> str | None:
        """Return a random image URL for a given channel.

        :param channel_id: The Discord channel ID.
        :return: A random image URL, or None if none exist.
        """
        async with self._connection.execute(
            "SELECT url FROM channel_images"
            " WHERE channel_id = ?"
            " ORDER BY RANDOM() LIMIT 1",
            (channel_id,),
        ) as cursor:
            row = await cursor.fetchone()
            return row[0] if row else None

    async def set_flag(self, entity_type: str, entity_id: str, flag_name: str) -> None:
        """Set a flag on a given entity. If the flag is already set, this is a no-op.

        :param entity_type: The entity type ("channel", "server", "user").
        :param entity_id: The Discord ID of the entity.
        :param flag_name: The name of the flag to set.
        """
        await self._connection.execute(
            "INSERT OR IGNORE INTO flags"
            " (entity_type, entity_id, flag_name)"
            " VALUES (?, ?, ?)",
            (entity_type, entity_id, flag_name),
        )
        await self._connection.commit()

    async def clear_flag(
        self, entity_type: str, entity_id: str, flag_name: str
    ) -> None:
        """Clear a flag on a given entity. If the flag is not set, this is a no-op.

        :param entity_type: The entity type ("channel", "server", "user").
        :param entity_id: The Discord ID of the entity.
        :param flag_name: The name of the flag to clear.
        """
        await self._connection.execute(
            "DELETE FROM flags"
            " WHERE entity_type = ?"
            " AND entity_id = ?"
            " AND flag_name = ?",
            (entity_type, entity_id, flag_name),
        )
        await self._connection.commit()

    async def is_flag_set(
        self, entity_type: str, entity_id: str, flag_name: str
    ) -> bool:
        """Check whether a flag is set on a given entity.

        :param entity_type: The entity type ("channel", "server", "user").
        :param entity_id: The Discord ID of the entity.
        :param flag_name: The name of the flag to check.
        :return: True if the flag is set, False otherwise.
        """
        async with self._connection.execute(
            "SELECT 1 FROM flags"
            " WHERE entity_type = ?"
            " AND entity_id = ?"
            " AND flag_name = ?",
            (entity_type, entity_id, flag_name),
        ) as cursor:
            return await cursor.fetchone() is not None

    async def is_channel_ingested(self, channel_id: int) -> bool:
        """Check whether a channel has already been bulk-ingested.

        :param channel_id: The Discord channel ID.
        :return: True if the channel has been ingested, False otherwise.
        """
        async with self._connection.execute(
            "SELECT 1 FROM ingested_channels WHERE channel_id = ?",
            (channel_id,),
        ) as cursor:
            return await cursor.fetchone() is not None

    async def mark_channel_ingested(self, channel_id: int) -> None:
        """Mark a channel as having been bulk-ingested.

        :param channel_id: The Discord channel ID.
        """
        await self._connection.execute(
            "INSERT OR IGNORE INTO ingested_channels (channel_id) VALUES (?)",
            (channel_id,),
        )
        await self._connection.commit()
