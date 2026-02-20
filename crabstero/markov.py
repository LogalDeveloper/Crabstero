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

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from crabstero.database import Database

# Section sign (§), used internally to mark the end of sentences that lack punctuation,
# as is common on Discord.
DEFAULT_SENTENCE_END = "\u00a7"


def is_complete_sentence(sentence: str) -> bool:
    """Check whether a sentence ends with a valid terminator.

    Valid terminators are the section sign, period, exclamation mark, or
    question mark.

    :param sentence: The sentence to test.
    :return: True if the sentence ends with a valid terminator, False otherwise.
    """
    if not sentence:
        return False

    return sentence[-1] in (DEFAULT_SENTENCE_END, ".", "!", "?")


async def ingest(db: Database, channel_id: int, user_id: int, paragraph: str) -> None:
    """Ingest a paragraph into the Markov chain for a given channel.

    The paragraph may contain multiple sentences which are split and ingested
    individually.

    :param db: The database instance.
    :param channel_id: The Discord channel ID to associate with this data.
    :param user_id: The Discord user ID of the contributor.
    :param paragraph: The paragraph of sentences to ingest.
    """
    if not is_complete_sentence(paragraph):
        paragraph += DEFAULT_SENTENCE_END

    # Normalize whitespace, then split on sentence-ending punctuation
    # followed by a space.
    normalized = re.sub(r" +", " ", paragraph.strip().replace("\n", " "))
    sentences = re.split(r"(?<=[.!?]) ", normalized)

    for sentence in sentences:
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
    if not is_complete_sentence(sentence):
        sentence += DEFAULT_SENTENCE_END

    # Normalize whitespace and split into individual words.
    words = re.sub(r" +", " ", sentence.strip()).split(" ")

    start_words: list[tuple[int, int, str]] = []
    transitions: list[tuple[int, int, str, str]] = []

    for i in range(len(words) - 1):
        if i == 0:
            start_words.append((channel_id, user_id, words[i]))
        transitions.append((channel_id, user_id, words[i], words[i + 1]))

    if start_words:
        await db.add_start_words_batch(start_words)
    if transitions:
        await db.add_transitions_batch(transitions)


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

    # Seed the chain with a fallback sentence if the channel has no data yet.
    if word is None:
        await _ingest_sentence(db, channel_id, 0, "Hello world!")
        word = await db.get_random_start_word(channel_id)
        if word is None:
            return ""

    parts: list[str] = []
    parts.append(word)
    current_length = len(word)

    # The loop is skipped if the starting word already ends a sentence (e.g. "Yes.").
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
            result = " ".join(parts)[:hard_limit]
            # Strip the internal sentence-end marker if it ended up at the boundary.
            if result and result[-1] == DEFAULT_SENTENCE_END:
                return result[:-1]
            return result

    result = " ".join(parts)

    # Strip the internal sentence-end marker so it never appears in output.
    if result and result[-1] == DEFAULT_SENTENCE_END:
        return result[:-1]

    return result
