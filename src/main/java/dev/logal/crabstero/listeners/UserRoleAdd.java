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

import org.javacord.api.event.server.role.UserRoleAddEvent;
import org.javacord.api.listener.server.role.UserRoleAddListener;

import dev.logal.crabstero.Crabstero;
import dev.logal.crabstero.tasks.ChannelHistoryIngestionTask;

public final class UserRoleAdd implements UserRoleAddListener {
    @Override
    public final void onUserRoleAdd(final UserRoleAddEvent event) {
        if (event.getUser().isYourself()) {
            event.getServer().getTextChannels().forEach((channel) -> {
                Crabstero.submitTask(new ChannelHistoryIngestionTask(channel));
            });
        }
    }
}