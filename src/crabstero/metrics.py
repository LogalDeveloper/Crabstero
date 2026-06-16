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

import contextlib
import errno
import logging
import socket
import stat
from dataclasses import dataclass
from pathlib import Path

from aiohttp import web
from prometheus_client import Counter, Gauge, Histogram, Info
from prometheus_client.aiohttp import make_aiohttp_handler

from crabstero import __version__

logger = logging.getLogger(__name__)

_FAST_BUCKETS = (0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0)
_SLOW_BUCKETS = (0.1, 0.5, 1.0, 5.0, 10.0, 30.0, 60.0, 120.0, 300.0, 600.0)
_MESSAGES_BUCKETS = (100, 500, 1000, 5000, 10000, 25000, 50000)


@dataclass(frozen=True, slots=True)
class TcpMetricsAddress:
    """TCP listen address for the metrics server."""

    host: str
    port: int


@dataclass(frozen=True, slots=True)
class UnixMetricsAddress:
    """Unix socket listen address for the metrics server."""

    path: str
    mode: int | None = None


type MetricsAddress = TcpMetricsAddress | UnixMetricsAddress


BUILD_INFO = Info("crabstero_build", "Build information")
BUILD_INFO.info({"version": __version__})

MESSAGES_UNINGESTED = Counter(
    "crabstero_messages_uningested_total",
    "Messages uningested from a Markov chain after deletion",
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
PINGME = Counter(
    "crabstero_pingme_total",
    "/pingme command outcomes",
    ["outcome"],
)
PINGME.labels(outcome="opted_in")
PINGME.labels(outcome="opted_out")

FORGETME = Counter(
    "crabstero_forgetme_total",
    "/forgetme command outcomes",
    ["outcome"],
)
FORGETME.labels(outcome="completed")
FORGETME.labels(outcome="cancelled")
FORGETME.labels(outcome="timeout")
FORGETME.labels(outcome="already_forgotten")

ERRORS = Counter(
    "crabstero_errors_total",
    "Unhandled exceptions by source",
    ["source"],
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
INGESTION_ACTIVE = Gauge(
    "crabstero_ingestion_active_channels",
    "Channels pending or in-progress for ingestion",
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
CHANNEL_INGESTION_MESSAGES = Histogram(
    "crabstero_channel_ingestion_messages",
    "Messages iterated per channel ingestion run",
    buckets=_MESSAGES_BUCKETS,
)


class MetricsServer:
    """HTTP server that exposes a Prometheus ``/metrics`` scrape endpoint.

    Uses ``aiohttp.web.AppRunner`` with TCP or Unix socket sites for
    async-native serving.
    The handler is provided by ``prometheus_client.aiohttp.make_aiohttp_handler``,
    which handles compression and content negotiation automatically.
    """

    __slots__ = ("_address", "_port", "_runner")

    def __init__(self, address: MetricsAddress) -> None:
        """Store the listen address.

        :param address: TCP or Unix socket address to bind to.
        """
        self._address = address
        self._port = address.port if isinstance(address, TcpMetricsAddress) else None
        self._runner: web.AppRunner | None = None

    @property
    def port(self) -> int:
        """The TCP port the server is bound to.

        After ``start()``, this reflects the actual port (useful when
        the constructor received port 0 for OS assignment).
        """
        if self._port is None:
            msg = "Unix socket metrics server does not have a TCP port"
            raise RuntimeError(msg)
        return self._port

    async def start(self) -> None:
        """Create the aiohttp application and start listening."""
        if self._runner is not None:
            msg = "Metrics server is already running"
            raise RuntimeError(msg)
        app = web.Application()
        app.router.add_get("/metrics", make_aiohttp_handler())
        self._runner = web.AppRunner(app)
        await self._runner.setup()
        try:
            if isinstance(self._address, TcpMetricsAddress):
                site: web.BaseSite = web.TCPSite(
                    self._runner,
                    self._address.host,
                    self._address.port,
                )
                await site.start()

                # Resolve the actual bound port when the OS assigned one.
                if self._address.port == 0:
                    self._port = self._runner.addresses[0][1]

                logger.info(
                    "Metrics server listening on %s:%d.",
                    self._address.host,
                    self.port,
                )
            else:
                socket_path = Path(self._address.path)
                # This runs once before binding the Unix site, so synchronous
                # path checks are simpler than offloading startup setup.
                try:
                    mode = socket_path.stat(  # noqa: ASYNC240
                        follow_symlinks=False,
                    ).st_mode
                except FileNotFoundError:
                    pass
                else:
                    if not stat.S_ISSOCK(mode):
                        msg = (
                            "Metrics Unix socket path already exists and is not a "
                            f"socket: {self._address.path}"
                        )
                        raise FileExistsError(msg)

                    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as probe:
                        probe.settimeout(0.1)
                        try:
                            probe.connect(self._address.path)
                        except OSError as exc:
                            if exc.errno not in {errno.ECONNREFUSED, errno.ENOENT}:
                                raise
                        else:
                            msg = (
                                "Metrics Unix socket path is already in use: "
                                f"{self._address.path}"
                            )
                            raise OSError(errno.EADDRINUSE, msg)

                    with contextlib.suppress(FileNotFoundError):
                        socket_path.unlink()  # noqa: ASYNC240

                site = web.UnixSite(self._runner, self._address.path)
                await site.start()
                if self._address.mode is not None:
                    socket_path.chmod(self._address.mode)  # noqa: ASYNC240
                logger.info(
                    "Metrics server listening on Unix socket %s.",
                    self._address.path,
                )
        except Exception:
            await self._runner.cleanup()
            self._runner = None
            raise

    async def stop(self) -> None:
        """Shut down the HTTP server and release resources."""
        if self._runner is not None:
            await self._runner.cleanup()
            self._runner = None
            logger.info("Metrics server stopped.")
