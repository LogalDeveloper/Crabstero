package dev.logal.crabstero.tasks;

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

import java.util.Iterator;
import java.util.stream.Stream;

import org.javacord.api.entity.channel.ServerTextChannel;
import org.javacord.api.entity.message.Message;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import dev.logal.crabstero.Crabstero;
import dev.logal.crabstero.utils.MarkovChainMessages;
import redis.clients.jedis.Jedis;
import redis.clients.jedis.Pipeline;

public final class ChannelHistoryIngestionTask implements Runnable {
    private static final String INGESTED_CHANNELS_KEY = "ingestedChannels";

    private static final Logger logger = LoggerFactory.getLogger(ChannelHistoryIngestionTask.class);

    private final ServerTextChannel channel;

    public ChannelHistoryIngestionTask(final ServerTextChannel channel) {
        this.channel = channel;
    }

    @Override
    public final void run() {
        try {
            if (!this.channel.canYouReadMessageHistory()) {
                logger.warn("Unable to ingest text channel history due to lacking permissions. Ignoring. (Name: \""
                        + this.channel.getName() + "\" | ID: " + this.channel.getIdAsString() + " | Server ID: "
                        + this.channel.getServer().getIdAsString() + ")");
                return;
            }

            try (final Jedis jedis = Crabstero.getJedis()) {
                if (jedis.lrange(INGESTED_CHANNELS_KEY, 0, -1).contains(this.channel.getIdAsString())) {
                    return;
                } else {
                    final Pipeline pipeline = jedis.pipelined();
                    pipeline.lpush(INGESTED_CHANNELS_KEY, this.channel.getIdAsString());
                    logger.info("Starting ingestion of text channel history. (Name: \"" + this.channel.getName()
                            + "\" | ID: " + this.channel.getIdAsString() + " | Server ID: "
                            + this.channel.getServer().getIdAsString() + ")");
                }
            }

            try (final Stream<Message> history = this.channel.getMessagesAsStream()) {
                final Iterator<Message> iterator = history.iterator();
                int i = 0;
                while (iterator.hasNext()) {
                    i++;
                    MarkovChainMessages.ingestMessage(iterator.next());
                    if (i == Crabstero.maximumMessagesPerChannel) {
                        break;
                    }
                }

                logger.info("Ingestion of text channel history complete. " + i + " messages ingested. (Name: \""
                        + this.channel.getName() + "\" | ID: " + this.channel.getIdAsString() + " | Server ID: "
                        + this.channel.getServer().getIdAsString() + ")");
            }
        } catch (final Throwable exception) {
            logger.error("An error occured while ingesting text channel history! (Name: \"" + this.channel.getName()
                    + "\" | ID: " + this.channel.getIdAsString() + " | Server ID: "
                    + this.channel.getServer().getIdAsString() + ")", exception);
        }
    }
}