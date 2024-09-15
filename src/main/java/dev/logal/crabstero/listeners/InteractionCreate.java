/*
 * Copyright 2024 Logan Fick
 *
 * Licensed under the Apache License, Version 2.0 (the "License"); you may not use this file except in compliance with the License. You may obtain a copy of the License at: https://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software distributed under the License is distributed on an "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the License for the specific language governing permissions and limitations under the License.
 */

package dev.logal.crabstero.listeners;

import dev.logal.crabstero.Crabstero;
import dev.logal.crabstero.utils.FlagManager;
import org.javacord.api.entity.message.MessageFlag;
import org.javacord.api.event.interaction.InteractionCreateEvent;
import org.javacord.api.interaction.SlashCommandInteraction;
import org.javacord.api.interaction.callback.InteractionImmediateResponseBuilder;
import org.javacord.api.listener.interaction.InteractionCreateListener;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.util.Optional;

/**
 * Handles responding to interactions.
 */
public class InteractionCreate implements InteractionCreateListener {
    private static final String ALLOW_PINGS_FLAG = "allowPings";

    private static final Logger logger = LoggerFactory.getLogger(InteractionCreate.class);

    private final FlagManager flagManager;

    /**
     * Creates a new interaction create handler owned by a given instance of Crabstero.
     *
     * @param crabstero The instance of Crabstero.
     */
    public InteractionCreate(final Crabstero crabstero) {
        this.flagManager = new FlagManager(crabstero);
    }


    /**
     * Executes slash commands and responds to the users who run them.
     * <p>
     * All non-slash command interactions are ignored.
     *
     * @param event The event.
     */
    @Override
    public void onInteractionCreate(final InteractionCreateEvent event) {
        final Optional<SlashCommandInteraction> potentialCommandInteraction = event.getSlashCommandInteraction();

        // Is this interaction not a slash command?
        if (potentialCommandInteraction.isEmpty()) {
            return; // Only slash commands are used, so ignore this interaction.
        }

        final SlashCommandInteraction interaction = potentialCommandInteraction.get();
        final InteractionImmediateResponseBuilder response = interaction.createImmediateResponder();

        final String commandName = interaction.getFullCommandName();

        // Since access activity is involved, put all command execution in a try/catch so we always respond to the user if something bad happens.
        try {
            // `/pingme` command.
            if (commandName.equalsIgnoreCase("pingme")) {
                // Is this user opted in to receiving pings for mentions?
                if (flagManager.isFlagSet(interaction.getUser(), ALLOW_PINGS_FLAG)) {
                    // Yes. Revert their opt-in.
                    flagManager.clearFlag(interaction.getUser(), ALLOW_PINGS_FLAG);
                    response.setContent("I will no longer ping you for messages which mention you. If you decide to opt back in, run `/pingme` any time.");
                } else {
                    // No. Opt them in.
                    flagManager.setFlag(interaction.getUser(), ALLOW_PINGS_FLAG);
                    response.setContent("I will now ping you for messages which mention you. If you change your mind, run `/pingme` any time.");
                }

            } else {
                // The only way I think this could be executed is if a new command is registered with the API in the Crabstero class, but this class isn't reflected to know it exists.
                // As long as the Crabstero class and this class are in sync, I don't think this should ever happen.
                logger.warn("Received slash command interaction for command '{}' which is not implemented!", commandName);
                response.setContent("I don't know how to execute that command. Please contact the bot administrator as this shouldn't happen.");
            }
        } catch (Exception exception) {
            logger.error("An exception occurred while attempting to execute slash command responder for command '{}'.", commandName, exception);
            response.setContent("An error occurred while executing your command. Please try again later.");
        }

        // Make the slash command response only visible to the executor.
        response.setFlags(MessageFlag.EPHEMERAL);

        // Finally, fire off the response to the user!
        response.respond();
    }
}
