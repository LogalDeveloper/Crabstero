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

import java.util.Optional;

import org.javacord.api.entity.channel.ServerTextChannel;
import org.javacord.api.entity.message.Message;
import org.javacord.api.entity.message.MessageAuthor;
import org.javacord.api.entity.message.MessageType;
import org.javacord.api.event.message.MessageCreateEvent;
import org.javacord.api.listener.message.MessageCreateListener;

import dev.logal.crabstero.utils.MarkovChainMessages;

public final class MessageCreate implements MessageCreateListener {
    @Override
    public final void onMessageCreate(final MessageCreateEvent event) {
        final MessageAuthor author = event.getMessageAuthor();
        final Optional<ServerTextChannel> serverTextChannel = event.getChannel().asServerTextChannel();
        if (!serverTextChannel.isPresent() || author.isBotUser() || author.isWebhook() || author.isYourself()) {
            return;
        }

        final Message message = event.getMessage();
        if (message.getMentionedUsers().contains(event.getApi().getYourself())) {
            MarkovChainMessages.replyToMessage(message);
            return;
        }

        if (message.getType() == MessageType.NORMAL) {
            MarkovChainMessages.ingestMessage(message);
        }
    }
}