/*
 * Copyright 2023 Logan Fick
 *
 * Licensed under the Apache License, Version 2.0 (the "License"); you may not use this file except in compliance with the License. You may obtain a copy of the License at: https://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software distributed under the License is distributed on an "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the License for the specific language governing permissions and limitations under the License.
 */

package dev.logal.crabstero.utils;

import dev.logal.crabstero.Crabstero;
import org.javacord.api.entity.channel.TextChannel;
import org.javacord.api.entity.message.Message;
import org.javacord.api.entity.message.MessageAuthor;
import org.javacord.api.entity.message.MessageBuilder;
import org.javacord.api.entity.message.embed.Embed;
import org.javacord.api.entity.message.embed.EmbedBuilder;
import org.javacord.api.entity.message.mention.AllowedMentions;
import org.javacord.api.entity.message.mention.AllowedMentionsBuilder;
import org.javacord.api.util.logging.ExceptionLogger;

import java.security.SecureRandom;
import java.util.List;
import java.util.Random;

/**
 * Assists with generating Discord messages in response to other users and ingesting raw messages.
 */
public class MarkovChainMessages {
    private static final String IMAGE_URLS_KEY = "imageURLS";

    private final Crabstero crabstero;
    private final AllowedMentions allowedMentions;
    private final Random rng = new SecureRandom();

    public MarkovChainMessages(final Crabstero crabstero) {
        this.crabstero = crabstero;

        // Set up an allowed mentions filter which blocks any mentions from generating notifications to users.
        final AllowedMentionsBuilder builder = new AllowedMentionsBuilder();
        builder.setMentionEveryoneAndHere(false);
        builder.setMentionRoles(false);
        builder.setMentionUsers(false);
        this.allowedMentions = builder.build();
    }

    /**
     * Sends a new message in Discord in response to a given message.
     *
     * @param message The message prompting the response.
     */
    public void replyToMessage(final Message message) {
        final TextChannel channel = message.getChannel();

        // Does Crabstero have permissions to write to the channel the message was sent in?
        if (!channel.canYouWrite()) {
            // No. Give up.
            return;
        }

        final long channelID;
        // Is this channel a thread?
        if (channel.asServerThreadChannel().isPresent()) {
            // Yes. Store the ID of the parent channel for this thread.
            channelID = channel.asServerThreadChannel().get().getParent().getId();
        } else {
            // No. Store the ID of the channel the message was sent in.
            channelID = channel.getId();
        }


        final MessageBuilder response = new MessageBuilder();
        final MarkovChain markovChain = new MarkovChain(channelID, this.crabstero);

        // Tell Discord which message Crabstero is replying to
        response.replyTo(message);

        // Generate a new body.
        response.setContent(markovChain.generate(750, 1000));

        // Was a random number greater than 0.95 (5% of the time) and does Crabstero have permission to use embeds in this channel?
        if (this.rng.nextDouble() >= 0.95 && channel.canYouEmbedLinks()) {
            // Yes. Generate an embed with a random title and description.
            final EmbedBuilder embed = new EmbedBuilder();
            embed.setTitle(markovChain.generate(200, 300));
            embed.setDescription(markovChain.generate(300, 500));

            // If image URLs are known for this channel, chose a random one and attach to the embed.
            final List<String> embedImageURLs = this.crabstero.getRedisDatabase().lrange(RedisDatabase.MAIN_DATABASE_ID, channelID, IMAGE_URLS_KEY, 0, 499);  // TODO: What's a good stop value? 0 seems like a potential DoS vector.

            if (embedImageURLs.size() > 0) {
                embed.setImage(embedImageURLs.get(this.rng.nextInt(embedImageURLs.size())));
            }

            // Add a watermark to the footer of the embed.
            embed.setFooter("Crabstero is a logal.dev project", "https://logal.dev/images/logo.png");

            // Attach the embed to the response.
            response.setEmbed(embed);
        }

        // Set the allowed mentions filter which blocks all mentions from generating notifications.
        response.setAllowedMentions(allowedMentions);

        // Send the response.
        response.send(channel).exceptionally(ExceptionLogger.get());
    }

    /**
     * Ingests a given message into its channel's Markov chain.
     *
     * @param message The message to ingest.
     */
    public void ingestMessage(final Message message) {
        final MessageAuthor author = message.getAuthor();
        // Is the author of the message a bot, a webhook, or mentioning Crabstero?
        if (author.isBotUser() || author.isWebhook() || message.getMentionedUsers().contains(message.getApi().getYourself())) {
            // Yes. Ignore it.
            return;
        }

        // Get the Markov chain for the message's text channel.
        final long channelID = message.getChannel().getId();
        final MarkovChain markovChain = new MarkovChain(channelID, this.crabstero);

        // Ingest the message content into the Markov chain.
        markovChain.ingest(message.getContent());

        // If the message has embeds, ingest each one individually.
        for (final Embed embed : message.getEmbeds()) {
            ingestEmbed(channelID, embed);
        }
    }

    /**
     * Ingests a given embed into a given channel's Markov chain.
     *
     * @param channelID The ID of the channel to use for the Markov Chain.
     * @param embed     The embed to ingest.
     */
    public void ingestEmbed(final long channelID, final Embed embed) {
        // Get the Markov chain for the given channel ID.
        final MarkovChain markovChain = new MarkovChain(channelID, this.crabstero);

        // If the embed has a title or description, ingest each one separately.
        embed.getTitle().ifPresent(markovChain::ingest);
        embed.getDescription().ifPresent(markovChain::ingest);

        // If the embed has an image, store the URL to the image.
        embed.getImage().ifPresent((image) -> {
            this.crabstero.getRedisDatabase().lpush(RedisDatabase.MAIN_DATABASE_ID, channelID, IMAGE_URLS_KEY, image.getUrl().toString());
        });
    }
}