/*
 * Copyright 2022 Logan Fick
 *
 * Licensed under the Apache License, Version 2.0 (the "License"); you may not use this file except in compliance with the License. You may obtain a copy of the License at: https://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software distributed under the License is distributed on an "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the License for the specific language governing permissions and limitations under the License.
 */

package dev.logal.crabstero.utils;

import dev.logal.crabstero.Crabstero;
import redis.clients.jedis.Jedis;
import redis.clients.jedis.Pipeline;

import java.security.SecureRandom;
import java.util.List;
import java.util.Random;

/**
 * Ingests sentences and generates new ones using a Markov chain with a Redis storage.
 */
public final class MarkovChain {
    private static final char DEFAULT_SENTENCE_END = '§'; // Default character to end sentences with if it does not include a period, exclamation mark, or question mark.

    private final long id; // A unique number for this Markov chain to segment it away from other Markov chains. This is usually the channel ID given by Discord.
    private final Crabstero crabstero; // The Crabstero instance using this Markov chain.
    private final Random rng; // A source of randomness.

    /**
     * Creates a new Markov chain identified by a given number and owned by a given instance of Crabstero.
     *
     * @param id        The unique number.
     * @param crabstero The instance of Crabstero.
     */
    public MarkovChain(final long id, final Crabstero crabstero) {
        this.id = id;
        this.crabstero = crabstero;
        this.rng = new SecureRandom(); // Probably placebo effect, but SecureRandom seems to produce better results.
    }

    /**
     * Creates a new Markov chain identified by a given number, owned by a given instance of Crabstero, and using a custom source of randomness.
     *
     * @param id        The unique number.
     * @param crabstero The instance of Crabstero.
     * @param random    The custom randomness source.
     */
    public MarkovChain(final long id, final Crabstero crabstero, final Random random) {
        this.id = id;
        this.crabstero = crabstero;
        this.rng = random;
    }

    /**
     * Checks whether a given sentence ends with a default sentence end character, a period, an exclamation mark, or a question mark.
     *
     * @param sentence The sentence to test.
     * @return True if the sentence ends with a default sentence end character, a period, an exclamation mark, or a question mark, false otherwise.
     */
    private static boolean isCompleteSentence(final String sentence) {
        // Is the given sentence an empty string?
        if (sentence.isEmpty()) {
            // Yes. Therefor, it is not a complete sentence.
            return false;
        }

        // Get the last character of the given sentence.
        final char lastChar = sentence.charAt(sentence.length() - 1);

        // Return true if th character is a default sentence end character, a period, an exclamation mark, or a question mark.
        return (lastChar == DEFAULT_SENTENCE_END || lastChar == '.' || lastChar == '!' || lastChar == '?');
    }

    /**
     * Ingests a string potentially containing multiple smaller sentences.
     *
     * @param paragraph The paragraph of sentences to ingest.
     */
    public void ingest(String paragraph) {
        // Is the given paragraph as a whole complete?
        if (!isCompleteSentence(paragraph)) {
            // No. Add DEFAULT_SENTENCE_END to the end.
            paragraph += DEFAULT_SENTENCE_END;
        }

        // First, trim all leading and trailing spaces.
        // Next, replace repeated spaces with a single space.
        // Then, replace new lines with spaces.
        // Finally, split the paragraph into a string array with each element being a single sentence.
        final String[] sentences = paragraph.trim().replaceAll(" +", " ").replaceAll("\n", " ").split("(?<=[.!?]) ");

        // Ingest each sentence individually.
        for (String sentence : sentences) {
            this.ingestSentence(sentence);
        }
    }

    /**
     * Ingests a string containing a single sentence.
     *
     * @param sentence The sentence to ingest.
     */
    private void ingestSentence(String sentence) {
        // Is the given sentence complete?
        if (!isCompleteSentence(sentence)) {
            // No. Add DEFAULT_SENTENCE_END to the end.
            sentence += DEFAULT_SENTENCE_END;
        }

        // First, trim all leading and trailing spaces.
        // Next, replace repeated spaces with a single space.
        // Finally, split the sentence into a string array with each element being a word.
        String[] words = sentence.trim().replaceAll(" +", " ").split(" ");

        // Get a reference to Jedis from the pool.
        try (final Jedis jedis = this.crabstero.getJedisPool().getResource()) {
            // Use a pipeline to avoid blocking caused by waiting for responses from the Redis server.
            final Pipeline pipeline = jedis.pipelined();

            // Iterate over the array of words, starting from the first element until the second to last element.
            for (int i = 0; i < words.length - 1; i++) {
                // Is this the first word?
                if (i == 0) {
                    // Yes. Push the word into a special list containing starting words.
                    pipeline.lpush(this.id + ":start", words[i]);
                }

                // Push the word after this one into a list identified by this word.
                pipeline.lpush(this.id + "::" + words[i], words[i + 1]);
            }
        }
    }

    /**
     * Generates a new sentence using word learned from previously ingested sentences.
     *
     * @param softCharacterLimit The amount of characters to try and limit sentence length around.
     * @param hardCharacterLimit The amount of characters to cut off the sentence at if it gets too long.
     * @return A new sentence.
     */
    public String generate(final int softCharacterLimit, final int hardCharacterLimit) {
        // Declare a new builder for building the new sentence.
        final StringBuilder newSentence = new StringBuilder();

        // Get a reference to Jedis from the pool.
        try (final Jedis jedis = this.crabstero.getJedisPool().getResource()) {
            // Does this Markov chain have any starting words?
            if (!jedis.exists(this.id + ":start")) {
                // No. Quickly ingest something to avoid throwing an error.
                // TODO: Could this soft lock since a reference to the jedis pool is already held?
                this.ingestSentence("Hello world!");
            }

            // Declare a variable for holding the word about to be added to the new sentence.
            String word;

            // Get a list of all starting words.
            final List<String> startingWords = jedis.lrange(this.id + ":start", 0, -1);

            // Pick a random index for a starting word.
            int index = rng.nextInt(startingWords.size());

            // Get the word.
            word = startingWords.get(index);

            // Add it to the output sentence.
            newSentence.append(word);

            // While the selected word is in complete...
            // Although quite rare, this loop will skip if the starting word is already complete on its own (e.g. "Yes.")
            while (!isCompleteSentence(word)) {
                // Get the list of words which can be added after the previous word.
                final List<String> wordChoices = jedis.lrange(this.id + "::" + word, 0, -1);

                // Reset the index.
                index = -1;

                // Is the new sentence length above the soft character limit?
                if (newSentence.length() >= softCharacterLimit) {
                    // Yes. Time to aggressively search for words which can complete this sentence.

                    // Iterate over the word choices.
                    for (int i = 0; i < wordChoices.size(); i++) {
                        // Get current candidate word.
                        final String candidate = wordChoices.get(i);

                        // Does this word conclude the sentence?
                        if (isCompleteSentence(candidate)) {
                            // Select the index for this word and stop evaluating choices.
                            index = i;
                            break;
                        }
                    }

                    // Was no word to conclude the new sentence found?
                    if (index == -1) {
                        // Yes. Pick a random word and hope the next word can conclude it.
                        index = rng.nextInt(wordChoices.size());
                    }
                } else {
                    // No, there is still room. Pick a random index.
                    index = rng.nextInt(wordChoices.size());
                }

                // Get the selected word.
                word = wordChoices.get(index);

                // Append a space and this word to the new sentence.
                newSentence.append(" ").append(word);

                // Is the new sentence now longer than the hard character limit?
                final int sentenceLength = newSentence.length();
                if (sentenceLength >= hardCharacterLimit) {
                    // Yes. Chop off the characters beyond the hard limit.
                    newSentence.delete(hardCharacterLimit, sentenceLength);
                    // Break out of the loop to forcefully declare the new sentence complete.
                    break;
                }
            }
        }

        // Is the last character of the new sentence the default sentence end character?
        if (newSentence.charAt(newSentence.length() - 1) == DEFAULT_SENTENCE_END) {
            // Yes. Get a rid of it. The default sentence end character is meant to internally mark the end of sentences which did not include a punctuation mark, as is common on Discord.
            return newSentence.deleteCharAt(newSentence.length() - 1).toString();
        } else {
            // No. Return the new sentence as is.
            return newSentence.toString();
        }
    }
}