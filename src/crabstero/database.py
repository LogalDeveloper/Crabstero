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

import asyncio
import logging
from collections import Counter
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, NamedTuple, Self, cast

import aiosqlite

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Sequence


logger = logging.getLogger(__name__)


class StartWord(NamedTuple):
    """A Markov chain starting word row."""

    channel_id: int
    user_id: int
    word: str


class Transition(NamedTuple):
    """A Markov chain word transition row."""

    channel_id: int
    user_id: int
    word: str
    next_word: str


class ChannelImage(NamedTuple):
    """An image URL associated with a channel."""

    channel_id: int
    user_id: int
    url: str


# SQLite schema version. Version 0 is the original duplicate-row Markov schema.
_CURRENT_SCHEMA_VERSION = 1


_CURRENT_MARKOV_TABLES_SQL = """
-- Markov chain starting words.
-- Occurrence counters represent frequency weight per contributing user.
CREATE TABLE IF NOT EXISTS markov_start_words (
    channel_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    word TEXT NOT NULL,
    occurrences INTEGER NOT NULL CHECK (occurrences > 0),
    PRIMARY KEY (channel_id, word, user_id)
) WITHOUT ROWID;

-- Markov chain word transitions.
-- Occurrence counters represent frequency weight per contributing user.
CREATE TABLE IF NOT EXISTS markov_transitions (
    channel_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    word TEXT NOT NULL,
    next_word TEXT NOT NULL,
    occurrences INTEGER NOT NULL CHECK (occurrences > 0),
    PRIMARY KEY (channel_id, word, next_word, user_id)
) WITHOUT ROWID;
"""


_CURRENT_NON_MARKOV_TABLES_SQL = """

-- Image URLs per channel.
CREATE TABLE IF NOT EXISTS channel_images (
    channel_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    url TEXT NOT NULL
);

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


_CURRENT_INDEXES_SQL = """
CREATE INDEX IF NOT EXISTS idx_start_user ON markov_start_words(user_id);
CREATE INDEX IF NOT EXISTS idx_transitions_user ON markov_transitions(user_id);
CREATE INDEX IF NOT EXISTS idx_images_channel ON channel_images(channel_id);
CREATE INDEX IF NOT EXISTS idx_images_user ON channel_images(user_id);
"""


_CREATE_CURRENT_SCHEMA_OBJECTS_SQL = (
    """
BEGIN IMMEDIATE;
"""
    + _CURRENT_MARKOV_TABLES_SQL
    + _CURRENT_NON_MARKOV_TABLES_SQL
    + _CURRENT_INDEXES_SQL
    + """
COMMIT;
"""
)


_MIGRATE_LEGACY_MARKOV_TO_COUNTED_ROWS_SQL = (
    """
BEGIN IMMEDIATE;

ALTER TABLE markov_start_words RENAME TO markov_start_words_v0;
ALTER TABLE markov_transitions RENAME TO markov_transitions_v0;

"""
    + _CURRENT_MARKOV_TABLES_SQL
    + _CURRENT_NON_MARKOV_TABLES_SQL
    + """

INSERT INTO markov_start_words (channel_id, user_id, word, occurrences)
SELECT channel_id, user_id, word, COUNT(*)
FROM markov_start_words_v0
GROUP BY channel_id, word, user_id
ORDER BY channel_id, word, user_id;

INSERT INTO markov_transitions (
    channel_id,
    user_id,
    word,
    next_word,
    occurrences
)
SELECT channel_id, user_id, word, next_word, COUNT(*)
FROM markov_transitions_v0
GROUP BY channel_id, word, next_word, user_id
ORDER BY channel_id, word, next_word, user_id;

DROP TABLE markov_start_words_v0;
DROP TABLE markov_transitions_v0;

"""
    + _CURRENT_INDEXES_SQL
    + """
COMMIT;
"""
)


def _has_expected_markov_primary_keys(
    start_columns: Sequence[Sequence[object]],
    transition_columns: Sequence[Sequence[object]],
) -> bool:
    """Return whether the Markov tables have the optimized primary keys."""
    start_primary_keys = {str(row[1]): cast("int", row[5]) for row in start_columns}
    transition_primary_keys = {
        str(row[1]): cast("int", row[5]) for row in transition_columns
    }
    return start_primary_keys == {
        "channel_id": 1,
        "word": 2,
        "user_id": 3,
        "occurrences": 0,
    } and transition_primary_keys == {
        "channel_id": 1,
        "word": 2,
        "next_word": 3,
        "user_id": 4,
        "occurrences": 0,
    }


async def _get_table_sql(connection: aiosqlite.Connection, table_name: str) -> str:
    """Return the SQLite schema SQL for a table."""
    async with connection.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table_name,),
    ) as cursor:
        row = await cursor.fetchone()
    return str(row[0]) if row else ""


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
        self._tx_lock = asyncio.Lock()

    @asynccontextmanager
    async def _transaction(self) -> AsyncIterator[None]:
        """Begin an immediate write transaction.

        Acquires an async lock so that only one transaction runs at a
        time, then commits on success or rolls back on error.
        """
        async with self._tx_lock:
            await self._connection.execute("BEGIN IMMEDIATE")
            try:
                yield
            except BaseException:
                await self._connection.rollback()
                raise
            else:
                await self._connection.commit()

    @classmethod
    async def connect(cls, path: str) -> Self:
        """Open a SQLite database, configure it, and create the schema.

        Configure the database for performance and create the schema if it
        does not already exist.

        :param path: The file path to the SQLite database.
        :return: A new Database instance ready for use.
        """
        connection = await aiosqlite.connect(path, isolation_level=None)
        try:
            # Set synchronous to NORMAL for a balance between safety and speed.
            await connection.execute("PRAGMA synchronous=NORMAL")

            async with connection.execute("PRAGMA user_version") as cursor:
                row = await cursor.fetchone()
            version = int(row[0]) if row else 0
            if version > _CURRENT_SCHEMA_VERSION:
                msg = (
                    f"Database schema version {version} is newer than supported "
                    f"version {_CURRENT_SCHEMA_VERSION}."
                )
                raise RuntimeError(msg)

            async with connection.execute(
                "SELECT name FROM sqlite_master"
                " WHERE type = 'table'"
                " AND name IN ('markov_start_words', 'markov_transitions')",
            ) as cursor:
                markov_tables = {str(row[0]) for row in await cursor.fetchall()}
            has_start_words = "markov_start_words" in markov_tables
            has_transitions = "markov_transitions" in markov_tables
            if has_start_words != has_transitions:
                raise RuntimeError("Database schema is missing a Markov table.")

            has_counted_markov = False
            has_current_markov = False
            if has_start_words:
                async with connection.execute(
                    "PRAGMA table_info(markov_start_words)",
                ) as cursor:
                    start_column_rows = [tuple(row) for row in await cursor.fetchall()]
                async with connection.execute(
                    "PRAGMA table_info(markov_transitions)",
                ) as cursor:
                    transition_column_rows = [
                        tuple(row) for row in await cursor.fetchall()
                    ]
                start_columns = {str(row[1]) for row in start_column_rows}
                transition_columns = {str(row[1]) for row in transition_column_rows}
                start_has_occurrences = "occurrences" in start_columns
                transition_has_occurrences = "occurrences" in transition_columns
                if start_has_occurrences != transition_has_occurrences:
                    raise RuntimeError(
                        "Database schema has mismatched Markov occurrence columns.",
                    )
                has_counted_markov = (
                    start_has_occurrences and transition_has_occurrences
                )
                if has_counted_markov:
                    start_table_sql = await _get_table_sql(
                        connection,
                        "markov_start_words",
                    )
                    transition_table_sql = await _get_table_sql(
                        connection,
                        "markov_transitions",
                    )
                    has_current_markov = (
                        _has_expected_markov_primary_keys(
                            start_column_rows,
                            transition_column_rows,
                        )
                        and "WITHOUT ROWID" in start_table_sql.upper()
                        and "WITHOUT ROWID" in transition_table_sql.upper()
                    )
                    if not has_current_markov:
                        raise RuntimeError(
                            "Database schema has unsupported counted Markov tables.",
                        )

            needs_markov_count_migration = has_start_words and not has_counted_markov
            needs_post_migration_vacuum = (
                has_current_markov and version < _CURRENT_SCHEMA_VERSION
            )
            if version == _CURRENT_SCHEMA_VERSION and needs_markov_count_migration:
                raise RuntimeError(
                    "Database schema version 1 has legacy Markov tables.",
                )

            if not needs_markov_count_migration:
                schema_script = _CREATE_CURRENT_SCHEMA_OBJECTS_SQL
            else:
                logger.info(
                    "Migrating legacy Markov tables to counted occurrences; "
                    "startup will continue after migration completes.",
                )
                schema_script = _MIGRATE_LEGACY_MARKOV_TO_COUNTED_ROWS_SQL

            try:
                await connection.executescript(schema_script)
            except BaseException:
                await connection.rollback()
                raise
            if needs_markov_count_migration:
                logger.info("Legacy Markov table migration complete.")
            if needs_markov_count_migration or needs_post_migration_vacuum:
                if needs_post_migration_vacuum:
                    logger.info(
                        "Retrying post-migration database vacuum before marking "
                        "schema version current.",
                    )
                logger.info(
                    "Vacuuming database after legacy Markov table migration; "
                    "startup will continue after vacuum completes.",
                )
                try:
                    async with connection.execute("VACUUM"):
                        pass
                except Exception:
                    logger.exception(
                        "Database vacuum failed after legacy Markov table "
                        "migration; continuing with migrated database.",
                    )
                else:
                    logger.info("Post-migration database vacuum complete.")
                    await connection.execute(
                        f"PRAGMA user_version = {_CURRENT_SCHEMA_VERSION}",
                    )
            elif version < _CURRENT_SCHEMA_VERSION:
                await connection.execute(
                    f"PRAGMA user_version = {_CURRENT_SCHEMA_VERSION}",
                )
        except BaseException:
            await connection.close()
            raise

        return cls(connection)

    async def close(self) -> None:
        """Close the database connection."""
        await self._connection.close()

    async def add_markov_data(
        self,
        start_words: list[StartWord],
        transitions: list[Transition],
    ) -> None:
        """Insert or increment Markov start words and transitions, then commit.

        Both writes happen in a single transaction.

        :param start_words: Starting word entries to insert.
        :param transitions: Transition entries to insert.
        """
        if start_words or transitions:
            async with self._transaction():
                if start_words:
                    counted_start_words = [
                        (*start_word, occurrences)
                        for start_word, occurrences in Counter(start_words).items()
                    ]
                    await self._connection.executemany(
                        "INSERT INTO markov_start_words"
                        " (channel_id, user_id, word, occurrences)"
                        " VALUES (?, ?, ?, ?)"
                        " ON CONFLICT(channel_id, word, user_id)"
                        " DO UPDATE SET occurrences ="
                        " markov_start_words.occurrences + excluded.occurrences",
                        counted_start_words,
                    )
                if transitions:
                    counted_transitions = [
                        (*transition, occurrences)
                        for transition, occurrences in Counter(transitions).items()
                    ]
                    await self._connection.executemany(
                        "INSERT INTO markov_transitions"
                        " (channel_id, user_id, word, next_word, occurrences)"
                        " VALUES (?, ?, ?, ?, ?)"
                        " ON CONFLICT(channel_id, word, next_word, user_id)"
                        " DO UPDATE SET occurrences ="
                        " markov_transitions.occurrences + excluded.occurrences",
                        counted_transitions,
                    )

    async def remove_markov_data(
        self,
        start_words: list[StartWord],
        transitions: list[Transition],
    ) -> None:
        """Remove one matching occurrence per entry from the Markov tables.

        Each entry removes at most one counted occurrence, preserving remaining
        frequency weight.

        :param start_words: Starting word entries to remove.
        :param transitions: Transition entries to remove.
        """
        if start_words or transitions:
            async with self._transaction():
                if start_words:
                    counted_start_words = [
                        (*start_word, occurrences)
                        for start_word, occurrences in Counter(start_words).items()
                    ]
                    await self._connection.executemany(
                        "DELETE FROM markov_start_words"
                        " WHERE channel_id = ?"
                        " AND user_id = ?"
                        " AND word = ?"
                        " AND occurrences <= ?",
                        counted_start_words,
                    )
                    await self._connection.executemany(
                        "UPDATE markov_start_words"
                        " SET occurrences = occurrences - ?"
                        " WHERE channel_id = ?"
                        " AND user_id = ?"
                        " AND word = ?"
                        " AND occurrences > ?",
                        [
                            (
                                occurrences,
                                start_word.channel_id,
                                start_word.user_id,
                                start_word.word,
                                occurrences,
                            )
                            for start_word, occurrences in Counter(start_words).items()
                        ],
                    )
                if transitions:
                    counted_transitions = [
                        (*transition, occurrences)
                        for transition, occurrences in Counter(transitions).items()
                    ]
                    await self._connection.executemany(
                        "DELETE FROM markov_transitions"
                        " WHERE channel_id = ?"
                        " AND user_id = ?"
                        " AND word = ?"
                        " AND next_word = ?"
                        " AND occurrences <= ?",
                        counted_transitions,
                    )
                    await self._connection.executemany(
                        "UPDATE markov_transitions"
                        " SET occurrences = occurrences - ?"
                        " WHERE channel_id = ?"
                        " AND user_id = ?"
                        " AND word = ?"
                        " AND next_word = ?"
                        " AND occurrences > ?",
                        [
                            (
                                occurrences,
                                transition.channel_id,
                                transition.user_id,
                                transition.word,
                                transition.next_word,
                                occurrences,
                            )
                            for transition, occurrences in Counter(transitions).items()
                        ],
                    )

    async def get_random_start_word(self, channel_id: int) -> str | None:
        """Return a random starting word for a channel.

        Weighted by occurrence frequency.

        :param channel_id: The Discord channel ID.
        :return: A random starting word, or None if none exist.
        """
        # Mask RANDOM() instead of ABS(RANDOM()) to avoid signed 64-bit overflow.
        async with self._connection.execute(
            """
            WITH weighted AS (
                SELECT word, SUM(occurrences) AS weight
                FROM markov_start_words
                WHERE channel_id = ?
                GROUP BY word
            ),
            total AS (
                SELECT SUM(weight) AS total_weight
                FROM weighted
            ),
            choice AS MATERIALIZED (
                SELECT
                    ((RANDOM() & 9223372036854775807) % total_weight) + 1
                    AS selected_weight
                FROM total
                WHERE total_weight > 0
            ),
            ranked AS (
                SELECT
                    word,
                    SUM(weight) OVER (
                        ORDER BY word
                        ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
                    ) AS cumulative_weight
                FROM weighted
            )
            SELECT word
            FROM ranked
            CROSS JOIN choice
            WHERE cumulative_weight >= selected_weight
            ORDER BY cumulative_weight
            LIMIT 1
            """,
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
        # Mask RANDOM() instead of ABS(RANDOM()) to avoid signed 64-bit overflow.
        async with self._connection.execute(
            """
            WITH weighted AS (
                SELECT next_word, SUM(occurrences) AS weight
                FROM markov_transitions
                WHERE channel_id = ? AND word = ?
                GROUP BY next_word
            ),
            total AS (
                SELECT SUM(weight) AS total_weight
                FROM weighted
            ),
            choice AS MATERIALIZED (
                SELECT
                    ((RANDOM() & 9223372036854775807) % total_weight) + 1
                    AS selected_weight
                FROM total
                WHERE total_weight > 0
            ),
            ranked AS (
                SELECT
                    next_word,
                    SUM(weight) OVER (
                        ORDER BY next_word
                        ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
                    ) AS cumulative_weight
                FROM weighted
            )
            SELECT next_word
            FROM ranked
            CROSS JOIN choice
            WHERE cumulative_weight >= selected_weight
            ORDER BY cumulative_weight
            LIMIT 1
            """,
            (channel_id, word),
        ) as cursor:
            row = await cursor.fetchone()
            return row[0] if row else None

    async def get_random_completing_next_word(
        self,
        channel_id: int,
        word: str,
    ) -> str | None:
        """Return a random sentence-ending next word for a given word in a channel.

        Weighted by occurrence frequency. A completing word is one whose last
        character is '.', '!', '?', or '§'.

        The set of terminators must match ``markov._TERMINATORS``.

        :param channel_id: The Discord channel ID.
        :param word: The current word to find a completing transition for.
        :return: A random completing next word, or None if none exist.
        """
        # Mask RANDOM() instead of ABS(RANDOM()) to avoid signed 64-bit overflow.
        async with self._connection.execute(
            """
            WITH weighted AS (
                SELECT next_word, SUM(occurrences) AS weight
                FROM markov_transitions
                WHERE channel_id = ?
                AND word = ?
                AND SUBSTR(next_word, -1, 1) IN ('.', '!', '?', '§')
                GROUP BY next_word
            ),
            total AS (
                SELECT SUM(weight) AS total_weight
                FROM weighted
            ),
            choice AS MATERIALIZED (
                SELECT
                    ((RANDOM() & 9223372036854775807) % total_weight) + 1
                    AS selected_weight
                FROM total
                WHERE total_weight > 0
            ),
            ranked AS (
                SELECT
                    next_word,
                    SUM(weight) OVER (
                        ORDER BY next_word
                        ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
                    ) AS cumulative_weight
                FROM weighted
            )
            SELECT next_word
            FROM ranked
            CROSS JOIN choice
            WHERE cumulative_weight >= selected_weight
            ORDER BY cumulative_weight
            LIMIT 1
            """,
            (channel_id, word),
        ) as cursor:
            row = await cursor.fetchone()
            return row[0] if row else None

    async def add_images(self, images: list[ChannelImage]) -> None:
        """Store image URLs for a given channel.

        :param images: Image entries to insert.
        """
        if images:
            async with self._transaction():
                await self._connection.executemany(
                    "INSERT INTO channel_images"
                    " (channel_id, user_id, url)"
                    " VALUES (?, ?, ?)",
                    images,
                )

    async def remove_images(self, images: list[ChannelImage]) -> None:
        """Remove one matching row per entry from the images table.

        Each entry removes at most one duplicate row, preserving remaining
        frequency weight.

        :param images: Image entries to remove.
        """
        if images:
            async with self._transaction():
                await self._connection.executemany(
                    "DELETE FROM channel_images"
                    " WHERE rowid = ("
                    "   SELECT rowid FROM channel_images"
                    "   WHERE channel_id = ?"
                    "   AND user_id = ?"
                    "   AND url = ?"
                    "   LIMIT 1"
                    " )",
                    images,
                )

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
        async with self._transaction():
            await self._connection.execute(
                "INSERT OR IGNORE INTO flags"
                " (entity_type, entity_id, flag_name)"
                " VALUES (?, ?, ?)",
                (entity_type, entity_id, flag_name),
            )

    async def clear_flag(
        self,
        entity_type: str,
        entity_id: str,
        flag_name: str,
    ) -> None:
        """Clear a flag on a given entity. If the flag is not set, this is a no-op.

        :param entity_type: The entity type ("channel", "server", "user").
        :param entity_id: The Discord ID of the entity.
        :param flag_name: The name of the flag to clear.
        """
        async with self._transaction():
            await self._connection.execute(
                "DELETE FROM flags"
                " WHERE entity_type = ?"
                " AND entity_id = ?"
                " AND flag_name = ?",
                (entity_type, entity_id, flag_name),
            )

    async def is_flag_set(
        self,
        entity_type: str,
        entity_id: str,
        flag_name: str,
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
        async with self._transaction():
            await self._connection.execute(
                "INSERT OR IGNORE INTO ingested_channels (channel_id) VALUES (?)",
                (channel_id,),
            )

    async def forget_user(self, user_id: int, no_ingest_flag: str) -> None:
        """Delete all user data, clear flags, and set noIngest atomically.

        Sets the noIngest flag, clears all other user flags, and deletes
        all Markov and image data in a single transaction.

        :param user_id: The Discord user ID to forget.
        :param no_ingest_flag: The flag name for noIngest.
        """
        entity_id = str(user_id)
        async with self._transaction():
            await self._connection.execute(
                "INSERT OR IGNORE INTO flags (entity_type, entity_id, flag_name)"
                " VALUES ('user', ?, ?)",
                (entity_id, no_ingest_flag),
            )
            await self._connection.execute(
                "DELETE FROM flags WHERE entity_type = 'user'"
                " AND entity_id = ? AND flag_name != ?",
                (entity_id, no_ingest_flag),
            )
            await self._connection.execute(
                "DELETE FROM markov_start_words WHERE user_id = ?",
                (user_id,),
            )
            await self._connection.execute(
                "DELETE FROM markov_transitions WHERE user_id = ?",
                (user_id,),
            )
            await self._connection.execute(
                "DELETE FROM channel_images WHERE user_id = ?",
                (user_id,),
            )
