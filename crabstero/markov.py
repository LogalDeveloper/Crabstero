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

"""Markov chain sentence ingestion and generation backed by SQLite.

The Markov chain stores word transitions per channel, using duplicate rows
to represent frequency weight. Random selection via ORDER BY RANDOM()
LIMIT 1 naturally preserves this weighting.
"""

import itertools
import re
from typing import TYPE_CHECKING

from crabstero import metrics
from crabstero.database import StartWord, Transition

if TYPE_CHECKING:
    from crabstero.database import Database

# Section sign (§), used internally to mark the end of sentences that lack punctuation,
# as is common on Discord.
DEFAULT_SENTENCE_END = "\u00a7"

_TERMINATORS = frozenset({DEFAULT_SENTENCE_END, ".", "!", "?"})
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?]) ")


def _normalize_whitespace(text: str) -> str:
    """Collapse runs of whitespace into single spaces and strip edges.

    :param text: The raw text to normalize.
    :return: The normalized text.
    """
    return " ".join(text.split())


def is_complete_sentence(sentence: str) -> bool:
    """Check whether a sentence ends with a valid terminator.

    Valid terminators are the section sign, period, exclamation mark, or
    question mark.

    :param sentence: The sentence to test.
    :return: True if the sentence ends with a valid terminator, False otherwise.
    """
    if not sentence:
        return False

    return sentence[-1] in _TERMINATORS


def _split_sentences(paragraph: str) -> list[str]:
    """Normalize whitespace and split a paragraph into sentences.

    :param paragraph: The raw paragraph text.
    :return: A list of individual sentences.
    """
    if not is_complete_sentence(paragraph):
        paragraph += DEFAULT_SENTENCE_END
    normalized = _normalize_whitespace(paragraph)
    return _SENTENCE_SPLIT.split(normalized)


def _tokenize_sentence(
    sentence: str,
) -> tuple[list[str], list[tuple[str, str]]]:
    """Tokenize a sentence into start words and transition pairs.

    :param sentence: A single sentence.
    :return: A tuple of (start_words, transitions) using bare word strings.
    """
    if not is_complete_sentence(sentence):
        sentence += DEFAULT_SENTENCE_END
    words = sentence.split()

    start_words: list[str] = [words[0]] if len(words) >= 2 else []
    transitions: list[tuple[str, str]] = list(itertools.pairwise(words))
    return start_words, transitions


async def ingest(db: Database, channel_id: int, user_id: int, paragraph: str) -> None:
    """Ingest a paragraph into the Markov chain for a given channel.

    The paragraph may contain multiple sentences which are split and ingested
    individually.

    :param db: The database instance.
    :param channel_id: The Discord channel ID to associate with this data.
    :param user_id: The Discord user ID of the contributor.
    :param paragraph: The paragraph of sentences to ingest.
    """
    for sentence in _split_sentences(paragraph):
        await _ingest_sentence(db, channel_id, user_id, sentence)


async def _ingest_sentence(
    db: Database, channel_id: int, user_id: int, sentence: str
) -> None:
    """Ingest a single sentence into the Markov chain for a given channel.

    :param db: The database instance.
    :param channel_id: The Discord channel ID to associate with this data.
    :param user_id: The Discord user ID of the contributor.
    :param sentence: The sentence to ingest.
    """
    raw_starts, raw_transitions = _tokenize_sentence(sentence)
    start_words = [StartWord(channel_id, user_id, w) for w in raw_starts]
    transitions = [Transition(channel_id, user_id, w, nw) for w, nw in raw_transitions]
    await db.add_markov_data(start_words, transitions)


async def uningest(db: Database, channel_id: int, user_id: int, paragraph: str) -> None:
    """Remove a paragraph's Markov data from the chain for a given channel.

    Mirrors :func:`ingest` but deletes one matching row per entry instead of
    inserting.

    :param db: The database instance.
    :param channel_id: The Discord channel ID.
    :param user_id: The Discord user ID of the contributor.
    :param paragraph: The paragraph of sentences to uningest.
    """
    for sentence in _split_sentences(paragraph):
        await _uningest_sentence(db, channel_id, user_id, sentence)


async def _uningest_sentence(
    db: Database, channel_id: int, user_id: int, sentence: str
) -> None:
    """Remove a single sentence's Markov data from the chain.

    :param db: The database instance.
    :param channel_id: The Discord channel ID.
    :param user_id: The Discord user ID of the contributor.
    :param sentence: The sentence to uningest.
    """
    raw_starts, raw_transitions = _tokenize_sentence(sentence)
    start_words = [StartWord(channel_id, user_id, w) for w in raw_starts]
    transitions = [Transition(channel_id, user_id, w, nw) for w, nw in raw_transitions]
    await db.remove_markov_data(start_words, transitions)


async def generate(
    db: Database, channel_id: int, soft_limit: int = 750, hard_limit: int = 1000
) -> str:
    """Generate a new sentence from previously ingested words for a channel.

    :param db: The database instance.
    :param channel_id: The Discord channel ID to generate from.
    :param soft_limit: Character count to aim for when wrapping up.
    :param hard_limit: Character count to hard-cut the sentence at.
    :return: A new generated sentence.
    """
    word = await db.get_random_start_word(channel_id)

    if word is None:
        return (
            "There's not enough data to generate a message yet."
            " Chat a bit more so I can learn how this channel talks!"
        )

    with metrics.GENERATION_DURATION.time():
        parts: list[str] = [word]
        current_length = len(word)

        # The loop is skipped if the start word already ends a sentence (e.g. "Yes.").
        while not is_complete_sentence(word):
            # Past the soft limit, prefer a sentence-ending word to wrap up.
            if current_length >= soft_limit:
                next_word = await db.get_random_completing_next_word(channel_id, word)
                if next_word is None:
                    next_word = await db.get_random_next_word(channel_id, word)
            else:
                next_word = await db.get_random_next_word(channel_id, word)

            if next_word is None:
                break

            word = next_word

            parts.append(word)
            current_length += 1 + len(word)  # +1 for the joining space.

            if current_length >= hard_limit:
                break

        result = " ".join(parts)
        if current_length >= hard_limit:
            result = result[:hard_limit]

        # Strip the internal sentence-end marker so it never appears in output.
        return result.removesuffix(DEFAULT_SENTENCE_END)
