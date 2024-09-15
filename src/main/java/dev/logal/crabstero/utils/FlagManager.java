/*
 * Copyright 2024 Logan Fick
 *
 * Licensed under the Apache License, Version 2.0 (the "License"); you may not use this file except in compliance with the License. You may obtain a copy of the License at: https://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software distributed under the License is distributed on an "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the License for the specific language governing permissions and limitations under the License.
 */

package dev.logal.crabstero.utils;

import dev.logal.crabstero.Crabstero;
import org.javacord.api.entity.channel.TextChannel;
import org.javacord.api.entity.server.Server;
import org.javacord.api.entity.user.User;
import redis.clients.jedis.Jedis;

/**
 * Provides ability to set and check for flags on text channels, servers, and users.
 */
public class FlagManager {
    public static final String CHANNEL_DB_PREFIX = "";
    public static final String SERVER_DB_PREFIX = "s";
    public static final String USER_DB_PREFIX = "u";

    private final Crabstero crabstero; // The Crabstero instance using this configuration manager.

    /**
     * Creates a new configuration manager owned by a given instance of Crabstero.
     *
     * @param crabstero The instance of Crabstero.
     */
    public FlagManager(final Crabstero crabstero) {
        this.crabstero = crabstero;
    }

    /**
     * Sets a flag of a given name on a given ID in the namespace of a prefix.
     * This should only be used if only an ID is known instead of a reference to a more abstract object.
     *
     * @param prefix The prefix.
     * @param ID     The ID of the entity within that prefix namespace to set the flag on.
     * @param flag   The flag to set.
     */
    public void setFlag(final String prefix, final String ID, final String flag) {
        try (final Jedis jedis = this.crabstero.getJedisPool().getResource()) {
            jedis.set(prefix + ID + ":flags:" + flag, "");
        }
    }

    /**
     * Sets a flag of a given name on the given text channel.
     *
     * @param channel The text channel to set the flag on.
     * @param flag    The name of the flag to set.
     */
    public void setFlag(final TextChannel channel, String flag) {
        this.setFlag(CHANNEL_DB_PREFIX, channel.getIdAsString(), flag);
    }

    /**
     * Sets a flag of a given name on the given server.
     *
     * @param server The server to set the flag on.
     * @param flag   The name of the flag to set.
     */
    public void setFlag(final Server server, String flag) {
        this.setFlag(SERVER_DB_PREFIX, server.getIdAsString(), flag);
    }

    /**
     * Sets a flag of a given name on the given user.
     *
     * @param user The user to set the flag on.
     * @param flag The name of the flag to set.
     */
    public void setFlag(final User user, String flag) {
        this.setFlag(USER_DB_PREFIX, user.getIdAsString(), flag);
    }

    /**
     * Clears the flag of a given name on a given ID in the namespace of a prefix.
     * This should only be used if only an ID is known instead of a reference to a more abstract object.
     *
     * @param prefix The prefix.
     * @param ID     The ID of the entity within that prefix namespace to clear the flag on.
     * @param flag   The name of the flag to clear.
     */
    public void clearFlag(final String prefix, final String ID, final String flag) {
        try (final Jedis jedis = this.crabstero.getJedisPool().getResource()) {
            jedis.del(prefix + ID + ":flags:" + flag);
        }
    }

    /**
     * Clears the flag of a given name on the given text channel.
     *
     * @param channel The text channel to clear the flag on.
     * @param flag    The name of the flag to clear.
     */
    public void clearFlag(final TextChannel channel, String flag) {
        this.clearFlag(CHANNEL_DB_PREFIX, channel.getIdAsString(), flag);
    }

    /**
     * Clears the flag of a given name on the given server.
     *
     * @param server The server to clear the flag on.
     * @param flag   The name of the flag to clear.
     */
    public void clearFlag(final Server server, String flag) {
        this.clearFlag(SERVER_DB_PREFIX, server.getIdAsString(), flag);
    }

    /**
     * Clears the flag of a given name on the given user.
     *
     * @param user The user to clear the flag on.
     * @param flag The name of the flag to clear.
     */
    public void clearFlag(final User user, String flag) {
        this.clearFlag(USER_DB_PREFIX, user.getIdAsString(), flag);
    }

    /**
     * Checks whether a flag of a given name is set on a given ID in the namespace of a prefix.
     * This should only be used if only an ID is known instead of a reference to a more abstract object.
     *
     * @param prefix The prefix.
     * @param ID     The ID of the entity within that prefix namespace to check on.
     * @param flag   The name of the flag to check for.
     * @return A boolean indicating whether the flag is set.
     */
    public boolean isFlagSet(final String prefix, final String ID, final String flag) {
        try (final Jedis jedis = this.crabstero.getJedisPool().getResource()) {
            return jedis.exists(prefix + ID + ":flags:" + flag);
        }
    }

    /**
     * Checks whether a flag of a given name is set on a given text channel.
     *
     * @param channel The text channel to check on.
     * @param flag    The name of the flag to check for.
     * @return A boolean indicating whether the flag is set.
     */
    public boolean isFlagSet(final TextChannel channel, String flag) {
        return this.isFlagSet(CHANNEL_DB_PREFIX, channel.getIdAsString(), flag);
    }

    /**
     * Checks whether a flag of a given name is set on a given server.
     *
     * @param server The server to check on.
     * @param flag   The name of the flag to check for.
     * @return A boolean indicating whether the flag is set.
     */
    public boolean isFlagSet(final Server server, String flag) {
        return this.isFlagSet(SERVER_DB_PREFIX, server.getIdAsString(), flag);
    }

    /**
     * Checks whether a flag of a given name is set on a given user.
     *
     * @param user The user to check on.
     * @param flag The name of the flag to check for.
     * @return A boolean indicating whether the flag is set.
     */
    public boolean isFlagSet(final User user, String flag) {
        return this.isFlagSet(USER_DB_PREFIX, user.getIdAsString(), flag);
    }
}
