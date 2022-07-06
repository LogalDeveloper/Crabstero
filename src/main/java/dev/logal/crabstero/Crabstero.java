/*
 * Copyright 2022 Logan Fick
 *
 * Licensed under the Apache License, Version 2.0 (the "License"); you may not use this file except in compliance with the License. You may obtain a copy of the License at: https://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software distributed under the License is distributed on an "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the License for the specific language governing permissions and limitations under the License.
 */

package dev.logal.crabstero;

import dev.logal.crabstero.listeners.*;
import org.javacord.api.DiscordApi;
import org.javacord.api.DiscordApiBuilder;
import org.javacord.api.entity.intent.Intent;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import redis.clients.jedis.JedisPool;
import redis.clients.jedis.JedisPoolConfig;

import java.util.concurrent.Executors;
import java.util.concurrent.ScheduledExecutorService;

public final class Crabstero {
    private static final Logger logger = LoggerFactory.getLogger(Crabstero.class);

    private final DiscordApi discordApi;
    private final JedisPool jedisPool;
    private final ScheduledExecutorService workerPool;

    public Crabstero(final String token, final String redisHost, final int redisPort) {
        this.jedisPool = new JedisPool(new JedisPoolConfig(), redisHost, redisPort);
        this.workerPool = Executors.newScheduledThreadPool(4, new CrabsteroThreadFactory());

        final DiscordApiBuilder builder = new DiscordApiBuilder();

        builder.setToken(token);
        builder.setTrustAllCertificates(false);

        builder.setWaitForServersOnStartup(false);
        builder.setWaitForUsersOnStartup(false);

        builder.setIntents(Intent.GUILDS, Intent.GUILD_MESSAGES);

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

    public static void main(final String[] arguments) {
        final String token = System.getenv("TOKEN");
        final String redisHost = System.getenv("REDIS_HOST");

        logger.info("Starting Crabstero...");
        new Crabstero(token, redisHost, 6379);
        logger.info("Crabstero started!");
    }

    public DiscordApi getDiscordApi() {
        return this.discordApi;
    }

    public JedisPool getJedisPool() {
        return this.jedisPool;
    }

    public ScheduledExecutorService getWorkerPool() {
        return this.workerPool;
    }
}