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
Ingests sentences and generates new ones using a Markov chain backed by SQLite storage.

The Markov chain stores word transitions per channel, using duplicate rows to represent frequency
weight. Random selection via ORDER BY RANDOM() LIMIT 1 naturally preserves this weighting.
"""

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from crabstero.database import Database

# Section sign (§), used internally to mark the end of sentences that lack punctuation,
# as is common on Discord.
DEFAULT_SENTENCE_END = "\u00a7"


def is_complete_sentence(sentence: str) -> bool:
    """
    Checks whether a given sentence ends with a default sentence end character, a period, an
    exclamation mark, or a question mark.

    :param sentence: The sentence to test.
    :return: True if the sentence ends with a valid terminator, False otherwise.
    """
    if not sentence:
        return False

    return sentence[-1] in (DEFAULT_SENTENCE_END, ".", "!", "?")


async def ingest(db: Database, channel_id: int, user_id: int, paragraph: str) -> None:
    """
    Ingests a string potentially containing multiple smaller sentences into the Markov chain
    for a given channel.

    :param db: The database instance.
    :param channel_id: The Discord channel ID to associate with this data.
    :param user_id: The Discord user ID of the contributor.
    :param paragraph: The paragraph of sentences to ingest.
    """
    if not is_complete_sentence(paragraph):
        paragraph += DEFAULT_SENTENCE_END

    # Normalize whitespace, then split on sentence-ending punctuation followed by a space.
    normalized = re.sub(r" +", " ", paragraph.strip().replace("\n", " "))
    sentences = re.split(r"(?<=[.!?]) ", normalized)

    for sentence in sentences:
        await _ingest_sentence(db, channel_id, user_id, sentence)


async def _ingest_sentence(
    db: Database, channel_id: int, user_id: int, sentence: str
) -> None:
    """
    Ingests a string containing a single sentence into the Markov chain for a given channel.

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
    """
    Generates a new sentence using words learned from previously ingested sentences for a given
    channel.

    :param db: The database instance.
    :param channel_id: The Discord channel ID to generate from.
    :param soft_limit: The amount of characters to try and limit sentence length around.
    :param hard_limit: The amount of characters to cut off the sentence at if it gets too long.
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
