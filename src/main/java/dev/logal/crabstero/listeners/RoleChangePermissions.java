/*
 * Copyright 2022 Logan Fick
 *
 * Licensed under the Apache License, Version 2.0 (the "License"); you may not use this file except in compliance with the License. You may obtain a copy of the License at: https://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software distributed under the License is distributed on an "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the License for the specific language governing permissions and limitations under the License.
 */

package dev.logal.crabstero.listeners;

import dev.logal.crabstero.Crabstero;
import dev.logal.crabstero.tasks.ChannelHistoryIngestionTask;
import org.javacord.api.event.server.role.RoleChangePermissionsEvent;
import org.javacord.api.listener.server.role.RoleChangePermissionsListener;

/**
 * Handles roles with changing permissions.
 */
public final class RoleChangePermissions implements RoleChangePermissionsListener {
    private final Crabstero crabstero;

    /**
     * Creates a new role change permission handler owned by a given instance of Crabstero.
     *
     * @param crabstero The instance of Crabstero.
     */
    public RoleChangePermissions(final Crabstero crabstero) {
        this.crabstero = crabstero;
    }

    /**
     * Queues all text channels for message history ingestion when role permissions change.
     *
     * @param event The event.
     */
    @Override
    public void onRoleChangePermissions(final RoleChangePermissionsEvent event) {
        // Does the updated role include Crabstero as a member?
        if (event.getRole().hasUser(event.getApi().getYourself())) {
            // Queue every text channel for message history ingestion.
            event.getServer().getTextChannels().forEach((channel) -> {
                this.crabstero.getWorkerPool().submit(new ChannelHistoryIngestionTask(channel, crabstero));
            });
        }
    }
}