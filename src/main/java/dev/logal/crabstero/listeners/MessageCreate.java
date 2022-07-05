/*
 * Copyright 2022 Logan Fick
 *
 * Licensed under the Apache License, Version 2.0 (the "License"); you may not use this file except in compliance with the License. You may obtain a copy of the License at: https://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software distributed under the License is distributed on an "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the License for the specific language governing permissions and limitations under the License.
 */

package dev.logal.crabstero.listeners;

import dev.logal.crabstero.utils.MarkovChainMessages;
import org.javacord.api.entity.channel.TextChannel;
import org.javacord.api.entity.message.Message;
import org.javacord.api.entity.message.MessageAuthor;
import org.javacord.api.entity.message.MessageType;
import org.javacord.api.event.message.MessageCreateEvent;
import org.javacord.api.listener.message.MessageCreateListener;

public final class MessageCreate implements MessageCreateListener {
    @Override
    public void onMessageCreate(final MessageCreateEvent event) {
        // Is this message being created outside of a server text channel or a server thread channel?
        final TextChannel channel = event.getChannel();
        if (channel.asServerTextChannel().isEmpty() && channel.asServerThreadChannel().isEmpty()) {
            return; // Yes. Ignore it.
        }

        // Is the user creating this message another bot, a webhook, or Crabstero itself?
        final MessageAuthor author = event.getMessageAuthor();
        if (author.isBotUser() || author.isWebhook() || author.isYourself()) {
            return; // Yes. Ignore it.
        }

        // Is this message mentioning Crabstero?
        final Message message = event.getMessage();
        if (message.getMentionedUsers().contains(event.getApi().getYourself())) {
            MarkovChainMessages.replyToMessage(message); // Yes. Respond to it.
            return; // Prevent further processing of the message.
        }

        // Is this message a normal message outside of a server thread channel?
        if (message.getType() == MessageType.NORMAL && channel.asServerThreadChannel().isEmpty()) {
            MarkovChainMessages.ingestMessage(message); // Yes. Ingest the message.
        }
    }
}