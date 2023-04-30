/*
 * Copyright 2023 Logan Fick
 *
 * Licensed under the Apache License, Version 2.0 (the "License"); you may not use this file except in compliance with the License. You may obtain a copy of the License at: https://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software distributed under the License is distributed on an "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the License for the specific language governing permissions and limitations under the License.
 */

package dev.logal.crabstero;

import dev.logal.crabstero.listeners.*;
import dev.logal.crabstero.utils.RedisDatabase;
import org.javacord.api.DiscordApi;
import org.javacord.api.DiscordApiBuilder;
import org.javacord.api.entity.intent.Intent;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.util.concurrent.Executors;
import java.util.concurrent.ScheduledExecutorService;

/**
 * The simple nonversation Discord bot.
 */
public final class Crabstero {
    private static final Logger logger = LoggerFactory.getLogger(Crabstero.class);

    private final DiscordApi discordApi;
    private final RedisDatabase redisDatabase;
    private final ScheduledExecutorService workerPool;

    /**
     * Creates a new instance of Crabstero using a given Discord bot token to connect to Discord and a given Redis host and port to connect to a Redis database.
     *
     * @param token     The Discord bot token.
     * @param redisHost The host of the Redis server.
     * @param redisPort The port of the Redis server.
     */
    public Crabstero(final String token, final String redisHost, final Integer redisPort) {
        if (redisPort == null) {
            this.redisDatabase = new RedisDatabase(redisHost);
        } else {
            this.redisDatabase = new RedisDatabase(redisHost, redisPort);
        }
        this.workerPool = Executors.newScheduledThreadPool(4, new CrabsteroThreadFactory());

        final DiscordApiBuilder builder = new DiscordApiBuilder();

        builder.setToken(token);
        builder.setTrustAllCertificates(false);

        builder.setWaitForServersOnStartup(false);
        builder.setWaitForUsersOnStartup(false);

        builder.setIntents(Intent.GUILDS, Intent.GUILD_MESSAGES, Intent.MESSAGE_CONTENT);

        builder.addListener(new MessageCreate(this));
        builder.addListener(new RoleChangePermissions(this));
        builder.addListener(new ServerBecomesAvailable(this));
        builder.addListener(new ServerChannelChangeOverwrittenPermissions(this));
        builder.addListener(new ServerJoin(this));
        builder.addListener(new UserRoleAdd(this));

        builder.setRecommendedTotalShards();

        this.discordApi = builder.login().join();
        this.discordApi.setMessageCacheSize(0, 1);
    }

    /**
     * Entrypoint to the standalone executable build of Crabstero. This simply starts an instance of Crabstero
     *
     * @param arguments Command line arguments. Not used.
     */
    public static void main(final String[] arguments) {
        final String token = System.getenv("TOKEN");
        final String redisHost = System.getenv("REDIS_HOST");
        final String redisPort = System.getenv("REDIS_PORT");

        logger.info("Starting Crabstero...");
        if (redisPort == null){
            new Crabstero(token, redisHost, null);
        } else {
            new Crabstero(token, redisHost, Integer.parseInt(redisPort));
        }
        logger.info("Crabstero started!");
    }

    /**
     * Gets the Discord API used by this instance of Crabstero.
     *
     * @return The Discord API.
     */
    public DiscordApi getDiscordApi() {
        return this.discordApi;
    }

    /**
     * Gets the Redis database used by this instance of Crabstero.
     *
     * @return The Redis database.
     */
    public RedisDatabase getRedisDatabase() {
        return this.redisDatabase;
    }

    /**
     * Gets the worker pool used by this instance of Crabstero.
     *
     * @return The scheduled executor service.
     */
    public ScheduledExecutorService getWorkerPool() {
        return this.workerPool;
    }
}