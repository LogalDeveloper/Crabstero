package dev.logal.crabstero.listeners;

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

import java.awt.Color;

import org.javacord.api.entity.message.embed.EmbedBuilder;
import org.javacord.api.entity.server.Server;
import org.javacord.api.event.server.ServerJoinEvent;
import org.javacord.api.listener.server.ServerJoinListener;
import org.javacord.api.util.logging.ExceptionLogger;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import dev.logal.crabstero.Crabstero;
import dev.logal.crabstero.tasks.ChannelHistoryIngestionTask;

public final class ServerJoin implements ServerJoinListener {
    private static final Logger logger = LoggerFactory.getLogger(ServerJoin.class);

    @Override
    public final void onServerJoin(final ServerJoinEvent event) {
        final Server server = event.getServer();
        logger.info("Joined new server! (Name: \"" + server.getName() + "\" | ID: " + server.getIdAsString() + ")");

        final EmbedBuilder embed = new EmbedBuilder();
        embed.setTitle("Joined New Server");
        embed.setColor(new Color(255, 165, 0));
        embed.addField(server.getName() + " (" + server.getIdAsString() + ")", server.getMemberCount() + " members");
        server.getIcon().ifPresent((icon) -> {
            embed.setImage(icon.getUrl().toString());
        });
        embed.setFooter(event.getApi().getServers().size() + " total servers");
        event.getApi().getOwner().thenAcceptAsync((owner) -> {
            owner.sendMessage(embed).exceptionally(ExceptionLogger.get());
        });

        server.getTextChannels().forEach((channel) -> {
            Crabstero.submitTask(new ChannelHistoryIngestionTask(channel));
        });
    }
}