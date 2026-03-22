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
transition CRUD, image storage, flag CRUD, and channel ingestion tracking.
"""

from typing import TYPE_CHECKING

import pytest

from crabstero.database import Database

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


class TestAddMarkovData:
    """Markov start word and transition storage via add_markov_data."""

    async def test_stores_start_word(self, db: Database) -> None:
        """Inserted start word can be retrieved by channel."""
        await db.add_markov_data([(1, 100, "Hello")], [])
        result = await db.get_random_start_word(1)
        assert result == "Hello"

    async def test_stores_transition(self, db: Database) -> None:
        """Inserted transition can be retrieved by channel and word."""
        await db.add_markov_data([], [(1, 100, "Hello", "world.")])
        result = await db.get_random_next_word(1, "Hello")
        assert result == "world."

    async def test_stores_both_in_single_call(self, db: Database) -> None:
        """Start words and transitions are stored in a single call."""
        await db.add_markov_data(
            [(1, 100, "Hello")],
            [(1, 100, "Hello", "world.")],
        )
        assert await db.get_random_start_word(1) == "Hello"
        assert await db.get_random_next_word(1, "Hello") == "world."

    async def test_empty_lists_store_nothing(self, db: Database) -> None:
        """Empty lists do not store any data."""
        await db.add_markov_data([], [])
        assert await db.get_random_start_word(1) is None

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
        self, db: Database, completing_word: str
    ) -> None:
        """get_random_completing_next_word only returns sentence-ending words."""
        await db.add_markov_data(
            [],
            [
                (1, 100, "Hello", "beautiful"),
                (1, 100, "Hello", completing_word),
            ],
        )
        for _ in range(100):
            result = await db.get_random_completing_next_word(1, "Hello")
            assert result == completing_word

    async def test_completing_returns_none_without_match(self, db: Database) -> None:
        """Returns None when no transitions end with sentence punctuation."""
        await db.add_markov_data([], [(1, 100, "Hello", "beautiful")])
        result = await db.get_random_completing_next_word(1, "Hello")
        assert result is None


class TestMarkovReadMethods:
    """Read behavior when no data has been stored."""

    async def test_returns_none_when_empty(self, db: Database) -> None:
        """Returns None for channels with no data."""
        assert await db.get_random_start_word(999) is None
        assert await db.get_random_next_word(1, "nonexistent") is None


class TestImages:
    """Image URL storage and random retrieval."""

    async def test_add_and_retrieve(self, db: Database) -> None:
        """Inserted image URL can be retrieved by channel."""
        await db.add_images([(1, 100, "https://example.com/cat.png")])
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
        assert await db.is_flag_set("channel", "123", "noReply") is True

    async def test_unset_flag_is_false(self, db: Database) -> None:
        """An unset flag is reported as not set."""
        assert await db.is_flag_set("channel", "123", "noReply") is False

    async def test_clear_flag(self, db: Database) -> None:
        """A cleared flag is no longer reported as set."""
        await db.set_flag("channel", "123", "noReply")
        await db.clear_flag("channel", "123", "noReply")
        assert await db.is_flag_set("channel", "123", "noReply") is False

    async def test_set_idempotent(self, db: Database) -> None:
        """Setting the same flag twice does not raise."""
        await db.set_flag("channel", "123", "noReply")
        await db.set_flag("channel", "123", "noReply")
        assert await db.is_flag_set("channel", "123", "noReply") is True


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


class TestRemoveMarkovData:
    """Markov data removal via remove_markov_data."""

    async def test_removes_one_start_word(self, db: Database) -> None:
        """Removes exactly one matching start word row."""
        await db.add_markov_data([(1, 100, "Hello"), (1, 100, "Hello")], [])
        await db.remove_markov_data([(1, 100, "Hello")], [])
        # One copy should remain.
        assert await db.get_random_start_word(1) == "Hello"

    async def test_removes_one_transition(self, db: Database) -> None:
        """Removes exactly one matching transition row."""
        await db.add_markov_data(
            [], [(1, 100, "Hello", "world."), (1, 100, "Hello", "world.")]
        )
        await db.remove_markov_data([], [(1, 100, "Hello", "world.")])
        assert await db.get_random_next_word(1, "Hello") == "world."

    async def test_removes_last_start_word(self, db: Database) -> None:
        """Removing the only start word leaves the table empty for that channel."""
        await db.add_markov_data([(1, 100, "Hello")], [])
        await db.remove_markov_data([(1, 100, "Hello")], [])
        assert await db.get_random_start_word(1) is None

    async def test_removes_last_transition(self, db: Database) -> None:
        """Removing the only transition leaves no next word."""
        await db.add_markov_data([], [(1, 100, "Hello", "world.")])
        await db.remove_markov_data([], [(1, 100, "Hello", "world.")])
        assert await db.get_random_next_word(1, "Hello") is None

    async def test_no_match_is_noop(self, db: Database) -> None:
        """Removing a non-existent row does not raise."""
        await db.remove_markov_data([(1, 100, "nope")], [(1, 100, "nope", "nah")])

    async def test_start_word_removal_scoped_to_channel(self, db: Database) -> None:
        """Removing a start word in one channel leaves another channel intact."""
        await db.add_markov_data([(1, 100, "Hello"), (2, 200, "Hello")], [])
        await db.remove_markov_data([(1, 100, "Hello")], [])
        assert await db.get_random_start_word(1) is None
        assert await db.get_random_start_word(2) == "Hello"

    async def test_transition_removal_scoped_to_channel(self, db: Database) -> None:
        """Removing a transition in one channel leaves another channel intact."""
        await db.add_markov_data(
            [], [(1, 100, "Hello", "world."), (2, 200, "Hello", "world.")]
        )
        await db.remove_markov_data([], [(1, 100, "Hello", "world.")])
        assert await db.get_random_next_word(1, "Hello") is None
        assert await db.get_random_next_word(2, "Hello") == "world."

    async def test_empty_lists_is_noop(self, db: Database) -> None:
        """Empty lists do not error."""
        await db.remove_markov_data([], [])


class TestRemoveImages:
    """Image removal via remove_images."""

    async def test_removes_one_image(self, db: Database) -> None:
        """Removes exactly one matching image row."""
        await db.add_images(
            [
                (1, 100, "https://example.com/a.png"),
                (1, 100, "https://example.com/a.png"),
            ]
        )
        await db.remove_images([(1, 100, "https://example.com/a.png")])
        # One copy should remain.
        assert await db.get_random_image(1) == "https://example.com/a.png"

    async def test_removes_last_image(self, db: Database) -> None:
        """Removing the only image leaves none for that channel."""
        await db.add_images([(1, 100, "https://example.com/a.png")])
        await db.remove_images([(1, 100, "https://example.com/a.png")])
        assert await db.get_random_image(1) is None

    async def test_image_removal_scoped_to_channel(self, db: Database) -> None:
        """Removing an image in one channel leaves another channel intact."""
        await db.add_images(
            [
                (1, 100, "https://example.com/a.png"),
                (2, 200, "https://example.com/a.png"),
            ]
        )
        await db.remove_images([(1, 100, "https://example.com/a.png")])
        assert await db.get_random_image(1) is None
        assert await db.get_random_image(2) == "https://example.com/a.png"

    async def test_no_match_is_noop(self, db: Database) -> None:
        """Removing a non-existent image does not raise."""
        await db.remove_images([(1, 100, "https://example.com/nope.png")])

    async def test_empty_list_is_noop(self, db: Database) -> None:
        """Empty list does not error."""
        await db.remove_images([])


class TestWriteDurability:
    """Writes persist across close and reopen."""

    async def test_markov_data_survives_reopen(self, tmp_path: Path) -> None:
        """Data written via add_markov_data is durable after close/reopen."""
        db_path = str(tmp_path / "durability.db")
        db = await Database.connect(db_path)
        await db.add_markov_data([(1, 100, "Hello")], [(1, 100, "Hello", "world.")])
        await db.close()

        db2 = await Database.connect(db_path)
        assert await db2.get_random_start_word(1) == "Hello"
        assert await db2.get_random_next_word(1, "Hello") == "world."
        await db2.close()
