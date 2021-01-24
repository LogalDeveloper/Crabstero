package dev.logal.crabstero;

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

import java.util.concurrent.Executors;
import java.util.concurrent.Future;
import java.util.concurrent.ScheduledExecutorService;
import java.util.concurrent.ScheduledFuture;
import java.util.concurrent.TimeUnit;

import org.javacord.api.AccountType;
import org.javacord.api.DiscordApi;
import org.javacord.api.DiscordApiBuilder;
import org.javacord.api.entity.intent.Intent;

import dev.logal.crabstero.listeners.*;
import redis.clients.jedis.Jedis;
import redis.clients.jedis.JedisPool;
import redis.clients.jedis.JedisPoolConfig;

public final class Crabstero {
    public static final int maximumMessagesPerChannel = 50000;

    private static final String token = System.getenv("TOKEN");
    private static final JedisPool jedisPool = new JedisPool(new JedisPoolConfig(), System.getenv("REDIS_HOST"));

    private static final ScheduledExecutorService workerPool = Executors.newScheduledThreadPool(4,
            new CrabsteroThreadFactory());

    private Crabstero() {
        throw new UnsupportedOperationException();
    }

    public static final void main(final String[] arguments) {
        final DiscordApiBuilder builder = new DiscordApiBuilder();

        builder.setAccountType(AccountType.BOT);
        builder.setToken(token);
        builder.setTrustAllCertificates(false);

        builder.setWaitForServersOnStartup(false);
        builder.setWaitForUsersOnStartup(false);

        builder.setIntents(Intent.GUILDS, Intent.GUILD_MESSAGES);

        builder.addListener(new MessageCreate());
        builder.addListener(new RoleChangePermissions());
        builder.addListener(new ServerBecomesAvailable());
        builder.addListener(new ServerChannelChangeOverwrittenPermissions());
        builder.addListener(new ServerJoin());
        builder.addListener(new UserRoleAdd());

        builder.setRecommendedTotalShards();

        final DiscordApi api = builder.login().join();
        api.setMessageCacheSize(0, 1);
    }

    public static final Future<?> submitTask(final Runnable task) {
        return workerPool.submit(task);
    }

    public static final ScheduledFuture<?> scheduleTask(final Runnable task, final long delay, final TimeUnit unit) {
        return workerPool.schedule(task, delay, unit);
    }

    public static final Jedis getJedis() {
        return jedisPool.getResource();
    }
}