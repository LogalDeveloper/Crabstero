package dev.logal.crabstero.utils;

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

import java.security.SecureRandom;
import java.util.List;
import java.util.Random;

import org.javacord.api.entity.channel.TextChannel;
import org.javacord.api.entity.message.Message;
import org.javacord.api.entity.message.MessageAuthor;
import org.javacord.api.entity.message.MessageBuilder;
import org.javacord.api.entity.message.embed.Embed;
import org.javacord.api.entity.message.embed.EmbedBuilder;
import org.javacord.api.entity.message.mention.AllowedMentions;
import org.javacord.api.entity.message.mention.AllowedMentionsBuilder;
import org.javacord.api.util.logging.ExceptionLogger;

import dev.logal.crabstero.Crabstero;
import redis.clients.jedis.Jedis;

public class MarkovChainMessages {
    private static final AllowedMentions allowedMentions;
    private static final Random rng = new SecureRandom();

    static {
        final AllowedMentionsBuilder builder = new AllowedMentionsBuilder();
        builder.setMentionEveryoneAndHere(false);
        builder.setMentionRoles(false);
        builder.setMentionUsers(false);
        allowedMentions = builder.build();
    }

    private MarkovChainMessages() {
        throw new UnsupportedOperationException();
    }

    public static void replyToMessage(final Message message) {
        final TextChannel channel = message.getChannel();
        if (!channel.canYouWrite()) {
            return;
        }

        final long channelID = channel.getId();

        final MessageBuilder response = new MessageBuilder();
        final MarkovChain markovChain = new MarkovChain(channelID);

        response.replyTo(message);
        response.setContent(markovChain.generate(750, 1000));

        if (rng.nextDouble() >= 0.95 && channel.canYouEmbedLinks()) {
            final EmbedBuilder embed = new EmbedBuilder();
            embed.setTitle(markovChain.generate(100, 150));
            embed.setDescription(markovChain.generate(100, 200));
            try (final Jedis jedis = Crabstero.getJedis()) {
                final List<String> embedImageURLs = jedis.lrange(channelID + ":images", 0, -1);

                if (embedImageURLs.size() > 0) {
                    embed.setImage(embedImageURLs.get(rng.nextInt(embedImageURLs.size())));
                }
            }

            embed.setFooter("Crabstero is a logal.dev project", "https://logal.dev/images/logo.png");
            response.setEmbed(embed);
        }

        response.setAllowedMentions(allowedMentions);
        response.send(channel).exceptionally(ExceptionLogger.get());
    }

    public static void ingestMessage(final Message message) {
        final MessageAuthor author = message.getAuthor();
        if (author.isBotUser() || author.isWebhook()
                || message.getMentionedUsers().contains(message.getApi().getYourself())) {
            return;
        }

        final long channelID = message.getChannel().getId();
        final MarkovChain markovChain = new MarkovChain(channelID);

        markovChain.ingest(message.getContent());

        for (final Embed embed : message.getEmbeds()) {
            ingestEmbed(channelID, embed);
        }
    }

    public static void ingestEmbed(final long channelID, final Embed embed) {
        final MarkovChain markovChain = new MarkovChain(channelID);

        embed.getTitle().ifPresent((title) -> {
            markovChain.ingest(title);
        });

        embed.getDescription().ifPresent((description) -> {
            markovChain.ingest(description);
        });

        embed.getImage().ifPresent((image) -> {
            try (final Jedis jedis = Crabstero.getJedis()) {
                jedis.lpush(channelID + ":images", image.getUrl().toString());
            }
        });
    }
}