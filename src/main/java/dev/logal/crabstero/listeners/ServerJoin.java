/*
 * Copyright 2024 Logan Fick
 *
 * Licensed under the Apache License, Version 2.0 (the "License"); you may not use this file except in compliance with the License. You may obtain a copy of the License at: https://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software distributed under the License is distributed on an "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the License for the specific language governing permissions and limitations under the License.
 */

package dev.logal.crabstero.listeners;

import dev.logal.crabstero.Crabstero;
import dev.logal.crabstero.tasks.ChannelHistoryIngestionTask;
import org.javacord.api.entity.channel.TextableRegularServerChannel;
import org.javacord.api.entity.message.embed.EmbedBuilder;
import org.javacord.api.entity.server.Server;
import org.javacord.api.event.server.ServerJoinEvent;
import org.javacord.api.listener.server.ServerJoinListener;
import org.javacord.api.util.logging.ExceptionLogger;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.awt.Color;

/**
 * Handles joining new servers.
 */
public final class ServerJoin implements ServerJoinListener {
    private static final Logger logger = LoggerFactory.getLogger(ServerJoin.class);

    private final Crabstero crabstero;

    /**
     * Creates a new server join handler owned by a given instance of Crabstero.
     *
     * @param crabstero The instance of Crabstero.
     */
    public ServerJoin(final Crabstero crabstero) {
        this.crabstero = crabstero;
    }

    /**
     * Queues all text channels for message history ingestion when joining a new server.
     *
     * @param event The event.
     */
    @Override
    public void onServerJoin(final ServerJoinEvent event) {
        // Log the server join.
        final Server server = event.getServer();
        logger.info("Joined new server! (Name: \"" + server.getName() + "\" | ID: " + server.getIdAsString() + ")");

        // Start building an embed with basic server information.
        final EmbedBuilder embed = new EmbedBuilder();
        embed.setTitle("Joined New Server");
        embed.setColor(new Color(255, 165, 0));
        embed.addField(server.getName() + " (" + server.getIdAsString() + ")", server.getMemberCount() + " members");
        server.getIcon().ifPresent((icon) -> embed.setImage(icon.getUrl().toString()));

        // Add the current total server count.
        embed.setFooter(event.getApi().getServers().size() + " total servers");

        // Send this embed to the bot owner.
        event.getApi().getOwner().ifPresent((owner) -> owner.thenAcceptAsync((user) -> user.sendMessage(embed).exceptionally(ExceptionLogger.get())));

        // Queue every textable channel for message history ingestion.
        event.getServer().getRegularChannels().forEach((channel) -> {
            if (channel instanceof final TextableRegularServerChannel textableChannel) {
                this.crabstero.getWorkerPool().submit(new ChannelHistoryIngestionTask(textableChannel, crabstero));
            }
        });
    }
}