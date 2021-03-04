package dev.logal.crabstero.utils;

// Copyright 2021 Logan Fick

// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at

// https://apache.org/licenses/LICENSE-2.0

// Unless required by applicable law or agreed to in writing, software
// distributed under the License is distributed on an "AS IS" BASIS,
// WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
// See the License for the specific language governing permissions and
// limitations under the License.

import java.security.SecureRandom;
import java.util.List;
import java.util.Random;

import dev.logal.crabstero.Crabstero;
import redis.clients.jedis.Jedis;
import redis.clients.jedis.Pipeline;

public final class MarkovChain {
    private static final char DEFAULT_SENTENCE_END = '§';
    private static final Random rng = new SecureRandom();

    private final long id;

    public MarkovChain(final long id) {
        this.id = id;
    }

    public final void ingest(String paragraph) {
        if (!isCompleteSentence(paragraph)) {
            paragraph += DEFAULT_SENTENCE_END;
        }

        final String[] sentences = paragraph.trim().replaceAll(" +", " ").replaceAll("\n", " ").split("(?<=[.!?]) ");

        for (int i = 0; i < sentences.length; i++) {
            this.ingestSentence(sentences[i]);
        }
    }

    private final void ingestSentence(String sentence) {
        if (!isCompleteSentence(sentence)) {
            sentence += DEFAULT_SENTENCE_END;
        }

        String[] words = sentence.trim().replaceAll(" +", " ").split(" ");

        try (final Jedis jedis = Crabstero.getJedis()) {
            final Pipeline pipeline = jedis.pipelined();
            for (int i = 0; i < words.length - 1; i++) {
                if (i == 0) {
                    pipeline.lpush(this.id + ":start", words[i]);
                    pipeline.lpush(this.id + "::" + words[i], words[i + 1]);
                } else {
                    pipeline.lpush(this.id + "::" + words[i], words[i + 1]);
                }
            }
        }
    }

    public final String generate(final int softCharacterLimit, final int hardCharacterLimit) {
        final StringBuilder newSentence = new StringBuilder();

        try (final Jedis jedis = Crabstero.getJedis()) {
            if (!jedis.exists(this.id + ":start")) {
                this.ingestSentence("Hello world!");
            }

            String word = "";

            final List<String> startingWords = jedis.lrange(this.id + ":start", 0, -1);
            int index = rng.nextInt(startingWords.size());
            word = startingWords.get(index);
            newSentence.append(word);

            while (!isCompleteSentence(word)) {
                final List<String> wordChoices = jedis.lrange(this.id + "::" + word, 0, -1);

                index = -1;
                if (newSentence.length() >= softCharacterLimit) {
                    for (int i = 0; i < wordChoices.size(); i++) {
                        final String candidate = wordChoices.get(i);

                        if (isCompleteSentence(candidate)) {
                            index = i;
                            break;
                        }
                    }

                    if (index == -1) {
                        index = rng.nextInt(wordChoices.size());
                    }
                } else {
                    index = rng.nextInt(wordChoices.size());
                }

                word = wordChoices.get(index);
                newSentence.append(" " + word);

                final int sentenceLength = newSentence.length();
                if (sentenceLength >= hardCharacterLimit) {
                    newSentence.delete(hardCharacterLimit, sentenceLength);
                    break;
                }
            }
        }

        if (newSentence.charAt(newSentence.length() - 1) == DEFAULT_SENTENCE_END) {
            return newSentence.deleteCharAt(newSentence.length() - 1).toString();
        } else {
            return newSentence.toString();
        }
    }

    private static final boolean isCompleteSentence(final String sentence) {
        if (sentence.isEmpty()) {
            return false;
        }

        final char lastChar = sentence.charAt(sentence.length() - 1);
        return (lastChar == DEFAULT_SENTENCE_END || lastChar == '.' || lastChar == '!' || lastChar == '?');
    }
}