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

"""Unit tests for the Prometheus metrics module.

Tests cover metric object registration and the MetricsServer HTTP endpoint.
"""

from typing import TYPE_CHECKING

import aiohttp
import pytest
from prometheus_client import generate_latest

from crabstero.metrics import MetricsServer

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator


class TestMetricObjects:
    """All expected metric objects exist and are registered."""

    @pytest.mark.parametrize(
        "name",
        [
            pytest.param("crabstero_build_info", id="build-info"),
            pytest.param("crabstero_messages_ingested_total", id="messages-ingested"),
            pytest.param("crabstero_replies_sent_total", id="replies-sent"),
            pytest.param("crabstero_embeds_generated_total", id="embeds-generated"),
            pytest.param("crabstero_messages_processed_total", id="messages-processed"),
            pytest.param("crabstero_channels_ingested_total", id="channels-ingested"),
            pytest.param("crabstero_pingme_total", id="pingme"),
            pytest.param("crabstero_forgetme_total", id="forgetme"),
            pytest.param("crabstero_errors_total", id="errors"),
            pytest.param("crabstero_discord_latency_seconds", id="discord-latency"),
            pytest.param("crabstero_guild_count", id="guild-count"),
            pytest.param("crabstero_ingestion_active_channels", id="ingestion-active"),
            pytest.param(
                "crabstero_message_ingestion_duration_seconds",
                id="message-ingestion-duration",
            ),
            pytest.param(
                "crabstero_channel_ingestion_duration_seconds",
                id="channel-ingestion-duration",
            ),
            pytest.param(
                "crabstero_generation_duration_seconds", id="generation-duration"
            ),
            pytest.param(
                "crabstero_generated_message_length_characters",
                id="generated-message-length",
            ),
            pytest.param(
                "crabstero_channel_ingestion_messages", id="channel-ingestion-messages"
            ),
            pytest.param("crabstero_discord_events_total", id="discord-events"),
            pytest.param(
                "crabstero_messages_uningested_total", id="messages-uningested"
            ),
        ],
    )
    def test_metric_in_output(self, name: str) -> None:
        """Each declared metric appears in the Prometheus text output."""
        output = generate_latest().decode()
        assert name in output, f"{name} not found in Prometheus output"


@pytest.fixture
async def metrics_server() -> AsyncGenerator[MetricsServer]:
    """Start a MetricsServer on an OS-assigned port and stop it after the test."""
    server = MetricsServer("127.0.0.1", 0)
    await server.start()
    yield server
    await server.stop()


class TestMetricsServer:
    """HTTP server serves Prometheus metrics on /metrics."""

    async def test_serves_metrics_endpoint(self, metrics_server: MetricsServer) -> None:
        """GET /metrics returns 200 with metric output containing our metrics."""
        async with (
            aiohttp.ClientSession() as session,
            session.get(f"http://127.0.0.1:{metrics_server.port}/metrics") as resp,
        ):
            assert resp.status == 200
            body = await resp.text()
            assert "crabstero_build_info" in body

    async def test_non_metrics_path_returns_404(
        self, metrics_server: MetricsServer
    ) -> None:
        """GET on an unknown path returns 404."""
        async with (
            aiohttp.ClientSession() as session,
            session.get(f"http://127.0.0.1:{metrics_server.port}/notfound") as resp,
        ):
            assert resp.status == 404
