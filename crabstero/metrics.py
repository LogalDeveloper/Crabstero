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

"""Prometheus metrics definitions and HTTP server for Crabstero.

All metric objects are defined at module level using the default global
registry. The MetricsServer class wraps an aiohttp application that serves
the ``/metrics`` scrape endpoint.
"""

import logging

from aiohttp import web
from prometheus_client import Counter, Gauge, Histogram, Info
from prometheus_client.aiohttp import make_aiohttp_handler

from crabstero import __version__

logger = logging.getLogger(__name__)

_FAST_BUCKETS = (0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0)
_SLOW_BUCKETS = (0.1, 0.5, 1.0, 5.0, 10.0, 30.0, 60.0, 120.0, 300.0, 600.0)
_LENGTH_BUCKETS = (10, 50, 100, 200, 500, 750, 1000, 1500, 2000)
_MESSAGES_BUCKETS = (100, 500, 1000, 5000, 10000, 25000, 50000)

BUILD_INFO = Info("crabstero_build", "Build information")
BUILD_INFO.info({"version": __version__})

MESSAGES_INGESTED = Counter(
    "crabstero_messages_ingested_total",
    "Messages ingested into a Markov chain",
)
REPLIES_SENT = Counter(
    "crabstero_replies_sent_total",
    "Markov replies sent",
)
EMBEDS_GENERATED = Counter(
    "crabstero_embeds_generated_total",
    "Embeds included in replies",
)
MESSAGES_PROCESSED = Counter(
    "crabstero_messages_processed_total",
    "Messages seen by the listener",
)
CHANNELS_INGESTED = Counter(
    "crabstero_channels_ingested_total",
    "Channels fully ingested successfully",
)
CHANNEL_INGESTION_ERRORS = Counter(
    "crabstero_channel_ingestion_errors_total",
    "Channel ingestions that failed",
)
PINGME_OPTED_IN = Counter(
    "crabstero_pingme_opted_in_total",
    "/pingme opt-ins",
)
PINGME_OPTED_OUT = Counter(
    "crabstero_pingme_opted_out_total",
    "/pingme opt-outs",
)
PINGME_ERRORS = Counter(
    "crabstero_pingme_errors_total",
    "/pingme exceptions",
)
DISCORD_EVENTS = Counter(
    "crabstero_discord_events_total",
    "Discord gateway events received",
    ["event"],
)

DISCORD_LATENCY = Gauge(
    "crabstero_discord_latency_seconds",
    "Discord WebSocket heartbeat latency",
)
GUILD_COUNT = Gauge(
    "crabstero_guild_count",
    "Guilds the bot is currently in",
)
INGESTION_BACKLOG = Gauge(
    "crabstero_ingestion_backlog_channels",
    "Channels currently queued for ingestion",
)

MESSAGE_INGESTION_DURATION = Histogram(
    "crabstero_message_ingestion_duration_seconds",
    "Time to ingest one message",
    buckets=_FAST_BUCKETS,
)
CHANNEL_INGESTION_DURATION = Histogram(
    "crabstero_channel_ingestion_duration_seconds",
    "Time to fully ingest a channel",
    buckets=_SLOW_BUCKETS,
)
GENERATION_DURATION = Histogram(
    "crabstero_generation_duration_seconds",
    "Time to generate a Markov response",
    buckets=_FAST_BUCKETS,
)
GENERATED_MESSAGE_LENGTH = Histogram(
    "crabstero_generated_message_length_characters",
    "Length of generated reply bodies in characters",
    buckets=_LENGTH_BUCKETS,
)
CHANNEL_INGESTION_MESSAGES = Histogram(
    "crabstero_channel_ingestion_messages",
    "Messages ingested per channel run",
    buckets=_MESSAGES_BUCKETS,
)


class MetricsServer:
    """HTTP server that exposes a Prometheus ``/metrics`` scrape endpoint.

    Uses ``aiohttp.web.AppRunner`` and ``TCPSite`` for async-native serving.
    The handler is provided by ``prometheus_client.aiohttp.make_aiohttp_handler``,
    which handles compression and content negotiation automatically.
    """

    def __init__(self, host: str, port: int) -> None:
        """Store the listen address.

        :param host: The hostname or IP to bind to.
        :param port: The TCP port to bind to. Use 0 for OS-assigned.
        """
        self._host = host
        self._port = port
        self._runner: web.AppRunner | None = None

    @property
    def port(self) -> int:
        """The TCP port the server is bound to.

        After ``start()``, this reflects the actual port (useful when
        the constructor received port 0 for OS assignment).
        """
        return self._port

    async def start(self) -> None:
        """Create the aiohttp application and start listening."""
        if self._runner is not None:
            raise RuntimeError("Metrics server is already running")
        app = web.Application()
        app.router.add_get("/metrics", make_aiohttp_handler())
        self._runner = web.AppRunner(app)
        await self._runner.setup()
        site = web.TCPSite(self._runner, self._host, self._port)
        await site.start()

        # Resolve the actual bound port when the OS assigned one.
        if self._port == 0:
            self._port = self._runner.addresses[0][1]

        logger.info("Metrics server listening on %s:%d.", self._host, self._port)

    async def stop(self) -> None:
        """Shut down the HTTP server and release resources."""
        if self._runner is not None:
            await self._runner.cleanup()
            self._runner = None
            logger.info("Metrics server stopped.")
