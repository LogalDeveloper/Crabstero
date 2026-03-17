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

"""Unit tests for the Database class.

Tests cover Database.connect (pragmas, schema), markov start word and
transition CRUD, image storage, flag CRUD, channel ingestion tracking,
and the auto-commit write-batching mechanism.
"""

import asyncio
from typing import TYPE_CHECKING

import pytest

from crabstero.database import AUTO_COMMIT_WRITE_THRESHOLD, Database

if TYPE_CHECKING:
    from pathlib import Path


class TestConnect:
    """Database.connect creates a configured SQLite database."""

    async def test_wal_mode(self, db: Database) -> None:
        """Journal mode is set to WAL."""
        async with db._connection.execute("PRAGMA journal_mode") as cursor:
            row = await cursor.fetchone()
        assert row is not None
        assert row[0] == "wal"

    async def test_synchronous_normal(self, db: Database) -> None:
        """Synchronous mode is set to NORMAL."""
        async with db._connection.execute("PRAGMA synchronous") as cursor:
            row = await cursor.fetchone()
        assert row is not None
        assert row[0] == 1

    async def test_schema_creates_tables(self, db: Database) -> None:
        """All expected tables exist after connect."""
        async with db._connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name"
        ) as cursor:
            tables = [row[0] for row in await cursor.fetchall()]
        assert tables == [
            "channel_images",
            "flags",
            "ingested_channels",
            "markov_start_words",
            "markov_transitions",
        ]


class TestMarkovStartWords:
    """Start word storage and random retrieval."""

    async def test_add_and_retrieve(self, db: Database) -> None:
        """Inserted start word can be retrieved by channel."""
        await db.add_start_words_batch([(1, 100, "Hello")])
        await db.commit()
        result = await db.get_random_start_word(1)
        assert result == "Hello"

    async def test_returns_none_when_empty(self, db: Database) -> None:
        """Returns None for a channel with no start words."""
        result = await db.get_random_start_word(999)
        assert result is None

    async def test_empty_batch_stores_nothing(self, db: Database) -> None:
        """An empty batch does not store any start words."""
        await db.add_start_words_batch([])
        await db.commit()
        assert await db.get_random_start_word(1) is None


class TestMarkovTransitions:
    """Word transition storage and random retrieval."""

    async def test_add_and_retrieve(self, db: Database) -> None:
        """Inserted transition can be retrieved by channel and word."""
        await db.add_transitions_batch([(1, 100, "Hello", "world.")])
        await db.commit()
        result = await db.get_random_next_word(1, "Hello")
        assert result == "world."

    async def test_returns_none_when_empty(self, db: Database) -> None:
        """Returns None when no transitions exist for the word."""
        result = await db.get_random_next_word(1, "nonexistent")
        assert result is None

    @pytest.mark.parametrize(
        "completing_word",
        [
            pytest.param("world.", id="period"),
            pytest.param("world!", id="exclamation"),
            pytest.param("world?", id="question"),
            pytest.param("world§", id="section-sign"),
        ],
    )
    async def test_completing_word_filters_punctuation(
        self, db: Database, completing_word: str
    ) -> None:
        """get_random_completing_next_word only returns sentence-ending words."""
        await db.add_transitions_batch(
            [
                (1, 100, "Hello", "beautiful"),
                (1, 100, "Hello", completing_word),
            ]
        )
        await db.commit()
        for _ in range(100):
            result = await db.get_random_completing_next_word(1, "Hello")
            assert result == completing_word

    async def test_completing_returns_none_without_match(self, db: Database) -> None:
        """Returns None when no transitions end with sentence punctuation."""
        await db.add_transitions_batch([(1, 100, "Hello", "beautiful")])
        await db.commit()
        result = await db.get_random_completing_next_word(1, "Hello")
        assert result is None

    async def test_empty_batch_stores_nothing(self, db: Database) -> None:
        """An empty batch does not store any transitions."""
        await db.add_transitions_batch([])
        await db.commit()
        assert await db.get_random_next_word(1, "Hello") is None


class TestImages:
    """Image URL storage and random retrieval."""

    async def test_add_and_retrieve(self, db: Database) -> None:
        """Inserted image URL can be retrieved by channel."""
        await db.add_image(1, 100, "https://example.com/cat.png")
        await db.commit()
        result = await db.get_random_image(1)
        assert result == "https://example.com/cat.png"

    async def test_returns_none_when_empty(self, db: Database) -> None:
        """Returns None for a channel with no images."""
        result = await db.get_random_image(999)
        assert result is None


class TestFlags:
    """Flag CRUD operations on entities."""

    async def test_set_and_check(self, db: Database) -> None:
        """A set flag is reported as set."""
        await db.set_flag("channel", "123", "noReply")
        await db.commit()
        assert await db.is_flag_set("channel", "123", "noReply") is True

    async def test_unset_flag_is_false(self, db: Database) -> None:
        """An unset flag is reported as not set."""
        assert await db.is_flag_set("channel", "123", "noReply") is False

    async def test_clear_flag(self, db: Database) -> None:
        """A cleared flag is no longer reported as set."""
        await db.set_flag("channel", "123", "noReply")
        await db.commit()
        await db.clear_flag("channel", "123", "noReply")
        await db.commit()
        assert await db.is_flag_set("channel", "123", "noReply") is False

    async def test_set_idempotent(self, db: Database) -> None:
        """Setting the same flag twice does not raise."""
        await db.set_flag("channel", "123", "noReply")
        await db.set_flag("channel", "123", "noReply")
        await db.commit()
        assert await db.is_flag_set("channel", "123", "noReply") is True


class TestChannelIngestion:
    """Channel ingestion tracking."""

    async def test_mark_and_check(self, db: Database) -> None:
        """A marked channel is reported as ingested."""
        await db.mark_channel_ingested(42)
        await db.commit()
        assert await db.is_channel_ingested(42) is True

    async def test_not_ingested_by_default(self, db: Database) -> None:
        """Unmarked channels are not reported as ingested."""
        assert await db.is_channel_ingested(42) is False

    async def test_mark_idempotent(self, db: Database) -> None:
        """Marking the same channel twice does not raise."""
        await db.mark_channel_ingested(42)
        await db.mark_channel_ingested(42)
        await db.commit()
        assert await db.is_channel_ingested(42) is True


class TestAutoCommit:
    """Write batching and automatic commit behavior."""

    async def test_commits_after_threshold(self, db: Database) -> None:
        """Pending writes reset to zero after reaching the write threshold."""
        for i in range(AUTO_COMMIT_WRITE_THRESHOLD):
            await db.add_start_words_batch([(1, 100, f"word{i}")])
        assert db._pending_writes == 0
        assert await db.get_random_start_word(1) is not None

    async def test_close_commits_pending(self, tmp_path: Path) -> None:
        """close() commits any pending writes before closing."""
        db_path = str(tmp_path / "close_test.db")
        db = await Database.connect(db_path)
        await db.add_start_words_batch([(1, 100, "hello")])
        assert db._pending_writes > 0
        await db.close()

        # Reopen and verify data persisted.
        db2 = await Database.connect(db_path)
        result = await db2.get_random_start_word(1)
        await db2.close()
        assert result == "hello"

    async def test_close_without_pending_writes(self, tmp_path: Path) -> None:
        """close() succeeds when there are no pending writes."""
        db = await Database.connect(str(tmp_path / "clean_close.db"))
        await db.close()

    async def test_flush_timer_commits(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Background flush timer commits pending writes after timeout."""
        monkeypatch.setattr("crabstero.database.AUTO_COMMIT_TIMEOUT_SECONDS", 0.05)
        db = await Database.connect(str(tmp_path / "timer_test.db"))
        await db.add_start_words_batch([(1, 100, "hello")])
        assert db._pending_writes > 0
        await asyncio.sleep(0.1)
        assert db._pending_writes == 0
        await db.close()
