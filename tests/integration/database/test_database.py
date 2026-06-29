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

"""Integration tests for the Database class.

Tests cover Database.connect (pragmas, schema), markov start word and
transition CRUD, image storage, flag CRUD, and channel ingestion tracking.
"""

import logging
from collections import Counter
from typing import TYPE_CHECKING, Any

import aiosqlite
import pytest

from crabstero.database import ChannelImage, Database, StartWord, Transition
from crabstero.flags import EntityType, Flag

if TYPE_CHECKING:
    from pathlib import Path


class TestConnect:
    """Database.connect creates a configured SQLite database."""

    async def test_synchronous_normal(self, db: Database) -> None:
        """Synchronous mode is set to NORMAL."""
        async with db._connection.execute("PRAGMA synchronous") as cursor:
            row = await cursor.fetchone()
        assert row is not None
        assert row[0] == 1

    async def test_schema_creates_tables(self, db: Database) -> None:
        """All expected tables exist after connect."""
        async with db._connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name",
        ) as cursor:
            tables = [row[0] for row in await cursor.fetchall()]
        assert tables == [
            "channel_images",
            "flags",
            "ingested_channels",
            "markov_start_words",
            "markov_transitions",
        ]

    async def test_schema_version(self, db: Database) -> None:
        """Fresh databases are marked with the current schema version."""
        async with db._connection.execute("PRAGMA user_version") as cursor:
            row = await cursor.fetchone()
        assert row is not None
        assert row[0] == 1

    async def test_connection_is_closed_on_setup_failure(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Failed setup closes the connection before re-raising."""
        db_path = str(tmp_path / "future.db")
        future = await aiosqlite.connect(db_path, isolation_level=None)
        try:
            await future.execute("PRAGMA user_version = 2")
        finally:
            await future.close()

        opened_connections: list[aiosqlite.Connection] = []
        real_connect = aiosqlite.connect

        def spy_connect(*args: Any, **kwargs: Any) -> aiosqlite.Connection:
            connection = real_connect(*args, **kwargs)
            opened_connections.append(connection)
            return connection

        monkeypatch.setattr(aiosqlite, "connect", spy_connect)

        with pytest.raises(RuntimeError, match="newer than supported"):
            await Database.connect(db_path)

        assert len(opened_connections) == 1
        opened_connection = opened_connections[0]
        try:
            with pytest.raises(ValueError, match="no active connection"):
                await opened_connection.execute("SELECT 1")
        finally:
            await opened_connection.close()

    async def test_markov_schema_uses_counted_rows(self, db: Database) -> None:
        """Markov tables use occurrence counters and optimized primary keys."""
        async with db._connection.execute(
            "PRAGMA table_info(markov_start_words)",
        ) as cursor:
            start_columns = await cursor.fetchall()
        async with db._connection.execute(
            "PRAGMA table_info(markov_transitions)",
        ) as cursor:
            transition_columns = await cursor.fetchall()
        assert [(row[1], row[5]) for row in start_columns] == [
            ("channel_id", 1),
            ("user_id", 3),
            ("word", 2),
            ("occurrences", 0),
        ]
        assert [(row[1], row[5]) for row in transition_columns] == [
            ("channel_id", 1),
            ("user_id", 4),
            ("word", 2),
            ("next_word", 3),
            ("occurrences", 0),
        ]

        async with db._connection.execute(
            "SELECT name, sql FROM sqlite_master"
            " WHERE type = 'table'"
            " AND name IN ('markov_start_words', 'markov_transitions')",
        ) as cursor:
            table_sql = {
                str(row[0]): str(row[1]) for row in await cursor.fetchall()
            }
        assert "occurrences INTEGER NOT NULL CHECK (occurrences > 0)" in table_sql[
            "markov_start_words"
        ]
        assert "WITHOUT ROWID" in table_sql["markov_start_words"]
        assert "WITHOUT ROWID" in table_sql["markov_transitions"]

    async def test_markov_indexes_exist(self, db: Database) -> None:
        """Expected explicit Markov indexes exist on fresh databases."""
        async with db._connection.execute(
            "SELECT name FROM sqlite_master"
            " WHERE type = 'index'"
            " AND tbl_name IN ('markov_start_words', 'markov_transitions')"
            " ORDER BY name",
        ) as cursor:
            indexes = [row[0] for row in await cursor.fetchall()]
        assert indexes == [
            "idx_start_user",
            "idx_transitions_user",
        ]

    async def test_old_markov_schema_is_converted(
        self,
        tmp_path: Path,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Old duplicate Markov rows are converted into counted rows."""
        db_path = str(tmp_path / "legacy.db")
        legacy = await aiosqlite.connect(db_path, isolation_level=None)
        try:
            await legacy.executescript(
                """
                CREATE TABLE markov_start_words (
                    channel_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    word TEXT NOT NULL
                );
                CREATE INDEX idx_start_channel
                    ON markov_start_words(channel_id, word);
                CREATE INDEX idx_start_user ON markov_start_words(user_id);

                CREATE TABLE markov_transitions (
                    channel_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    word TEXT NOT NULL,
                    next_word TEXT NOT NULL
                );
                CREATE INDEX idx_transitions_channel_word
                    ON markov_transitions(channel_id, word);
                CREATE INDEX idx_transitions_user ON markov_transitions(user_id);

                CREATE TABLE channel_images (
                    channel_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    url TEXT NOT NULL
                );
                CREATE INDEX idx_images_channel ON channel_images(channel_id);
                CREATE INDEX idx_images_user ON channel_images(user_id);

                CREATE TABLE flags (
                    entity_type TEXT NOT NULL,
                    entity_id TEXT NOT NULL,
                    flag_name TEXT NOT NULL,
                    PRIMARY KEY (entity_type, entity_id, flag_name)
                );

                CREATE TABLE ingested_channels (
                    channel_id INTEGER NOT NULL PRIMARY KEY
                );

                INSERT INTO markov_start_words
                    (channel_id, user_id, word)
                VALUES
                    (1, 100, 'Hello'),
                    (1, 100, 'Hello'),
                    (1, 200, 'Hello'),
                    (2, 100, 'Hello');
                INSERT INTO markov_transitions
                    (channel_id, user_id, word, next_word)
                VALUES
                    (1, 100, 'Hello', 'world.'),
                    (1, 100, 'Hello', 'world.'),
                    (1, 100, 'Hello', 'friend.'),
                    (1, 200, 'Hello', 'world.');
                INSERT INTO channel_images
                    (channel_id, user_id, url)
                VALUES
                    (1, 100, 'https://example.com/a.png');
                INSERT INTO flags
                    (entity_type, entity_id, flag_name)
                VALUES
                    ('user', '100', 'allowPings');
                INSERT INTO ingested_channels (channel_id) VALUES (1);
                """,
            )
        finally:
            await legacy.close()

        with caplog.at_level(logging.INFO, logger="crabstero.database"):
            migrated = await Database.connect(db_path)
        assert "Migrating legacy Markov tables to counted occurrences" in caplog.text
        assert "Legacy Markov table migration complete." in caplog.text
        assert "Post-migration database vacuum complete." in caplog.text
        try:
            async with migrated._connection.execute(
                "SELECT channel_id, user_id, word, occurrences"
                " FROM markov_start_words"
                " ORDER BY channel_id, user_id, word",
            ) as cursor:
                start_rows = [tuple(row) for row in await cursor.fetchall()]
            assert start_rows == [
                (1, 100, "Hello", 2),
                (1, 200, "Hello", 1),
                (2, 100, "Hello", 1),
            ]

            async with migrated._connection.execute(
                "SELECT channel_id, user_id, word, next_word, occurrences"
                " FROM markov_transitions"
                " ORDER BY channel_id, user_id, word, next_word",
            ) as cursor:
                transition_rows = [tuple(row) for row in await cursor.fetchall()]
            assert transition_rows == [
                (1, 100, "Hello", "friend.", 1),
                (1, 100, "Hello", "world.", 2),
                (1, 200, "Hello", "world.", 1),
            ]

            assert await migrated.get_random_image(1) == "https://example.com/a.png"
            assert await migrated.is_flag_set("user", "100", "allowPings") is True
            assert await migrated.is_channel_ingested(1) is True

            async with migrated._connection.execute("PRAGMA user_version") as cursor:
                row = await cursor.fetchone()
            assert row is not None
            assert row[0] == 1

            async with migrated._connection.execute(
                "SELECT name FROM sqlite_master"
                " WHERE type = 'table'"
                " AND name IN ("
                " 'markov_start_words_v0',"
                " 'markov_transitions_v0'"
                " )",
            ) as cursor:
                assert await cursor.fetchall() == []

            async with migrated._connection.execute(
                "SELECT name FROM sqlite_master"
                " WHERE type = 'index'"
                " AND tbl_name IN ('markov_start_words', 'markov_transitions')"
                " ORDER BY name",
            ) as cursor:
                indexes = [row[0] for row in await cursor.fetchall()]
            assert indexes == [
                "idx_start_user",
                "idx_transitions_user",
            ]
        finally:
            await migrated.close()

        reopened = await Database.connect(db_path)
        try:
            async with reopened._connection.execute(
                "SELECT channel_id, user_id, word, occurrences"
                " FROM markov_start_words"
                " ORDER BY channel_id, user_id, word",
            ) as cursor:
                assert [tuple(row) for row in await cursor.fetchall()] == start_rows
        finally:
            await reopened.close()

    async def test_failed_post_migration_vacuum_is_retried(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """A migrated database is not marked current until vacuum succeeds."""
        db_path = str(tmp_path / "legacy.db")
        legacy = await aiosqlite.connect(db_path, isolation_level=None)
        try:
            await legacy.executescript(
                """
                CREATE TABLE markov_start_words (
                    channel_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    word TEXT NOT NULL
                );

                CREATE TABLE markov_transitions (
                    channel_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    word TEXT NOT NULL,
                    next_word TEXT NOT NULL
                );

                INSERT INTO markov_start_words
                    (channel_id, user_id, word)
                VALUES
                    (1, 100, 'Hello'),
                    (1, 100, 'Hello');
                INSERT INTO markov_transitions
                    (channel_id, user_id, word, next_word)
                VALUES
                    (1, 100, 'Hello', 'world.'),
                    (1, 100, 'Hello', 'world.');
                """,
            )
        finally:
            await legacy.close()

        real_execute = aiosqlite.Connection.execute
        vacuum_failures = 0

        def fail_first_vacuum(
            connection: aiosqlite.Connection,
            sql: str,
            parameters: Any | None = None,
        ) -> Any:
            nonlocal vacuum_failures
            if sql.strip().upper() == "VACUUM" and vacuum_failures == 0:
                vacuum_failures += 1
                raise RuntimeError("simulated vacuum failure")
            return real_execute(connection, sql, parameters)

        monkeypatch.setattr(aiosqlite.Connection, "execute", fail_first_vacuum)

        with caplog.at_level(logging.INFO, logger="crabstero.database"):
            migrated = await Database.connect(db_path)
        assert (
            "Database vacuum failed after legacy Markov table migration"
            in caplog.text
        )
        assert vacuum_failures == 1
        try:
            async with migrated._connection.execute("PRAGMA user_version") as cursor:
                row = await cursor.fetchone()
            assert row is not None
            assert row[0] == 0
        finally:
            await migrated.close()

        caplog.clear()
        with caplog.at_level(logging.INFO, logger="crabstero.database"):
            reopened = await Database.connect(db_path)
        assert "Retrying post-migration database vacuum" in caplog.text
        assert "Post-migration database vacuum complete." in caplog.text
        try:
            async with reopened._connection.execute("PRAGMA user_version") as cursor:
                row = await cursor.fetchone()
            assert row is not None
            assert row[0] == 1
        finally:
            await reopened.close()

    async def test_unsupported_counted_markov_schema_is_rejected(
        self,
        tmp_path: Path,
    ) -> None:
        """Counted Markov tables must match the optimized schema layout."""
        db_path = str(tmp_path / "unsupported.db")
        counted = await aiosqlite.connect(db_path, isolation_level=None)
        try:
            await counted.executescript(
                """
                CREATE TABLE markov_start_words (
                    channel_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    word TEXT NOT NULL,
                    occurrences INTEGER NOT NULL CHECK (occurrences > 0),
                    PRIMARY KEY (channel_id, user_id, word)
                );

                CREATE TABLE markov_transitions (
                    channel_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    word TEXT NOT NULL,
                    next_word TEXT NOT NULL,
                    occurrences INTEGER NOT NULL CHECK (occurrences > 0),
                    PRIMARY KEY (channel_id, user_id, word, next_word)
                );

                PRAGMA user_version = 1;
                """,
            )
        finally:
            await counted.close()

        with pytest.raises(RuntimeError, match="unsupported counted Markov"):
            await Database.connect(db_path)


class TestAddMarkovData:
    """Markov start word and transition storage via add_markov_data."""

    async def test_stores_start_word(self, db: Database) -> None:
        """Inserted start word can be retrieved by channel."""
        await db.add_markov_data([StartWord(1, 100, "Hello")], [])
        result = await db.get_random_start_word(1)
        assert result == "Hello"

    async def test_stores_transition(self, db: Database) -> None:
        """Inserted transition can be retrieved by channel and word."""
        await db.add_markov_data([], [Transition(1, 100, "Hello", "world.")])
        result = await db.get_random_next_word(1, "Hello")
        assert result == "world."

    async def test_stores_both_in_single_call(self, db: Database) -> None:
        """Start words and transitions are stored in a single call."""
        await db.add_markov_data(
            [StartWord(1, 100, "Hello")],
            [Transition(1, 100, "Hello", "world.")],
        )
        assert await db.get_random_start_word(1) == "Hello"
        assert await db.get_random_next_word(1, "Hello") == "world."

    async def test_empty_lists_is_noop(self, db: Database) -> None:
        """Empty lists do not store any data."""
        await db.add_markov_data([], [])
        assert await db.get_random_start_word(1) is None

    async def test_duplicate_inputs_increment_occurrences(self, db: Database) -> None:
        """Duplicate inputs in one call increment one counted row."""
        await db.add_markov_data(
            [StartWord(1, 100, "Hello"), StartWord(1, 100, "Hello")],
            [
                Transition(1, 100, "Hello", "world."),
                Transition(1, 100, "Hello", "world."),
            ],
        )

        async with db._connection.execute(
            "SELECT occurrences FROM markov_start_words"
            " WHERE channel_id = 1 AND user_id = 100 AND word = 'Hello'",
        ) as cursor:
            row = await cursor.fetchone()
        assert row == (2,)
        async with db._connection.execute(
            "SELECT occurrences FROM markov_transitions"
            " WHERE channel_id = 1"
            " AND user_id = 100"
            " AND word = 'Hello'"
            " AND next_word = 'world.'",
        ) as cursor:
            row = await cursor.fetchone()
        assert row == (2,)


class TestMarkovReadMethods:
    """Markov read methods return None for out-of-scope or missing data."""

    async def test_returns_none_when_empty(self, db: Database) -> None:
        """Returns None when no data has been stored."""
        assert await db.get_random_start_word(1) is None
        assert await db.get_random_next_word(1, "nonexistent") is None
        assert await db.get_random_completing_next_word(1, "nonexistent") is None

    async def test_start_word_scoped_to_channel(self, db: Database) -> None:
        """A start word in one channel is not returned for another channel."""
        await db.add_markov_data([StartWord(1, 100, "Hello")], [])
        assert await db.get_random_start_word(2) is None

    async def test_next_word_scoped_to_channel(self, db: Database) -> None:
        """A transition in one channel is not returned for another channel."""
        await db.add_markov_data([], [Transition(1, 100, "Hello", "world.")])
        assert await db.get_random_next_word(2, "Hello") is None

    async def test_completing_next_word_scoped_to_channel(self, db: Database) -> None:
        """Completing transition is not returned for another channel."""
        await db.add_markov_data([], [Transition(1, 100, "Hello", "world.")])
        assert await db.get_random_completing_next_word(2, "Hello") is None

    async def test_next_word_scoped_to_word(self, db: Database) -> None:
        """A transition for one word is not returned when querying a different word."""
        await db.add_markov_data([], [Transition(1, 100, "Hello", "world.")])
        assert await db.get_random_next_word(1, "Goodbye") is None

    async def test_completing_next_word_scoped_to_word(self, db: Database) -> None:
        """Completing transition is not returned for a different word."""
        await db.add_markov_data([], [Transition(1, 100, "Hello", "world.")])
        assert await db.get_random_completing_next_word(1, "Goodbye") is None

    @pytest.mark.parametrize(
        "completing_word",
        [
            pytest.param("world.", id="period"),
            pytest.param("world!", id="exclamation"),
            pytest.param("world?", id="question"),
            pytest.param("world\u00a7", id="section-sign"),
        ],
    )
    async def test_completing_word_filters_punctuation(
        self,
        db: Database,
        completing_word: str,
    ) -> None:
        """get_random_completing_next_word only returns sentence-ending words."""
        await db.add_markov_data(
            [],
            [
                Transition(1, 100, "Hello", "beautiful"),
                Transition(1, 100, "Hello", completing_word),
            ],
        )
        for _ in range(100):
            result = await db.get_random_completing_next_word(1, "Hello")
            assert result == completing_word

    async def test_completing_returns_none_without_match(self, db: Database) -> None:
        """Returns None when no transitions end with sentence punctuation."""
        await db.add_markov_data([], [Transition(1, 100, "Hello", "beautiful")])
        result = await db.get_random_completing_next_word(1, "Hello")
        assert result is None

    async def test_start_word_pooled_across_users(self, db: Database) -> None:
        """Start words from different users are visible in the same channel query."""
        await db.add_markov_data(
            [StartWord(1, 100, "Hello"), StartWord(1, 200, "Goodbye")],
            [],
        )
        assert await db.get_random_start_word(1) in {"Hello", "Goodbye"}

    async def test_next_word_pooled_across_users(self, db: Database) -> None:
        """Transitions from different users are visible in the same channel query."""
        await db.add_markov_data(
            [],
            [
                Transition(1, 100, "Hello", "world."),
                Transition(1, 200, "Hello", "friend."),
            ],
        )
        assert await db.get_random_next_word(1, "Hello") in {"world.", "friend."}

    async def test_completing_next_word_pooled_across_users(self, db: Database) -> None:
        """Completing transitions from different users are visible."""
        await db.add_markov_data(
            [],
            [
                Transition(1, 100, "Hello", "world."),
                Transition(1, 200, "Hello", "friend."),
            ],
        )
        assert await db.get_random_completing_next_word(1, "Hello") in {
            "world.",
            "friend.",
        }

    async def test_same_candidate_aggregates_across_users(self, db: Database) -> None:
        """Identical candidates contributed by multiple users are still one choice."""
        await db.add_markov_data(
            [StartWord(1, 100, "Hello"), StartWord(1, 200, "Hello")],
            [
                Transition(1, 100, "Hello", "world."),
                Transition(1, 200, "Hello", "world."),
            ],
        )

        for _ in range(20):
            assert await db.get_random_start_word(1) == "Hello"
            assert await db.get_random_next_word(1, "Hello") == "world."

    async def test_equal_weights_sample_evenly(self, db: Database) -> None:
        """Weighted random reads use one random threshold per query."""
        await db.add_markov_data(
            [
                StartWord(1, 100, "Alpha"),
                StartWord(1, 100, "Beta"),
                StartWord(1, 100, "Gamma"),
            ],
            [
                Transition(1, 100, "seed", "alpha"),
                Transition(1, 100, "seed", "beta"),
                Transition(1, 100, "seed", "gamma"),
            ],
        )

        samples = 1200
        start_counts: Counter[str] = Counter()
        transition_counts: Counter[str] = Counter()
        for _ in range(samples):
            start_word = await db.get_random_start_word(1)
            next_word = await db.get_random_next_word(1, "seed")
            assert start_word is not None
            assert next_word is not None
            start_counts[start_word] += 1
            transition_counts[next_word] += 1

        expected = samples / 3
        tolerance = samples * 0.075
        assert set(start_counts) == {"Alpha", "Beta", "Gamma"}
        assert set(transition_counts) == {"alpha", "beta", "gamma"}
        for counts in (start_counts, transition_counts):
            for count in counts.values():
                assert expected - tolerance <= count <= expected + tolerance


class TestRemoveMarkovData:
    """Markov data removal via remove_markov_data."""

    async def test_removes_one_start_word(self, db: Database) -> None:
        """Removes exactly one matching start word row."""
        await db.add_markov_data(
            [StartWord(1, 100, "Hello"), StartWord(1, 100, "Hello")],
            [],
        )
        await db.remove_markov_data([StartWord(1, 100, "Hello")], [])
        # One copy should remain.
        assert await db.get_random_start_word(1) == "Hello"

    async def test_removes_one_transition(self, db: Database) -> None:
        """Removes exactly one matching transition row."""
        await db.add_markov_data(
            [],
            [
                Transition(1, 100, "Hello", "world."),
                Transition(1, 100, "Hello", "world."),
            ],
        )
        await db.remove_markov_data([], [Transition(1, 100, "Hello", "world.")])
        assert await db.get_random_next_word(1, "Hello") == "world."

    async def test_duplicate_removals_decrement_occurrences(self, db: Database) -> None:
        """Duplicate removals in one call decrement counted rows."""
        await db.add_markov_data(
            [
                StartWord(1, 100, "Hello"),
                StartWord(1, 100, "Hello"),
                StartWord(1, 100, "Hello"),
            ],
            [
                Transition(1, 100, "Hello", "world."),
                Transition(1, 100, "Hello", "world."),
                Transition(1, 100, "Hello", "world."),
            ],
        )
        await db.remove_markov_data(
            [StartWord(1, 100, "Hello"), StartWord(1, 100, "Hello")],
            [
                Transition(1, 100, "Hello", "world."),
                Transition(1, 100, "Hello", "world."),
            ],
        )

        async with db._connection.execute(
            "SELECT occurrences FROM markov_start_words"
            " WHERE channel_id = 1 AND user_id = 100 AND word = 'Hello'",
        ) as cursor:
            row = await cursor.fetchone()
        assert row == (1,)
        async with db._connection.execute(
            "SELECT occurrences FROM markov_transitions"
            " WHERE channel_id = 1"
            " AND user_id = 100"
            " AND word = 'Hello'"
            " AND next_word = 'world.'",
        ) as cursor:
            row = await cursor.fetchone()
        assert row == (1,)

    async def test_removes_last_start_word(self, db: Database) -> None:
        """Removing the only start word leaves the table empty for that channel."""
        await db.add_markov_data([StartWord(1, 100, "Hello")], [])
        await db.remove_markov_data([StartWord(1, 100, "Hello")], [])
        assert await db.get_random_start_word(1) is None

    async def test_removes_last_transition(self, db: Database) -> None:
        """Removing the only transition leaves no next word."""
        await db.add_markov_data([], [Transition(1, 100, "Hello", "world.")])
        await db.remove_markov_data([], [Transition(1, 100, "Hello", "world.")])
        assert await db.get_random_next_word(1, "Hello") is None

    async def test_no_match_is_noop(self, db: Database) -> None:
        """Removing a non-existent row does not raise."""
        await db.remove_markov_data(
            [StartWord(1, 100, "nope")],
            [Transition(1, 100, "nope", "nah")],
        )

    async def test_start_word_removal_scoped_to_channel(self, db: Database) -> None:
        """Removing a start word in one channel leaves another channel intact."""
        await db.add_markov_data(
            [StartWord(1, 100, "Hello"), StartWord(2, 200, "Hello")],
            [],
        )
        await db.remove_markov_data([StartWord(1, 100, "Hello")], [])
        assert await db.get_random_start_word(1) is None
        assert await db.get_random_start_word(2) == "Hello"

    async def test_transition_removal_scoped_to_channel(self, db: Database) -> None:
        """Removing a transition in one channel leaves another channel intact."""
        await db.add_markov_data(
            [],
            [
                Transition(1, 100, "Hello", "world."),
                Transition(2, 200, "Hello", "world."),
            ],
        )
        await db.remove_markov_data([], [Transition(1, 100, "Hello", "world.")])
        assert await db.get_random_next_word(1, "Hello") is None
        assert await db.get_random_next_word(2, "Hello") == "world."

    async def test_start_word_removal_scoped_to_user(self, db: Database) -> None:
        """Removing a start word for one user leaves another user."""
        await db.add_markov_data(
            [StartWord(1, 100, "Hello"), StartWord(1, 200, "Hello")],
            [],
        )
        await db.remove_markov_data([StartWord(1, 100, "Hello")], [])
        assert await db.get_random_start_word(1) == "Hello"

    async def test_transition_removal_scoped_to_user(self, db: Database) -> None:
        """Removing a transition for one user leaves another user."""
        await db.add_markov_data(
            [],
            [
                Transition(1, 100, "Hello", "world."),
                Transition(1, 200, "Hello", "world."),
            ],
        )
        await db.remove_markov_data([], [Transition(1, 100, "Hello", "world.")])
        assert await db.get_random_next_word(1, "Hello") == "world."

    async def test_start_word_removal_scoped_to_word(self, db: Database) -> None:
        """Removing one start word leaves a different start word."""
        await db.add_markov_data(
            [StartWord(1, 100, "Hello"), StartWord(1, 100, "World")],
            [],
        )
        await db.remove_markov_data([StartWord(1, 100, "Hello")], [])
        assert await db.get_random_start_word(1) == "World"

    async def test_transition_removal_scoped_to_word(self, db: Database) -> None:
        """Removing one word's transition leaves another word's."""
        await db.add_markov_data(
            [],
            [
                Transition(1, 100, "Hello", "world."),
                Transition(1, 100, "Goodbye", "world."),
            ],
        )
        await db.remove_markov_data([], [Transition(1, 100, "Hello", "world.")])
        assert await db.get_random_next_word(1, "Hello") is None
        assert await db.get_random_next_word(1, "Goodbye") == "world."

    async def test_transition_removal_scoped_to_next_word(self, db: Database) -> None:
        """Removing one next_word leaves a different next_word."""
        await db.add_markov_data(
            [],
            [
                Transition(1, 100, "Hello", "world."),
                Transition(1, 100, "Hello", "friend."),
            ],
        )
        await db.remove_markov_data([], [Transition(1, 100, "Hello", "world.")])
        assert await db.get_random_next_word(1, "Hello") == "friend."

    async def test_empty_lists_is_noop(self, db: Database) -> None:
        """Empty lists do not error."""
        await db.remove_markov_data([], [])


class TestImages:
    """Image URL storage and random retrieval."""

    async def test_add_and_retrieve(self, db: Database) -> None:
        """Inserted image URL can be retrieved by channel."""
        await db.add_images([ChannelImage(1, 100, "https://example.com/cat.png")])
        result = await db.get_random_image(1)
        assert result == "https://example.com/cat.png"

    async def test_returns_none_when_empty(self, db: Database) -> None:
        """Returns None for a channel with no images."""
        result = await db.get_random_image(999)
        assert result is None

    async def test_image_scoped_to_channel(self, db: Database) -> None:
        """An image in one channel is not returned for another channel."""
        await db.add_images([ChannelImage(1, 100, "https://example.com/cat.png")])
        assert await db.get_random_image(2) is None

    async def test_empty_list_is_noop(self, db: Database) -> None:
        """Empty list does not store any data."""
        await db.add_images([])
        assert await db.get_random_image(1) is None

    async def test_image_pooled_across_users(self, db: Database) -> None:
        """Images from different users are visible in the same channel query."""
        await db.add_images(
            [
                ChannelImage(1, 100, "https://example.com/a.png"),
                ChannelImage(1, 200, "https://example.com/b.png"),
            ],
        )
        assert await db.get_random_image(1) in {
            "https://example.com/a.png",
            "https://example.com/b.png",
        }


class TestRemoveImages:
    """Image removal via remove_images."""

    async def test_removes_one_image(self, db: Database) -> None:
        """Removes exactly one matching image row."""
        await db.add_images(
            [
                ChannelImage(1, 100, "https://example.com/a.png"),
                ChannelImage(1, 100, "https://example.com/a.png"),
            ],
        )
        await db.remove_images([ChannelImage(1, 100, "https://example.com/a.png")])
        # One copy should remain.
        assert await db.get_random_image(1) == "https://example.com/a.png"

    async def test_removes_last_image(self, db: Database) -> None:
        """Removing the only image leaves none for that channel."""
        await db.add_images([ChannelImage(1, 100, "https://example.com/a.png")])
        await db.remove_images([ChannelImage(1, 100, "https://example.com/a.png")])
        assert await db.get_random_image(1) is None

    async def test_image_removal_scoped_to_channel(self, db: Database) -> None:
        """Removing an image in one channel leaves another channel intact."""
        await db.add_images(
            [
                ChannelImage(1, 100, "https://example.com/a.png"),
                ChannelImage(2, 200, "https://example.com/a.png"),
            ],
        )
        await db.remove_images([ChannelImage(1, 100, "https://example.com/a.png")])
        assert await db.get_random_image(1) is None
        assert await db.get_random_image(2) == "https://example.com/a.png"

    async def test_image_removal_scoped_to_user(self, db: Database) -> None:
        """Removing an image for one user leaves another user."""
        await db.add_images(
            [
                ChannelImage(1, 100, "https://example.com/a.png"),
                ChannelImage(1, 200, "https://example.com/a.png"),
            ],
        )
        await db.remove_images([ChannelImage(1, 100, "https://example.com/a.png")])
        assert await db.get_random_image(1) == "https://example.com/a.png"

    async def test_image_removal_scoped_to_url(self, db: Database) -> None:
        """Removing one URL leaves a different URL for the same user."""
        await db.add_images(
            [
                ChannelImage(1, 100, "https://example.com/a.png"),
                ChannelImage(1, 100, "https://example.com/b.png"),
            ],
        )
        await db.remove_images([ChannelImage(1, 100, "https://example.com/a.png")])
        assert await db.get_random_image(1) == "https://example.com/b.png"

    async def test_no_match_is_noop(self, db: Database) -> None:
        """Removing a non-existent image does not raise."""
        await db.remove_images([ChannelImage(1, 100, "https://example.com/nope.png")])

    async def test_empty_list_is_noop(self, db: Database) -> None:
        """Empty list does not error."""
        await db.remove_images([])


class TestFlags:
    """Flag CRUD operations on entities."""

    async def test_set_and_check(self, db: Database) -> None:
        """A set flag is reported as set."""
        await db.set_flag(EntityType.CHANNEL, "123", Flag.NO_REPLY)
        assert await db.is_flag_set(EntityType.CHANNEL, "123", Flag.NO_REPLY) is True

    async def test_unset_flag_is_false(self, db: Database) -> None:
        """An unset flag is reported as not set."""
        assert await db.is_flag_set(EntityType.CHANNEL, "123", Flag.NO_REPLY) is False

    async def test_clear_flag(self, db: Database) -> None:
        """A cleared flag is no longer reported as set."""
        await db.set_flag(EntityType.CHANNEL, "123", Flag.NO_REPLY)
        await db.clear_flag(EntityType.CHANNEL, "123", Flag.NO_REPLY)
        assert await db.is_flag_set(EntityType.CHANNEL, "123", Flag.NO_REPLY) is False

    async def test_set_idempotent(self, db: Database) -> None:
        """Setting the same flag twice does not raise."""
        await db.set_flag(EntityType.CHANNEL, "123", Flag.NO_REPLY)
        await db.set_flag(EntityType.CHANNEL, "123", Flag.NO_REPLY)
        assert await db.is_flag_set(EntityType.CHANNEL, "123", Flag.NO_REPLY) is True

    async def test_scoped_to_entity_id(self, db: Database) -> None:
        """A flag set on one entity is not visible on another entity."""
        await db.set_flag(EntityType.CHANNEL, "123", Flag.NO_REPLY)
        assert await db.is_flag_set(EntityType.CHANNEL, "456", Flag.NO_REPLY) is False

    async def test_scoped_to_entity_type(self, db: Database) -> None:
        """A flag set on one entity type is not visible on another."""
        await db.set_flag(EntityType.CHANNEL, "123", Flag.NO_REPLY)
        assert await db.is_flag_set(EntityType.USER, "123", Flag.NO_REPLY) is False

    async def test_scoped_to_flag_name(self, db: Database) -> None:
        """A flag set under one name is not visible under a different name."""
        await db.set_flag(EntityType.CHANNEL, "123", Flag.NO_REPLY)
        assert await db.is_flag_set(EntityType.CHANNEL, "123", Flag.NO_INGEST) is False

    async def test_clear_scoped_to_flag_name(self, db: Database) -> None:
        """Clearing one flag leaves other flags on the same entity intact."""
        await db.set_flag(EntityType.CHANNEL, "123", Flag.NO_REPLY)
        await db.set_flag(EntityType.CHANNEL, "123", Flag.NO_INGEST)
        await db.clear_flag(EntityType.CHANNEL, "123", Flag.NO_REPLY)
        assert await db.is_flag_set(EntityType.CHANNEL, "123", Flag.NO_INGEST) is True

    async def test_clear_scoped_to_entity_id(self, db: Database) -> None:
        """Clearing a flag on one entity leaves another entity."""
        await db.set_flag(EntityType.CHANNEL, "123", Flag.NO_REPLY)
        await db.set_flag(EntityType.CHANNEL, "456", Flag.NO_REPLY)
        await db.clear_flag(EntityType.CHANNEL, "123", Flag.NO_REPLY)
        assert await db.is_flag_set(EntityType.CHANNEL, "456", Flag.NO_REPLY) is True

    async def test_clear_scoped_to_entity_type(self, db: Database) -> None:
        """Clearing a flag on one entity type leaves another type."""
        await db.set_flag(EntityType.CHANNEL, "123", Flag.NO_REPLY)
        await db.set_flag(EntityType.USER, "123", Flag.NO_REPLY)
        await db.clear_flag(EntityType.CHANNEL, "123", Flag.NO_REPLY)
        assert await db.is_flag_set(EntityType.USER, "123", Flag.NO_REPLY) is True

    async def test_clear_unset_flag_is_noop(self, db: Database) -> None:
        """Clearing a flag that was never set does not raise or affect other flags."""
        await db.set_flag(EntityType.CHANNEL, "123", Flag.NO_REPLY)
        await db.clear_flag(EntityType.CHANNEL, "123", Flag.NO_INGEST)
        assert await db.is_flag_set(EntityType.CHANNEL, "123", Flag.NO_REPLY) is True


class TestChannelIngestion:
    """Channel ingestion tracking."""

    async def test_mark_and_check(self, db: Database) -> None:
        """A marked channel is reported as ingested."""
        await db.mark_channel_ingested(42)
        assert await db.is_channel_ingested(42) is True

    async def test_not_ingested_by_default(self, db: Database) -> None:
        """Unmarked channels are not reported as ingested."""
        assert await db.is_channel_ingested(42) is False

    async def test_mark_idempotent(self, db: Database) -> None:
        """Marking the same channel twice does not raise."""
        await db.mark_channel_ingested(42)
        await db.mark_channel_ingested(42)
        assert await db.is_channel_ingested(42) is True

    async def test_scoped_to_channel(self, db: Database) -> None:
        """Marking one channel as ingested does not affect another channel."""
        await db.mark_channel_ingested(42)
        assert await db.is_channel_ingested(99) is False


class TestTransactionRollback:
    """Transaction rolls back all changes on error."""

    async def test_error_rolls_back_insert(self, db: Database) -> None:
        """An error during a transaction prevents partial data from persisting."""

        async def insert_and_fail() -> None:
            async with db._transaction():
                await db._connection.execute(
                    "INSERT INTO markov_start_words"
                    " (channel_id, user_id, word, occurrences)"
                    " VALUES (?, ?, ?, ?)",
                    (1, 100, "should_not_persist", 1),
                )
                msg = "simulated failure"
                raise RuntimeError(msg)

        with pytest.raises(RuntimeError, match="simulated"):
            await insert_and_fail()
        assert await db.get_random_start_word(1) is None


class TestForgetUser:
    """Atomic forget-user transaction across all tables."""

    async def test_deletes_data_and_sets_no_ingest(self, db: Database) -> None:
        """All user data is removed and noIngest flag is set."""
        await db.add_markov_data(
            [StartWord(1, 100, "Hello")],
            [Transition(1, 100, "Hello", "world.")],
        )
        await db.add_images([ChannelImage(1, 100, "https://example.com/a.png")])
        await db.set_flag(EntityType.USER, "100", Flag.ALLOW_PINGS)

        await db.forget_user(100, Flag.NO_INGEST)

        assert await db.get_random_start_word(1) is None
        assert await db.get_random_next_word(1, "Hello") is None
        assert await db.get_random_image(1) is None
        assert await db.is_flag_set(EntityType.USER, "100", Flag.ALLOW_PINGS) is False
        assert await db.is_flag_set(EntityType.USER, "100", Flag.NO_INGEST) is True

    async def test_preserves_other_users(self, db: Database) -> None:
        """Data belonging to other users is not affected."""
        await db.add_markov_data(
            [
                StartWord(1, 100, "Gone"),
                StartWord(1, 200, "Keep"),
                StartWord(1, 200, "Keep"),
            ],
            [
                Transition(1, 100, "Gone", "away."),
                Transition(1, 200, "Keep", "this."),
                Transition(1, 200, "Keep", "this."),
            ],
        )
        await db.add_images(
            [
                ChannelImage(1, 100, "https://example.com/gone.png"),
                ChannelImage(1, 200, "https://example.com/stay.png"),
            ],
        )
        await db.set_flag(EntityType.USER, "200", Flag.ALLOW_PINGS)

        await db.forget_user(100, Flag.NO_INGEST)

        assert await db.get_random_start_word(1) == "Keep"
        assert await db.get_random_next_word(1, "Keep") == "this."
        async with db._connection.execute(
            "SELECT occurrences FROM markov_start_words"
            " WHERE channel_id = 1 AND user_id = 200 AND word = 'Keep'",
        ) as cursor:
            row = await cursor.fetchone()
        assert row == (2,)
        assert await db.get_random_image(1) == "https://example.com/stay.png"
        assert await db.is_flag_set(EntityType.USER, "200", Flag.ALLOW_PINGS) is True

    async def test_preserves_other_entity_type_flags(self, db: Database) -> None:
        """Flags on channels with the same entity ID are not affected."""
        await db.set_flag(EntityType.CHANNEL, "100", Flag.NO_REPLY)
        await db.set_flag(EntityType.USER, "100", Flag.NO_REPLY)

        await db.forget_user(100, Flag.NO_INGEST)

        assert await db.is_flag_set(EntityType.CHANNEL, "100", Flag.NO_REPLY) is True
        assert await db.is_flag_set(EntityType.USER, "100", Flag.NO_REPLY) is False

    async def test_clears_existing_flags_except_no_ingest(self, db: Database) -> None:
        """Existing user flags are cleared but noIngest remains."""
        await db.set_flag(EntityType.USER, "100", Flag.NO_REPLY)
        await db.set_flag(EntityType.USER, "100", Flag.ALLOW_PINGS)

        await db.forget_user(100, Flag.NO_INGEST)

        assert await db.is_flag_set(EntityType.USER, "100", Flag.NO_REPLY) is False
        assert await db.is_flag_set(EntityType.USER, "100", Flag.ALLOW_PINGS) is False
        assert await db.is_flag_set(EntityType.USER, "100", Flag.NO_INGEST) is True

    async def test_deletes_across_channels(self, db: Database) -> None:
        """All user data is removed from every channel."""
        await db.add_markov_data(
            [StartWord(1, 100, "One"), StartWord(2, 100, "Two")],
            [
                Transition(1, 100, "One", "fish."),
                Transition(2, 100, "Two", "fish."),
            ],
        )
        await db.add_images(
            [
                ChannelImage(1, 100, "https://example.com/a.png"),
                ChannelImage(2, 100, "https://example.com/b.png"),
            ],
        )

        await db.forget_user(100, Flag.NO_INGEST)

        assert await db.get_random_start_word(1) is None
        assert await db.get_random_start_word(2) is None
        assert await db.get_random_next_word(1, "One") is None
        assert await db.get_random_next_word(2, "Two") is None
        assert await db.get_random_image(1) is None
        assert await db.get_random_image(2) is None

    async def test_noop_for_nonexistent_user(self, db: Database) -> None:
        """Forgetting a user with no data does not raise."""
        await db.forget_user(999, Flag.NO_INGEST)
        assert await db.is_flag_set(EntityType.USER, "999", Flag.NO_INGEST) is True


class TestWriteDurability:
    """Writes persist across close and reopen."""

    async def test_markov_data_survives_reopen(self, tmp_path: Path) -> None:
        """Data written via add_markov_data is durable after close/reopen."""
        db_path = str(tmp_path / "durability.db")
        db = await Database.connect(db_path)
        try:
            await db.add_markov_data(
                [StartWord(1, 100, "Hello")],
                [Transition(1, 100, "Hello", "world.")],
            )
        finally:
            await db.close()

        db2 = await Database.connect(db_path)
        try:
            assert await db2.get_random_start_word(1) == "Hello"
            assert await db2.get_random_next_word(1, "Hello") == "world."
        finally:
            await db2.close()
