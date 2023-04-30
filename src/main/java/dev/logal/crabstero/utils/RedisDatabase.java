/*
 * Copyright 2023 Logan Fick
 *
 * Licensed under the Apache License, Version 2.0 (the "License"); you may not use this file except in compliance with the License. You may obtain a copy of the License at: https://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software distributed under the License is distributed on an "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the License for the specific language governing permissions and limitations under the License.
 */

package dev.logal.crabstero.utils;

import redis.clients.jedis.Jedis;
import redis.clients.jedis.JedisPool;
import redis.clients.jedis.JedisPoolConfig;

import java.util.List;

/**
 * Wrapper for a Redis database which forces logical separation of data by requiring two extra parameters for every command:
 * 1. A logical database ID, which is intended to separate Markov chain data from other miscellaneous state data.
 * 2. A general unique ID, which is intended to separate data in the same logical database between Discord channels.
 */
public final class RedisDatabase {
    public static final int MAIN_DATABASE_ID = 0; // The ID of the logical database which stores miscellaneous state data.
    public static final int WORDS_DATABASE_ID = 1; // The ID of the logical database which specifically stores Markov chain data.
    private static final int DEFAULT_REDIS_PORT = 6379; // Default port to use when not specifically during instantiation.
    private static final String SEPARATOR = ":"; // Separator to use between the virtual namespace ID and key.

    private final JedisPool jedisPool;

    /**
     * Creates a new Redis database which connects to the Redis server hosted on the given host and port DEFAULT_REDIS_PORT.
     *
     * @param redisHost The host of the Redis server.
     */
    public RedisDatabase(final String redisHost) {
        this.jedisPool = new JedisPool(new JedisPoolConfig(), redisHost, DEFAULT_REDIS_PORT);
    }

    /**
     * Creates a new Redis database which connects to the Redis server hosted on the given host and port.
     *
     * @param redisHost The host of the Redis server.
     * @param redisPort The port of the Redis server.
     */
    public RedisDatabase(final String redisHost, final int redisPort) {
        this.jedisPool = new JedisPool(new JedisPoolConfig(), redisHost, redisPort);
    }

    /**
     * Converts a given unique ID and a key into a single String which can be used as a key to the backend Redis database.
     *
     * @param id  The unique ID.
     * @param key The key.
     * @return The long key which can be sent to a Redis database, unless the given unique ID is 0, then just key exactly as given.
     */
    private static String resolveKey(final long id, final String key) {
        if (id == 0) {
            return key;
        } else {
            return id + SEPARATOR + key;
        }
    }

    /**
     * Adds a string to the head of the list stored at the given key uniquely identified by the given ID in the given logical database ID.
     *
     * @param database Zero-based index logical database ID.
     * @param id       The unique ID.
     * @param key      The key of the list.
     * @param values   The value(s) to add to the list.
     */
    public void lpush(final int database, final long id, final String key, final String... values) {
        try (final Jedis jedis = this.jedisPool.getResource()) {
            jedis.select(database);
            jedis.lpush(resolveKey(id, key), values);
        }
    }

    /**
     * Tests whether the given key uniquely identified by the given ID in the given logical database ID exists.
     *
     * @param database Zero-based index logical database ID.
     * @param id       The unique ID.
     * @param key      The key to test.
     * @return True if the key exists, false otherwise.
     */
    public boolean exists(final int database, final long id, final String key) {
        try (final Jedis jedis = this.jedisPool.getResource()) {
            jedis.select(database);
            return jedis.exists(resolveKey(id, key));
        }
    }

    /**
     * Returns the elements starting and ending at the given indexes of the list stored at the given key uniquely identified by the given ID in the given logical database ID.
     *
     * @param database Zero-based index logical database ID.
     * @param id       The unique ID
     * @param key      The key of the list.
     * @param start    Zero-based index of the starting element.
     * @param stop     Zero-based index of the ending element.
     * @return A List with the requested elements.
     */
    public List<String> lrange(final int database, final long id, final String key, final long start, final long stop) {
        try (final Jedis jedis = this.jedisPool.getResource()) {
            jedis.select(database);
            return jedis.lrange(resolveKey(id, key), start, stop);
        }
    }
}
