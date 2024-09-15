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
import org.javacord.api.event.server.role.UserRoleAddEvent;
import org.javacord.api.listener.server.role.UserRoleAddListener;

/**
 * Handles when roles are added to users.
 */
public final class UserRoleAdd implements UserRoleAddListener {
    private final Crabstero crabstero;

    /**
     * Creates a new user role add handler owned by a given instance of Crabstero.
     *
     * @param crabstero The instance of Crabstero.
     */
    public UserRoleAdd(final Crabstero crabstero) {
        this.crabstero = crabstero;
    }

    /**
     * Queues all text channels for message history ingestion when added to a new role.
     *
     * @param event The event.
     */
    @Override
    public void onUserRoleAdd(final UserRoleAddEvent event) {
        // Is the user who was assigned the role Crabstero?
        if (event.getUser().isYourself()) {
            // Queue every textable channel for message history ingestion.
            event.getServer().getRegularChannels().forEach((channel) -> {
                if (channel instanceof final TextableRegularServerChannel textableChannel) {
                    this.crabstero.getWorkerPool().submit(new ChannelHistoryIngestionTask(textableChannel, crabstero));
                }
            });
        }
    }
}