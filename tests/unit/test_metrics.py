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

Tests cover metric object registration.
"""

import pytest
from prometheus_client import generate_latest

# Importing the module registers its Prometheus metrics with the default registry.
import crabstero.metrics  # noqa: F401 - imported for registration side effects


@pytest.fixture(scope="module")
def prometheus_output() -> str:
    """Return Prometheus exposition text after module-level metric registration."""
    return generate_latest().decode()


class TestMetricObjects:
    """All expected metric objects exist and are registered."""

    @pytest.mark.parametrize(
        "name",
        [
            pytest.param("crabstero_build_info", id="build-info"),
            pytest.param("crabstero_replies_sent_total", id="replies-sent"),
            pytest.param("crabstero_embeds_generated_total", id="embeds-generated"),
            pytest.param("crabstero_messages_processed_total", id="messages-processed"),
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
                "crabstero_generation_duration_seconds",
                id="generation-duration",
            ),
            pytest.param(
                "crabstero_channel_ingestion_messages",
                id="channel-ingestion-messages",
            ),
            pytest.param("crabstero_discord_events_total", id="discord-events"),
            pytest.param(
                "crabstero_messages_uningested_total",
                id="messages-uningested",
            ),
        ],
    )
    def test_metric_in_output(self, prometheus_output: str, name: str) -> None:
        """Each declared metric appears in the Prometheus text output."""
        assert name in prometheus_output, f"{name} not found in Prometheus output"
