# Copyright 2026 Logan Fick
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""The simple nonversation Discord bot.

Provides the Crabstero subclass that owns the full bot lifecycle: database connection,
cog loading, background ingestion, and graceful shutdown.
"""

import asyncio
import logging
from typing import override

import discord
from discord import app_commands
from discord.app_commands import AppCommandError, CommandInvokeError
from discord.ext import commands

from crabstero import __version__ as crabstero_version
from crabstero.cache import IngestCache
from crabstero.database import Database
from crabstero.listeners import interaction, message, server_events
from crabstero.metrics import (
    DISCORD_EVENTS,
    DISCORD_LATENCY,
    ERRORS,
    GUILD_COUNT,
    INGESTION_ACTIVE,
    MetricsServer,
)
from crabstero.tasks.ingestion import ingest_channel

logger = logging.getLogger(__name__)

type IngestableChannel = discord.TextChannel | discord.VoiceChannel

_MAX_CONCURRENT_INGESTIONS = 4


class Crabstero(commands.Bot):
    """Central bot subclass that owns all lifecycle state.

    The database is opened in setup_hook and closed in close(). Background
    ingestion is handled by semaphore-bounded dynamic tasks.
    """

    def __init__(
        self,
        token: str,
        database_path: str,
        ingest_only: bool = False,
        metrics_address: tuple[str, int] | None = None,
    ) -> None:
        """Configure intents, store configuration, and prepare ingestion state.

        :param token: The Discord bot token.
        :param database_path: The file path to the SQLite database.
        :param ingest_only: When True, the bot only ingests data and never responds.
        :param metrics_address: Optional (host, port) for the Prometheus metrics server.
        """
        intents = discord.Intents.default()
        intents.guilds = True
        intents.guild_messages = True
        intents.message_content = True

        super().__init__(
            command_prefix=[],
            intents=intents,
            max_messages=None,  # Disables the message cache.
        )

        self._token = token
        self._database_path = database_path
        self._ingest_only = ingest_only
        self._ingestion_semaphore = asyncio.Semaphore(_MAX_CONCURRENT_INGESTIONS)
        self._ingestion_tasks: dict[int, asyncio.Task[None]] = {}
        self._db: Database | None = None
        self.ingest_cache = IngestCache()

        self._metrics_address = metrics_address
        self._metrics_server: MetricsServer | None = None

        DISCORD_LATENCY.set_function(lambda: self.latency)
        GUILD_COUNT.set_function(lambda: len(self.guilds))
        INGESTION_ACTIVE.set_function(lambda: len(self._ingestion_tasks))

        repo_url = "https://git.logal.dev/LogalDeveloper/Crabstero"
        self.http.user_agent = f"DiscordBot ({repo_url}, {crabstero_version})"

    @property
    def db(self) -> Database:
        """The active database connection.

        :raises RuntimeError: If accessed before :meth:`setup_hook` has run.
        """
        if self._db is None:
            raise RuntimeError("Database is not initialized")
        return self._db

    @property
    def ingest_only(self) -> bool:
        """Whether the bot is running in ingest-only mode."""
        return self._ingest_only

    @override
    async def setup_hook(self) -> None:
        """Open the database, start the ingest cache, and load all cogs."""
        self._db = await Database.connect(self._database_path)
        self.ingest_cache.start()

        if self._metrics_address is not None:
            server = MetricsServer(*self._metrics_address)
            await server.start()
            self._metrics_server = server

        if not self._ingest_only:
            await interaction.setup(self)

        await message.setup(self)
        await server_events.setup(self)

        @self.tree.error
        async def on_app_command_error(
            interaction: discord.Interaction, error: AppCommandError
        ) -> None:
            original = (
                error.original if isinstance(error, CommandInvokeError) else error
            )
            command_name = (
                interaction.command.name if interaction.command else "unknown"
            )

            ERRORS.labels(source="command").inc()
            logger.error(
                "Unhandled exception in app command '%s'.",
                command_name,
                exc_info=original,
            )

            if self._metrics_server is not None:
                reply = (
                    "An error occurred while processing this command."
                    " The developer has been notified,"
                    " please try again later."
                )
            else:
                reply = (
                    "An error occurred while processing this command."
                    " Please try again later."
                )

            try:
                if interaction.response.is_done():
                    await interaction.followup.send(reply, ephemeral=True)
                else:
                    await interaction.response.send_message(reply, ephemeral=True)
            except discord.HTTPException:
                logger.debug(
                    "Failed to send error response for command '%s'.",
                    command_name,
                )

        if not self._ingest_only:
            # Only sync slash commands if the registered commands
            # differ from local definitions.
            local_commands = {
                cmd.name: cmd.description
                for cmd in self.tree.get_commands()
                if isinstance(cmd, (app_commands.Command, app_commands.Group))
            }
            try:
                remote_commands = {
                    cmd.name: cmd.description
                    for cmd in await self.tree.fetch_commands()
                }
            except discord.HTTPException:
                remote_commands = {}

            if local_commands != remote_commands:
                logger.info("Slash command tree has changed, syncing with Discord.")
                await self.tree.sync()

    @override
    def dispatch(self, event: str, /, *args: object, **kwargs: object) -> None:
        """Dispatch an event, incrementing the events counter.

        :param event: The event name.
        """
        DISCORD_EVENTS.labels(event=event).inc()
        super().dispatch(event, *args, **kwargs)

    @override
    async def on_error(
        self, event_method: str, /, *args: object, **kwargs: object
    ) -> None:
        """Increment the global error counter for event listener exceptions.

        :param event_method: The name of the event that raised the exception.
        """
        ERRORS.labels(source=event_method).inc()
        logger.error("Unhandled exception in %s.", event_method, exc_info=True)

    async def on_ready(self) -> None:
        """Log that the bot has started successfully."""
        logger.info("Crabstero started!")

    @override
    async def start(self, token: str = "", *, reconnect: bool = True) -> None:
        """Start the bot using the token provided at initialization."""
        await super().start(self._token, reconnect=reconnect)

    @override
    async def close(self) -> None:
        """Cancel ingestion tasks, close the database, and then the bot connection."""
        if self.is_closed():
            return
        logger.info("Shutting down Crabstero...")
        tasks = list(self._ingestion_tasks.values())
        self._ingestion_tasks.clear()
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await self.ingest_cache.stop()
        if self._metrics_server is not None:
            await self._metrics_server.stop()
        if self._db is not None:
            await self._db.close()
        await super().close()

    def queue_channel_for_ingestion(self, channel: IngestableChannel) -> None:
        """Create a background task to ingest a single channel.

        Duplicate requests for a channel that is already in-flight are ignored.
        Concurrency is bounded by the ingestion semaphore.

        :param channel: The channel to ingest.
        """
        if channel.id in self._ingestion_tasks:
            return
        task = asyncio.create_task(
            self._ingest_one(channel), name=f"ingest-{channel.id}"
        )
        self._ingestion_tasks[channel.id] = task
        task.add_done_callback(lambda t: self._on_ingestion_done(channel.id, t))

    async def _ingest_one(self, channel: IngestableChannel) -> None:
        """Acquire the semaphore and ingest one channel."""
        async with self._ingestion_semaphore:
            await ingest_channel(channel, self.db)

    def _on_ingestion_done(self, channel_id: int, task: asyncio.Task[None]) -> None:
        """Clean up a finished ingestion task and log any errors."""
        self._ingestion_tasks.pop(channel_id, None)
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None:
            ERRORS.labels(source="ingestion").inc()
            logger.error("Ingestion task failed.", exc_info=exc)


class TrackedView(discord.ui.View):
    """Base View that increments the global error counter on failures."""

    @override
    async def on_error(
        self,
        interaction: discord.Interaction,
        error: Exception,
        item: discord.ui.Item[TrackedView],
        /,
    ) -> None:
        """Increment the error counter and log the exception.

        :param interaction: The interaction that led to the failure.
        :param error: The exception that was raised.
        :param item: The item that failed the dispatch.
        """
        ERRORS.labels(source="view").inc()
        logger.error(
            "Unhandled exception in view %r for item %r.",
            self,
            item,
            exc_info=error,
        )


class TrackedModal(discord.ui.Modal):
    """Base Modal that increments the global error counter on failures."""

    @override
    async def on_error(
        self,
        interaction: discord.Interaction,
        error: Exception,
        item: discord.ui.Item[TrackedModal] | None = None,
        /,
    ) -> None:
        """Increment the error counter and log the exception.

        :param interaction: The interaction that led to the failure.
        :param error: The exception that was raised.
        :param item: Unused. Present for BaseView signature compatibility.
        """
        ERRORS.labels(source="modal").inc()
        logger.error(
            "Unhandled exception in modal %r.",
            self,
            exc_info=error,
        )
