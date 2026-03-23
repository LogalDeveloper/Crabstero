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
cog loading, ingestion worker pool, and graceful shutdown.
"""

import asyncio
import logging
from typing import override

import discord
from discord import app_commands
from discord.ext import commands

from crabstero import __version__ as crabstero_version
from crabstero.cache import IngestCache
from crabstero.database import Database
from crabstero.listeners import interaction, message, server_events
from crabstero.metrics import (
    CHANNEL_INGESTION_ERRORS,
    DISCORD_EVENTS,
    DISCORD_LATENCY,
    GUILD_COUNT,
    INGESTION_BACKLOG,
    MetricsServer,
)
from crabstero.tasks.ingestion import ingest_channel

logger = logging.getLogger(__name__)

type IngestableChannel = discord.TextChannel | discord.VoiceChannel


class Crabstero(commands.Bot):
    """Central bot subclass that owns all lifecycle state.

    The database is opened in setup_hook and closed in close(). Background
    ingestion is handled by a bounded queue and a fixed worker pool.
    """

    def __init__(
        self,
        token: str,
        database_path: str,
        ingestion_workers: int = 4,
        ingest_only: bool = False,
        metrics_address: tuple[str, int] | None = None,
    ) -> None:
        """Configure intents, store configuration, and prepare ingestion queue state.

        :param token: The Discord bot token.
        :param database_path: The file path to the SQLite database.
        :param ingestion_workers: The number of concurrent ingestion workers.
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
        self._ingestion_worker_count = ingestion_workers
        self._ingest_only = ingest_only
        self._ingestion_queue: asyncio.Queue[IngestableChannel] = asyncio.Queue()
        self._ingestion_workers: list[asyncio.Task[None]] = []
        self._db: Database | None = None
        self.ingest_cache = IngestCache()

        self._metrics_address = metrics_address
        self._metrics_server: MetricsServer | None = None

        DISCORD_LATENCY.set_function(lambda: self.latency)
        GUILD_COUNT.set_function(lambda: len(self.guilds))
        INGESTION_BACKLOG.set_function(self._ingestion_queue.qsize)

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
        """Open the database, start ingestion workers, and load all cogs."""
        self._db = await Database.connect(self._database_path)
        self.ingest_cache.start()
        self._start_ingestion_workers()

        if self._metrics_address is not None:
            server = MetricsServer(*self._metrics_address)
            await server.start()
            self._metrics_server = server

        if not self._ingest_only:
            await interaction.setup(self)

        await message.setup(self)
        await server_events.setup(self)

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

    async def on_ready(self) -> None:
        """Log that the bot has started successfully."""
        logger.info("Crabstero started!")

    @override
    async def start(self, token: str = "", *, reconnect: bool = True) -> None:
        """Start the bot using the token provided at initialization."""
        await super().start(self._token, reconnect=reconnect)

    @override
    async def close(self) -> None:
        """Cancel ingestion workers, close the database, and then the bot connection."""
        if self.is_closed():
            return
        logger.info("Shutting down Crabstero...")
        for worker in self._ingestion_workers:
            worker.cancel()
        await asyncio.gather(*self._ingestion_workers, return_exceptions=True)
        self._ingestion_workers.clear()
        await self.ingest_cache.stop()
        if self._metrics_server is not None:
            await self._metrics_server.stop()
        if self._db is not None:
            await self._db.close()
        await super().close()

    def queue_channel_for_ingestion(self, channel: IngestableChannel) -> None:
        """Enqueue a single channel for background message history ingestion.

        :param channel: The channel to enqueue.
        """
        self._ingestion_queue.put_nowait(channel)

    def _start_ingestion_workers(self) -> None:
        """Spawn the fixed pool of ingestion worker tasks."""
        for i in range(self._ingestion_worker_count):
            task = asyncio.create_task(
                self._ingestion_worker(), name=f"ingestion-worker-{i}"
            )
            self._ingestion_workers.append(task)

    async def _ingestion_worker(self) -> None:
        """Loop forever pulling channels from the ingestion queue and ingesting them."""
        while True:
            channel = await self._ingestion_queue.get()
            try:
                await ingest_channel(channel, self.db)
            except Exception:
                CHANNEL_INGESTION_ERRORS.inc()
                logger.exception("Ingestion failed for channel %s", channel.id)
            finally:
                self._ingestion_queue.task_done()
