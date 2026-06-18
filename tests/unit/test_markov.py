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

"""Unit tests for Markov chain ingestion and generation.

Tests cover is_complete_sentence, _ingest_sentence, ingest, and generate
from crabstero.markov without crossing the SQLite database boundary.
"""

import contextlib

import pytest

from crabstero.database import Database, StartWord, Transition
from crabstero.markov import (
    DEFAULT_SENTENCE_END,
    _ingest_sentence,
    _uningest_sentence,
    generate,
    ingest,
    is_complete_sentence,
    uningest,
)


class _FakeMarkovDatabase(Database):
    """In-memory Markov store with deterministic first-row selection.

    The fake preserves duplicate rows and one-row-at-a-time removal semantics
    so ingestion and uningestion tests can stay below the SQLite boundary.
    """

    def __init__(self) -> None:
        self.start_words: list[StartWord] = []
        self.transitions: list[Transition] = []

    async def add_markov_data(
        self,
        start_words: list[StartWord],
        transitions: list[Transition],
    ) -> None:
        """Record Markov rows exactly as the application would write them."""
        self.start_words.extend(start_words)
        self.transitions.extend(transitions)

    async def remove_markov_data(
        self,
        start_words: list[StartWord],
        transitions: list[Transition],
    ) -> None:
        """Remove at most one matching row per requested Markov row."""
        for start_word in start_words:
            with contextlib.suppress(ValueError):
                self.start_words.remove(start_word)
        for transition in transitions:
            with contextlib.suppress(ValueError):
                self.transitions.remove(transition)

    async def get_random_start_word(self, channel_id: int) -> str | None:
        """Return the first start word for deterministic generation tests."""
        return next(
            (row.word for row in self.start_words if row.channel_id == channel_id),
            None,
        )

    async def get_random_next_word(self, channel_id: int, word: str) -> str | None:
        """Return the first matching transition for deterministic tests."""
        return next(
            (
                row.next_word
                for row in self.transitions
                if row.channel_id == channel_id and row.word == word
            ),
            None,
        )

    async def get_random_completing_next_word(
        self,
        channel_id: int,
        word: str,
    ) -> str | None:
        """Return the first sentence-ending transition for deterministic tests."""
        return next(
            (
                row.next_word
                for row in self.transitions
                if row.channel_id == channel_id
                and row.word == word
                and is_complete_sentence(row.next_word)
            ),
            None,
        )


@pytest.fixture
def markov_db() -> _FakeMarkovDatabase:
    """Return an isolated deterministic Markov store for one test."""
    return _FakeMarkovDatabase()


class TestIsCompleteSentence:
    """Sentence completeness detection."""

    @pytest.mark.parametrize(
        ("sentence", "expected"),
        [
            pytest.param("Hello.", True, id="period"),
            pytest.param("Wow!", True, id="exclamation"),
            pytest.param("Really?", True, id="question"),
            pytest.param(f"Hello{DEFAULT_SENTENCE_END}", True, id="section-sign"),
            pytest.param("Hello", False, id="no-punctuation"),
            pytest.param("", False, id="empty-string"),
            pytest.param("Hello. ", False, id="trailing-space"),
            pytest.param("Hello,", False, id="comma"),
        ],
    )
    def test_detection(self, sentence: str, expected: bool) -> None:  # noqa: FBT001
        """Correctly identifies sentence completeness."""
        assert is_complete_sentence(sentence) is expected


class TestIngestSentence:
    """Single sentence ingestion into the Markov chain."""

    async def test_stores_start_word(self, markov_db: _FakeMarkovDatabase) -> None:
        """First word of the sentence is stored as a start word."""
        await _ingest_sentence(markov_db, 1, 100, "Hello world.")

        assert markov_db.start_words == [StartWord(1, 100, "Hello")]

    async def test_stores_transitions(self, markov_db: _FakeMarkovDatabase) -> None:
        """Adjacent words create transitions."""
        await _ingest_sentence(markov_db, 1, 100, "Hello world.")

        assert markov_db.transitions == [Transition(1, 100, "Hello", "world.")]

    async def test_appends_sentence_end_if_missing(
        self,
        markov_db: _FakeMarkovDatabase,
    ) -> None:
        """Unpunctuated sentence gets the default sentence-end marker."""
        await _ingest_sentence(markov_db, 1, 100, "Hello world")

        assert markov_db.transitions == [
            Transition(1, 100, "Hello", f"world{DEFAULT_SENTENCE_END}"),
        ]

    async def test_preserves_existing_punctuation(
        self,
        markov_db: _FakeMarkovDatabase,
    ) -> None:
        """Already-punctuated sentence keeps its terminator."""
        await _ingest_sentence(markov_db, 1, 100, "Hello world!")

        assert markov_db.transitions == [Transition(1, 100, "Hello", "world!")]

    async def test_single_word_stores_nothing(
        self,
        markov_db: _FakeMarkovDatabase,
    ) -> None:
        """A single-word sentence produces no start words or transitions."""
        await _ingest_sentence(markov_db, 1, 100, "Hello.")

        assert markov_db.start_words == []
        assert markov_db.transitions == []

    async def test_stores_all_transitions(
        self,
        markov_db: _FakeMarkovDatabase,
    ) -> None:
        """All adjacent word pairs create transitions."""
        await _ingest_sentence(markov_db, 1, 100, "A B C.")

        assert markov_db.transitions == [
            Transition(1, 100, "A", "B"),
            Transition(1, 100, "B", "C."),
        ]


class TestIngestParagraph:
    """Paragraph ingestion splits into sentences."""

    async def test_single_sentence(self, markov_db: _FakeMarkovDatabase) -> None:
        """A single sentence paragraph is ingested."""
        await ingest(markov_db, 1, 100, "Hello world.")

        assert markov_db.start_words == [StartWord(1, 100, "Hello")]

    async def test_multiple_sentences(self, markov_db: _FakeMarkovDatabase) -> None:
        """Multiple sentences are split and ingested individually."""
        await ingest(markov_db, 1, 100, "Hello world. Goodbye world!")

        assert {row.word for row in markov_db.start_words} == {"Hello", "Goodbye"}

    async def test_normalizes_whitespace(
        self,
        markov_db: _FakeMarkovDatabase,
    ) -> None:
        """Extra spaces and newlines are collapsed."""
        await ingest(markov_db, 1, 100, "Hello   world.\nGoodbye  world!")

        assert Transition(1, 100, "Hello", "world.") in markov_db.transitions
        assert Transition(1, 100, "Goodbye", "world!") in markov_db.transitions

    async def test_appends_default_end(self, markov_db: _FakeMarkovDatabase) -> None:
        """Unpunctuated paragraph gets the default sentence-end marker."""
        await ingest(markov_db, 1, 100, "Hello world")

        assert markov_db.transitions == [
            Transition(1, 100, "Hello", f"world{DEFAULT_SENTENCE_END}"),
        ]

    @pytest.mark.parametrize(
        "paragraph",
        [
            pytest.param("", id="empty-string"),
            pytest.param("   ", id="whitespace-only"),
        ],
    )
    async def test_empty_input_stores_nothing(
        self,
        markov_db: _FakeMarkovDatabase,
        paragraph: str,
    ) -> None:
        """Empty or whitespace-only input does not store any data."""
        await ingest(markov_db, 1, 100, paragraph)

        assert markov_db.start_words == []
        assert markov_db.transitions == []

    async def test_splits_on_punctuation_followed_by_space(
        self,
        markov_db: _FakeMarkovDatabase,
    ) -> None:
        """Punctuation followed by a space splits into separate sentences."""
        await ingest(markov_db, 1, 100, "Dr. Smith likes cats")

        # "Dr." splits off as a single-word sentence (stores nothing).
        # "Smith likes cats" becomes a sentence with "Smith" as start word.
        assert markov_db.start_words == [StartWord(1, 100, "Smith")]

    async def test_no_split_without_space_after_punctuation(
        self,
        markov_db: _FakeMarkovDatabase,
    ) -> None:
        """Punctuation not followed by a space keeps words together."""
        await ingest(markov_db, 1, 100, "Hello.World is here")

        assert markov_db.start_words == [StartWord(1, 100, "Hello.World")]


class TestGenerate:
    """Markov chain text generation."""

    async def test_fallback_on_empty_channel(
        self,
        markov_db: _FakeMarkovDatabase,
    ) -> None:
        """Returns an informational message when the channel has no data."""
        result = await generate(markov_db, 1)
        expected = (
            "I do not have enough data to generate a message yet."
            " Chat a bit more so I can learn how this channel talks."
        )
        assert result == expected

    async def test_generates_from_ingested_data(
        self,
        markov_db: _FakeMarkovDatabase,
    ) -> None:
        """Generated text uses words from ingested data."""
        await ingest(markov_db, 1, 100, "The quick brown fox.")

        result = await generate(markov_db, 1)
        assert result == "The quick brown fox."

    async def test_strips_section_sign(self, markov_db: _FakeMarkovDatabase) -> None:
        """The internal section sign marker never appears in output."""
        await ingest(markov_db, 1, 100, "Hello world")

        result = await generate(markov_db, 1)
        assert result == "Hello world"

    async def test_respects_hard_limit(
        self,
        markov_db: _FakeMarkovDatabase,
    ) -> None:
        """Output is truncated at hard_limit."""
        words = [f"w{i}" for i in range(100)]
        text = " ".join(words)
        await ingest(markov_db, 1, 100, text)

        result = await generate(markov_db, 1, soft_limit=10, hard_limit=49)
        assert result == "w0 w1 w2 w3 w4 w5 w6 w7 w8 w9 w10 w11 w12 w13 w14"

    async def test_prefers_completing_word_after_soft_limit(
        self,
        markov_db: _FakeMarkovDatabase,
    ) -> None:
        """After soft_limit, generation prefers completing words."""
        # Chain: A -> B -> C -> D -> {E, "end."}
        # A, B, C have only non-completing transitions, so past the soft limit
        # the loop falls back to get_random_next_word for each. D has both a
        # continuing ("E") and completing ("end.") transition, so
        # get_random_completing_next_word deterministically picks "end.".
        await markov_db.add_markov_data(
            [StartWord(1, 100, "A")],
            [
                Transition(1, 100, "A", "B"),
                Transition(1, 100, "B", "C"),
                Transition(1, 100, "C", "D"),
                Transition(1, 100, "D", "E"),
                Transition(1, 100, "D", "F"),
                Transition(1, 100, "D", "G"),
                Transition(1, 100, "D", "H"),
                Transition(1, 100, "D", "end."),
            ],
        )

        result = await generate(markov_db, 1, soft_limit=1, hard_limit=1000)
        assert result == "A B C D end."

    async def test_start_word_already_ends_sentence(
        self,
        markov_db: _FakeMarkovDatabase,
    ) -> None:
        """Generation stops immediately when the start word is sentence-ending."""
        await markov_db.add_markov_data([StartWord(1, 100, "Yes.")], [])

        result = await generate(markov_db, 1)
        assert result == "Yes."

    async def test_chain_dead_end(self, markov_db: _FakeMarkovDatabase) -> None:
        """Generation stops when no next word exists (dead-end chain)."""
        await markov_db.add_markov_data([StartWord(1, 100, "Hello")], [])

        result = await generate(markov_db, 1)
        assert result == "Hello"

    async def test_hard_limit_strips_section_sign(
        self,
        markov_db: _FakeMarkovDatabase,
    ) -> None:
        """Section sign at the truncation boundary is stripped."""
        await markov_db.add_markov_data(
            [StartWord(1, 100, "A")],
            [
                Transition(1, 100, "A", "B"),
                Transition(1, 100, "B", f"C{DEFAULT_SENTENCE_END}"),
            ],
        )

        result = await generate(markov_db, 1, soft_limit=100, hard_limit=6)
        assert result == "A B C"

    async def test_soft_limit_falls_back_to_regular_next_word(
        self,
        markov_db: _FakeMarkovDatabase,
    ) -> None:
        """After soft_limit, falls back when no completing word exists."""
        await markov_db.add_markov_data(
            [StartWord(1, 100, "A")],
            [Transition(1, 100, "A", "B")],
        )

        result = await generate(markov_db, 1, soft_limit=1, hard_limit=1000)
        assert result == "A B"

    async def test_hard_limit_truncates_mid_word(
        self,
        markov_db: _FakeMarkovDatabase,
    ) -> None:
        """Hard limit slices output even when it falls inside a word."""
        await markov_db.add_markov_data(
            [StartWord(1, 100, "AB")],
            [Transition(1, 100, "AB", "CDEF")],
        )

        result = await generate(markov_db, 1, soft_limit=100, hard_limit=5)
        assert result == "AB CD"

    async def test_channel_isolation(self, markov_db: _FakeMarkovDatabase) -> None:
        """Data ingested into one channel does not leak into another."""
        await ingest(markov_db, 1, 100, "Channel one data.")
        await ingest(markov_db, 2, 100, "Channel two data.")

        result = await generate(markov_db, 3)
        expected = (
            "I do not have enough data to generate a message yet."
            " Chat a bit more so I can learn how this channel talks."
        )
        assert result == expected


class TestUningestSentence:
    """Single sentence uningest from the Markov chain."""

    async def test_removes_start_word_and_transition(
        self,
        markov_db: _FakeMarkovDatabase,
    ) -> None:
        """Uningest removes the start word and transition added by ingest."""
        await _ingest_sentence(markov_db, 1, 100, "Hello world.")
        await _uningest_sentence(markov_db, 1, 100, "Hello world.")
        assert markov_db.start_words == []
        assert markov_db.transitions == []

    async def test_preserves_other_data(
        self,
        markov_db: _FakeMarkovDatabase,
    ) -> None:
        """Uningest only removes data for the specified sentence."""
        await _ingest_sentence(markov_db, 1, 100, "Hello world.")
        await _ingest_sentence(markov_db, 1, 100, "Goodbye world.")
        await _uningest_sentence(markov_db, 1, 100, "Hello world.")
        assert markov_db.start_words == [StartWord(1, 100, "Goodbye")]
        assert markov_db.transitions == [Transition(1, 100, "Goodbye", "world.")]

    async def test_handles_missing_punctuation(
        self,
        markov_db: _FakeMarkovDatabase,
    ) -> None:
        """Uningest appends the default sentence end, matching ingest behavior."""
        await _ingest_sentence(markov_db, 1, 100, "Hello world")
        await _uningest_sentence(markov_db, 1, 100, "Hello world")
        assert markov_db.start_words == []
        assert markov_db.transitions == []

    async def test_single_word_is_noop(self, markov_db: _FakeMarkovDatabase) -> None:
        """Uningesting a single-word sentence does not error."""
        await _ingest_sentence(markov_db, 1, 100, "Hello.")
        await _uningest_sentence(markov_db, 1, 100, "Hello.")
        assert markov_db.start_words == []
        assert markov_db.transitions == []


class TestUningestParagraph:
    """Paragraph-level uningest."""

    async def test_uningest_multiple_sentences(
        self,
        markov_db: _FakeMarkovDatabase,
    ) -> None:
        """Uningest reverses a multi-sentence paragraph."""
        await ingest(markov_db, 1, 100, "Hello world. Goodbye world!")
        await uningest(markov_db, 1, 100, "Hello world. Goodbye world!")
        assert markov_db.start_words == []
        assert markov_db.transitions == []

    async def test_uningest_preserves_duplicate_data(
        self,
        markov_db: _FakeMarkovDatabase,
    ) -> None:
        """Uningesting one copy leaves the other intact."""
        await ingest(markov_db, 1, 100, "Hello world.")
        await ingest(markov_db, 1, 100, "Hello world.")
        await uningest(markov_db, 1, 100, "Hello world.")
        assert markov_db.start_words == [StartWord(1, 100, "Hello")]
        assert markov_db.transitions == [Transition(1, 100, "Hello", "world.")]
