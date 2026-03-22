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

"""Integration tests for the full ingest → uningest cycle."""

from typing import TYPE_CHECKING

import pytest

from crabstero.cache import CachedMessage, IngestCache
from crabstero.markov import ingest, uningest

if TYPE_CHECKING:
    import aiosqlite

    from crabstero.database import Database


async def _snapshot(db: Database) -> dict[str, list[aiosqlite.Row]]:
    """Return sorted rows from all Markov-related tables."""
    tables: dict[str, list[aiosqlite.Row]] = {}
    for table in ("markov_start_words", "markov_transitions", "channel_images"):
        async with db._connection.execute(f"SELECT * FROM {table}") as cursor:  # noqa: S608
            tables[table] = sorted(await cursor.fetchall())
    return tables


class TestIngestUningestCycle:
    """Full round-trip: ingest data, then uningest it completely."""

    async def test_content_round_trip(self, db: Database) -> None:
        """Ingest and uningest content leaves the database clean."""
        await ingest(db, 1, 100, "Hello beautiful world.")
        await uningest(db, 1, 100, "Hello beautiful world.")

        assert await db.get_random_start_word(1) is None
        assert await db.get_random_next_word(1, "Hello") is None
        assert await db.get_random_next_word(1, "beautiful") is None

    async def test_image_round_trip(self, db: Database) -> None:
        """Ingest and uningest an image leaves the database clean."""
        await db.add_images([(1, 100, "https://example.com/cat.png")])
        await db.remove_images([(1, 100, "https://example.com/cat.png")])

        assert await db.get_random_image(1) is None

    def test_cache_round_trip(self) -> None:
        """Cache put then pop returns the original entry."""
        cache = IngestCache()
        entry = CachedMessage(
            channel_id=1,
            user_id=100,
            content="Hello world.",
            embed_texts=["Title"],
            image_urls=["https://example.com/cat.png"],
        )
        cache.put(12345, entry)
        assert cache.pop(12345) is entry
        assert cache.pop(12345) is None

    async def test_multi_sentence_round_trip(self, db: Database) -> None:
        """Multi-sentence ingest and uningest leaves the database clean."""
        text = "Hello world. Goodbye world! How are you?"
        await ingest(db, 1, 100, text)
        await uningest(db, 1, 100, text)

        assert await db.get_random_start_word(1) is None

    async def test_uningest_only_removes_one_copy(self, db: Database) -> None:
        """Uningesting once preserves data from a second identical ingest."""
        await ingest(db, 1, 100, "Hello world.")
        await ingest(db, 1, 100, "Hello world.")
        await uningest(db, 1, 100, "Hello world.")

        # One copy of each row should remain.
        assert await db.get_random_start_word(1) == "Hello"
        assert await db.get_random_next_word(1, "Hello") == "world."

    async def test_uningest_does_not_affect_other_channels(self, db: Database) -> None:
        """Uningesting from one channel leaves another channel's data intact."""
        await ingest(db, 1, 100, "Hello world.")
        await ingest(db, 2, 100, "Hello world.")
        await uningest(db, 1, 100, "Hello world.")

        assert await db.get_random_start_word(1) is None
        assert await db.get_random_start_word(2) == "Hello"
        assert await db.get_random_next_word(2, "Hello") == "world."


class TestUningestRestoresState:
    """Uningest restores the database to its prior state."""

    @pytest.mark.parametrize(
        "text",
        [
            pytest.param("Hello world.", id="simple-sentence"),
            pytest.param("Hello world", id="missing-punctuation"),
            pytest.param(
                "Hello world. Goodbye world! How are you?",
                id="multi-sentence",
            ),
            pytest.param("One.", id="single-word"),
            pytest.param(
                "Lots of extra   spaces\nand\nnewlines   here.",
                id="whitespace-normalization",
            ),
        ],
    )
    async def test_uningest_restores_empty_db(self, db: Database, text: str) -> None:
        """Ingest then uningest on an empty database leaves all tables empty."""
        before = await _snapshot(db)
        await ingest(db, 1, 100, text)
        await uningest(db, 1, 100, text)
        after = await _snapshot(db)
        assert after == before

    @pytest.mark.parametrize(
        "text",
        [
            pytest.param("Hello world.", id="simple-sentence"),
            pytest.param(
                "Hello world. Goodbye world! How are you?",
                id="multi-sentence",
            ),
        ],
    )
    async def test_uningest_restores_preexisting_data(
        self, db: Database, text: str
    ) -> None:
        """Ingest then uningest preserves unrelated pre-existing data exactly."""
        await ingest(db, 99, 200, "Pre-existing data stays safe.")
        await db.add_images([(99, 200, "https://example.com/existing.png")])

        before = await _snapshot(db)
        await ingest(db, 1, 100, text)
        await uningest(db, 1, 100, text)
        after = await _snapshot(db)
        assert after == before
