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
Provides async SQLite database access for all of Crabstero's persistent storage needs.

Uses aiosqlite for native async access. All methods are async def.
"""

import asyncio
import contextlib
import logging
from typing import Self

import aiosqlite

logger = logging.getLogger(__name__)

AUTO_COMMIT_WRITE_THRESHOLD = 100  # Commit after this many DB write operations.
AUTO_COMMIT_TIMEOUT_SECONDS = (
    60.0  # Commit after this many seconds since first uncommitted write.
)

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
CREATE INDEX IF NOT EXISTS idx_transitions_channel_word ON markov_transitions(channel_id, word);
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
    """
    Manages all SQLite database operations for Crabstero.

    Uses aiosqlite for native async access. A single connection is held open for the lifetime of
    the bot process with WAL mode enabled for concurrent read performance.
    """

    def __init__(self, connection: aiosqlite.Connection) -> None:
        """
        Initializes the Database wrapper with an already-opened aiosqlite connection.

        :param connection: An open aiosqlite connection.
        """
        self._connection = connection
        self._pending_writes = 0
        self._flush_task: asyncio.Task[None] | None = None

    @classmethod
    async def connect(cls, path: str) -> Self:
        """
        Opens a new SQLite database at the given path, configures it for performance, and creates
        the schema if it does not already exist.

        :param path: The file path to the SQLite database.
        :return: A new Database instance ready for use.
        """
        connection = await aiosqlite.connect(path)

        # Enable WAL mode for better concurrent read performance.
        await connection.execute("PRAGMA journal_mode=WAL")

        # Set synchronous to NORMAL for a balance between safety and speed.
        await connection.execute("PRAGMA synchronous=NORMAL")

        # Create the schema tables and indexes if they do not already exist.
        # executescript auto-commits, so no explicit commit is needed.
        await connection.executescript(_SCHEMA)

        return cls(connection)

    async def commit(self) -> None:
        """Commits pending writes and resets the flush timer."""
        await self._connection.commit()
        self._pending_writes = 0
        if self._flush_task is not None:
            self._flush_task.cancel()
            self._flush_task = None

    async def close(self) -> None:
        """Cancels the flush timer, commits any pending writes, then closes the database connection."""
        if self._flush_task is not None:
            self._flush_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._flush_task
            self._flush_task = None
        if self._pending_writes > 0:
            await self._connection.commit()
            self._pending_writes = 0
        await self._connection.close()

    async def add_start_words_batch(self, rows: list[tuple[int, int, str]]) -> None:
        """
        Inserts a batch of starting words into the markov_start_words table.

        :param rows: A list of (channel_id, user_id, word) tuples to insert.
        """
        await self._connection.executemany(
            "INSERT INTO markov_start_words (channel_id, user_id, word) VALUES (?, ?, ?)",
            rows,
        )
        await self._maybe_commit()

    async def get_random_start_word(self, channel_id: int) -> str | None:
        """
        Returns a random starting word for a given channel, weighted by occurrence frequency.

        :param channel_id: The Discord channel ID.
        :return: A random starting word, or None if no starting words exist for this channel.
        """
        async with self._connection.execute(
            "SELECT word FROM markov_start_words WHERE channel_id = ? ORDER BY RANDOM() LIMIT 1",
            (channel_id,),
        ) as cursor:
            row = await cursor.fetchone()
            return row[0] if row else None

    async def add_transitions_batch(
        self, rows: list[tuple[int, int, str, str]]
    ) -> None:
        """
        Inserts a batch of word transitions into the markov_transitions table.

        :param rows: A list of (channel_id, user_id, word, next_word) tuples to insert.
        """
        await self._connection.executemany(
            "INSERT INTO markov_transitions (channel_id, user_id, word, next_word) VALUES (?, ?, ?, ?)",
            rows,
        )
        await self._maybe_commit()

    async def get_random_next_word(self, channel_id: int, word: str) -> str | None:
        """
        Returns a random next word for a given word in a given channel, weighted by occurrence
        frequency.

        :param channel_id: The Discord channel ID.
        :param word: The current word to find a transition for.
        :return: A random next word, or None if no transitions exist.
        """
        async with self._connection.execute(
            "SELECT next_word FROM markov_transitions WHERE channel_id = ? AND word = ? ORDER BY RANDOM() LIMIT 1",
            (channel_id, word),
        ) as cursor:
            row = await cursor.fetchone()
            return row[0] if row else None

    async def get_random_completing_next_word(
        self, channel_id: int, word: str
    ) -> str | None:
        """
        Returns a random next word that ends a sentence for a given word in a given channel,
        weighted by occurrence frequency.

        A completing word is one whose last character is '.', '!', '?', or '§'.

        :param channel_id: The Discord channel ID.
        :param word: The current word to find a completing transition for.
        :return: A random completing next word, or None if no completing transitions exist.
        """
        async with self._connection.execute(
            "SELECT next_word FROM markov_transitions WHERE channel_id = ? AND word = ? AND SUBSTR(next_word, -1, 1) IN ('.', '!', '?', '§') ORDER BY RANDOM() LIMIT 1",
            (channel_id, word),
        ) as cursor:
            row = await cursor.fetchone()
            return row[0] if row else None

    async def add_image(self, channel_id: int, user_id: int, url: str) -> None:
        """
        Stores an image URL for a given channel.

        :param channel_id: The Discord channel ID.
        :param user_id: The Discord user ID of the contributor.
        :param url: The image URL to store.
        """
        await self._connection.execute(
            "INSERT INTO channel_images (channel_id, user_id, url) VALUES (?, ?, ?)",
            (channel_id, user_id, url),
        )
        await self._maybe_commit()

    async def get_random_image(self, channel_id: int) -> str | None:
        """
        Returns a random image URL for a given channel.

        :param channel_id: The Discord channel ID.
        :return: A random image URL, or None if no images exist for this channel.
        """
        async with self._connection.execute(
            "SELECT url FROM channel_images WHERE channel_id = ? ORDER BY RANDOM() LIMIT 1",
            (channel_id,),
        ) as cursor:
            row = await cursor.fetchone()
            return row[0] if row else None

    async def set_flag(self, entity_type: str, entity_id: str, flag_name: str) -> None:
        """
        Sets a flag on a given entity. If the flag is already set, this is a no-op.

        :param entity_type: The type of entity ("channel", "server", or "user").
        :param entity_id: The Discord ID of the entity.
        :param flag_name: The name of the flag to set.
        """
        await self._connection.execute(
            "INSERT OR IGNORE INTO flags (entity_type, entity_id, flag_name) VALUES (?, ?, ?)",
            (entity_type, entity_id, flag_name),
        )
        await self._maybe_commit()

    async def clear_flag(
        self, entity_type: str, entity_id: str, flag_name: str
    ) -> None:
        """
        Clears a flag on a given entity. If the flag is not set, this is a no-op.

        :param entity_type: The type of entity ("channel", "server", or "user").
        :param entity_id: The Discord ID of the entity.
        :param flag_name: The name of the flag to clear.
        """
        await self._connection.execute(
            "DELETE FROM flags WHERE entity_type = ? AND entity_id = ? AND flag_name = ?",
            (entity_type, entity_id, flag_name),
        )
        await self._maybe_commit()

    async def is_flag_set(
        self, entity_type: str, entity_id: str, flag_name: str
    ) -> bool:
        """
        Checks whether a flag is set on a given entity.

        :param entity_type: The type of entity ("channel", "server", or "user").
        :param entity_id: The Discord ID of the entity.
        :param flag_name: The name of the flag to check.
        :return: True if the flag is set, False otherwise.
        """
        async with self._connection.execute(
            "SELECT 1 FROM flags WHERE entity_type = ? AND entity_id = ? AND flag_name = ?",
            (entity_type, entity_id, flag_name),
        ) as cursor:
            return await cursor.fetchone() is not None

    async def is_channel_ingested(self, channel_id: int) -> bool:
        """
        Checks whether a channel has already been bulk-ingested.

        :param channel_id: The Discord channel ID.
        :return: True if the channel has been ingested, False otherwise.
        """
        async with self._connection.execute(
            "SELECT 1 FROM ingested_channels WHERE channel_id = ?",
            (channel_id,),
        ) as cursor:
            return await cursor.fetchone() is not None

    async def mark_channel_ingested(self, channel_id: int) -> None:
        """
        Marks a channel as having been bulk-ingested.

        :param channel_id: The Discord channel ID.
        """
        await self._connection.execute(
            "INSERT OR IGNORE INTO ingested_channels (channel_id) VALUES (?)",
            (channel_id,),
        )
        await self._maybe_commit()

    async def _maybe_commit(self) -> None:
        """Tracks a pending write. Commits if the threshold is reached, otherwise starts a flush timer."""
        self._pending_writes += 1
        if self._pending_writes >= AUTO_COMMIT_WRITE_THRESHOLD:
            await self.commit()
        elif self._flush_task is None:
            self._flush_task = asyncio.create_task(self._flush_after_timeout())

    async def _flush_after_timeout(self) -> None:
        """Background task that commits after the timeout elapses."""
        try:
            await asyncio.sleep(AUTO_COMMIT_TIMEOUT_SECONDS)
            if self._pending_writes > 0:
                await self.commit()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Error in database flush timer.")
