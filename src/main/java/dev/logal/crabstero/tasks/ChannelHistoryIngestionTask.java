/*
 * Copyright 2023 Logan Fick
 *
 * Licensed under the Apache License, Version 2.0 (the "License"); you may not use this file except in compliance with the License. You may obtain a copy of the License at: https://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software distributed under the License is distributed on an "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the License for the specific language governing permissions and limitations under the License.
 */

package dev.logal.crabstero.tasks;

import dev.logal.crabstero.Crabstero;
import dev.logal.crabstero.utils.MarkovChainMessages;
import dev.logal.crabstero.utils.RedisDatabase;
import org.javacord.api.entity.channel.ServerTextChannel;
import org.javacord.api.entity.message.Message;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.util.Iterator;
import java.util.stream.Stream;

/**
 * Bulk-ingests the message history of a channel.
 */
public final class ChannelHistoryIngestionTask implements Runnable {
    private static final String INGESTED_CHANNELS_KEY = "ingestedChannels"; // Key in redis for storing the IDs of channels which have been ingested.
    private static final int MAXIMUM_MESSAGES_PER_CHANNEL = 50000; // The maximum amount of historical messages to ingest per channel.

    private static final Logger logger = LoggerFactory.getLogger(ChannelHistoryIngestionTask.class);

    private final Crabstero crabstero;
    private final ServerTextChannel channel;

    /**
     * Creates a new task for ingesting the message history of a channel.
     *
     * @param channel   The channel to ingest.
     * @param crabstero The Crabstero instance running this task.
     */
    public ChannelHistoryIngestionTask(final ServerTextChannel channel, final Crabstero crabstero) {
        this.channel = channel;
        this.crabstero = crabstero;
    }

    /**
     * Runs the task. The task will be ended early if permissions do not allow ingesting this channel or if it has already been ingested in the past.
     */
    @Override
    public void run() {
        try {
            // Do permissions allow Crabstero to read message history in this channel?
            if (!this.channel.canYouReadMessageHistory()) {
                // No. Log the problem and stop further processing this channel.
                logger.warn("Unable to ingest text channel history due to lacking permissions. Ignoring. (Name: \"" + this.channel.getName() + "\" | ID: " + this.channel.getIdAsString() + " | Server ID: " + this.channel.getServer().getIdAsString() + ")");
                return;
            }


            // Have we already ingested this channel before?
            if (this.crabstero.getRedisDatabase().lrange(RedisDatabase.MAIN_DATABASE_ID, 0, INGESTED_CHANNELS_KEY, 0, -1).contains(this.channel.getIdAsString())) {
                // Yes. Stop further processing of this channel.
                return;
            } else {
                // No. Add this channel's ID to the list of ingested channels.
                this.crabstero.getRedisDatabase().lpush(RedisDatabase.MAIN_DATABASE_ID, 0, INGESTED_CHANNELS_KEY, this.channel.getIdAsString());

                // Log the start of the ingestion of this channel.
                logger.info("Starting ingestion of text channel history. (Name: \"" + this.channel.getName() + "\" | ID: " + this.channel.getIdAsString() + " | Server ID: " + this.channel.getServer().getIdAsString() + ")");
            }

            final MarkovChainMessages markovChainMessages = new MarkovChainMessages(this.crabstero);
            // Start streaming messages in this channel from newest to oldest.
            try (final Stream<Message> history = this.channel.getMessagesAsStream()) {
                final Iterator<Message> iterator = history.iterator();
                int i = 0;
                // While the stream still has messages...
                while (iterator.hasNext()) {
                    i++;

                    //Ingest the message.
                    markovChainMessages.ingestMessage(iterator.next());

                    // Have we reached the maximum amount of messages to ingest per channel?
                    if (i >= MAXIMUM_MESSAGES_PER_CHANNEL) {
                        // Yes. Stop ingesting.
                        break;
                    }
                }

                // Log the results of the ingestion.
                logger.info("Ingestion of text channel history complete. " + i + " messages ingested. (Name: \"" + this.channel.getName() + "\" | ID: " + this.channel.getIdAsString() + " | Server ID: " + this.channel.getServer().getIdAsString() + ")");
            }
        } catch (final Throwable exception) {
            // A problem occurred somewhere in this task. Log the problem.
            logger.error("An error occurred while ingesting text channel history! (Name: \"" + this.channel.getName() + "\" | ID: " + this.channel.getIdAsString() + " | Server ID: " + this.channel.getServer().getIdAsString() + ")", exception);
        }
    }
}