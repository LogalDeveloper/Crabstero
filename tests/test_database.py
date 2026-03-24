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
        self, db: Database, completing_word: str
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
            [StartWord(1, 100, "Hello"), StartWord(1, 200, "Goodbye")], []
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


class TestRemoveMarkovData:
    """Markov data removal via remove_markov_data."""

    async def test_removes_one_start_word(self, db: Database) -> None:
        """Removes exactly one matching start word row."""
        await db.add_markov_data(
            [StartWord(1, 100, "Hello"), StartWord(1, 100, "Hello")], []
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
            [StartWord(1, 100, "Hello"), StartWord(2, 200, "Hello")], []
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
            [StartWord(1, 100, "Hello"), StartWord(1, 200, "Hello")], []
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
            [StartWord(1, 100, "Hello"), StartWord(1, 100, "World")], []
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
            ]
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
            ]
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
            ]
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
            ]
        )
        await db.remove_images([ChannelImage(1, 100, "https://example.com/a.png")])
        assert await db.get_random_image(1) == "https://example.com/a.png"

    async def test_image_removal_scoped_to_url(self, db: Database) -> None:
        """Removing one URL leaves a different URL for the same user."""
        await db.add_images(
            [
                ChannelImage(1, 100, "https://example.com/a.png"),
                ChannelImage(1, 100, "https://example.com/b.png"),
            ]
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
        with pytest.raises(RuntimeError, match="simulated"):
            await self._insert_and_fail(db)
        assert await db.get_random_start_word(1) is None

    @staticmethod
    async def _insert_and_fail(db: Database) -> None:
        async with db._transaction():
            await db._connection.execute(
                "INSERT INTO markov_start_words"
                " (channel_id, user_id, word)"
                " VALUES (?, ?, ?)",
                (1, 100, "should_not_persist"),
            )
            raise RuntimeError("simulated failure")


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
            [StartWord(1, 100, "Gone"), StartWord(1, 200, "Keep")],
            [
                Transition(1, 100, "Gone", "away."),
                Transition(1, 200, "Keep", "this."),
            ],
        )
        await db.add_images(
            [
                ChannelImage(1, 100, "https://example.com/gone.png"),
                ChannelImage(1, 200, "https://example.com/stay.png"),
            ]
        )
        await db.set_flag(EntityType.USER, "200", Flag.ALLOW_PINGS)

        await db.forget_user(100, Flag.NO_INGEST)

        assert await db.get_random_start_word(1) == "Keep"
        assert await db.get_random_next_word(1, "Keep") == "this."
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
            ]
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
        await db.add_markov_data(
            [StartWord(1, 100, "Hello")],
            [Transition(1, 100, "Hello", "world.")],
        )
        await db.close()

        db2 = await Database.connect(db_path)
        assert await db2.get_random_start_word(1) == "Hello"
        assert await db2.get_random_next_word(1, "Hello") == "world."
        await db2.close()
