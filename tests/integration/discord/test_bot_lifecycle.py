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

"""Simcord integration tests for Crabstero bot lifecycle behavior."""

from typing import TYPE_CHECKING, Any, cast

import discord
import pytest
from discord import app_commands
from discord.ext import commands
from simcord import run

from crabstero import metrics
from crabstero.bot import Crabstero, TrackedModal, TrackedView

if TYPE_CHECKING:
    from pathlib import Path

    from simcord import Env

    from tests.integration.discord.conftest import (
        MakeSimcordMemberChannel,
        SimcordMemberChannel,
    )


def _counter_value(counter: Any, **labels: str) -> float:
    """Return the current value for a labelled Prometheus counter."""
    return float(counter.labels(**labels)._value.get())


class _LifecycleResource:
    """Resource double that records lifecycle cleanup calls."""

    def __init__(
        self,
        name: str,
        events: list[str],
        *,
        fail_once: bool = False,
    ) -> None:
        """Store the event name and shared event log."""
        self._name = name
        self._events = events
        self._fail_once = fail_once

    async def _record(self) -> None:
        """Record a cleanup call and optionally fail once."""
        self._events.append(self._name)
        if self._fail_once:
            self._fail_once = False
            raise RuntimeError(f"{self._name} cleanup failed")

    async def close(self) -> None:
        """Record a close call."""
        await self._record()

    async def stop(self) -> None:
        """Record a stop call."""
        await self._record()


class TestSetupHook:
    """Bot startup wires Discord cogs and slash commands under Simcord."""

    def test_db_before_setup_raises(self, tmp_path: Path) -> None:
        """The database property is unavailable before setup_hook runs."""
        bot = Crabstero(str(tmp_path / "not-started.db"))

        with pytest.raises(RuntimeError, match="Database is not initialized"):
            _ = bot.db

    async def test_loads_cogs_and_syncs_commands(
        self,
        simcord_env: Env,
        crabstero_bot: Crabstero,
    ) -> None:
        """setup_hook loads expected cogs and syncs app commands."""
        assert set(crabstero_bot.cogs) == {
            "InteractionCog",
            "MessageCog",
            "ServerEventsCog",
        }

        commands = simcord_env.backend.commands[None]
        assert {name for name, _ in commands} == {"forgetme", "pingme"}

        application_id = simcord_env.backend.application_id
        http_routes = [
            f"{request.method} {request.path}" for request in simcord_env.http_requests
        ]
        assert f"GET /applications/{application_id}/commands" in http_routes
        assert f"PUT /applications/{application_id}/commands" in http_routes

    async def test_metrics_server_lifecycle_starts_and_stops(
        self,
        tmp_path: Path,
    ) -> None:
        """A configured metrics server is started and stopped with the bot."""
        bot = Crabstero(
            str(tmp_path / "metrics.db"),
            metrics_address=metrics.TcpMetricsAddress("127.0.0.1", 0),
        )
        try:
            async with run(bot):
                assert bot._metrics_server is not None
                assert bot._metrics_server.port > 0
        finally:
            bot.ws = None  # type: ignore[assignment]
            await bot.close()


class TestClose:
    """Bot shutdown ordering."""

    async def test_closes_discord_before_local_resources(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Discord is closed before local resources are released."""
        events: list[str] = []
        bot = Crabstero(str(tmp_path / "close-order.db"))
        original_close = commands.Bot.close

        async def close_discord(self: commands.Bot) -> None:
            events.append("discord")
            await original_close(self)

        monkeypatch.setattr(commands.Bot, "close", close_discord)
        cast("Any", bot).ingest_cache = _LifecycleResource("cache", events)
        cast("Any", bot)._metrics_server = _LifecycleResource("metrics", events)
        cast("Any", bot)._db = _LifecycleResource("db", events)

        await bot.close()

        assert events == ["discord", "cache", "metrics", "db"]

    async def test_local_resources_close_when_discord_close_fails(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Local resources are released even if Discord close raises."""
        events: list[str] = []
        bot = Crabstero(str(tmp_path / "close-failure.db"))

        async def close_discord(_self: commands.Bot) -> None:
            events.append("discord")
            raise RuntimeError("discord close failed")

        monkeypatch.setattr(commands.Bot, "close", close_discord)
        cast("Any", bot).ingest_cache = _LifecycleResource("cache", events)
        cast("Any", bot)._metrics_server = _LifecycleResource("metrics", events)
        cast("Any", bot)._db = _LifecycleResource("db", events)

        with pytest.raises(RuntimeError, match="discord close failed"):
            await bot.close()

        assert events == ["discord", "cache", "metrics", "db"]

    async def test_local_resource_cleanup_is_retried_after_local_failure(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Local cleanup can be retried after Discord has already closed."""
        events: list[str] = []
        bot = Crabstero(str(tmp_path / "close-retry.db"))
        original_close = commands.Bot.close

        async def close_discord(self: commands.Bot) -> None:
            events.append("discord")
            await original_close(self)

        monkeypatch.setattr(commands.Bot, "close", close_discord)
        cast("Any", bot).ingest_cache = _LifecycleResource(
            "cache",
            events,
            fail_once=True,
        )
        cast("Any", bot)._metrics_server = _LifecycleResource("metrics", events)
        cast("Any", bot)._db = _LifecycleResource("db", events)

        with pytest.raises(RuntimeError, match="cache cleanup failed"):
            await bot.close()

        assert events == ["discord", "cache", "metrics", "db"]

        await bot.close()

        assert events == ["discord", "cache", "metrics", "db", "cache"]


class TestMetrics:
    """Bot-level metrics are incremented by lifecycle error paths."""

    def test_dispatch_increments_discord_event_metric(
        self,
        crabstero_bot: Crabstero,
    ) -> None:
        """Dispatch increments the labelled Discord event counter."""
        event = "codex_lifecycle_metric"
        before = _counter_value(metrics.DISCORD_EVENTS, event=event)

        crabstero_bot.dispatch(event)

        assert _counter_value(metrics.DISCORD_EVENTS, event=event) == before + 1

    async def test_on_error_increments_event_error_metric(
        self,
        crabstero_bot: Crabstero,
    ) -> None:
        """Unhandled event listener failures are counted by event name."""
        source = "on_message"
        before = _counter_value(metrics.ERRORS, source=source)

        await crabstero_bot.on_error(source)

        assert _counter_value(metrics.ERRORS, source=source) == before + 1

    async def test_tracked_view_and_modal_errors_increment_metrics(self) -> None:
        """Tracked UI error handlers increment their error counters."""
        view_before = _counter_value(metrics.ERRORS, source="view")
        modal_before = _counter_value(metrics.ERRORS, source="modal")
        interaction = cast("discord.Interaction", object())
        button = cast("discord.ui.Item[TrackedView]", discord.ui.Button(label="Run"))

        await TrackedView().on_error(interaction, RuntimeError("view failed"), button)
        await TrackedModal(title="Tracked").on_error(
            interaction,
            RuntimeError("modal failed"),
        )

        assert _counter_value(metrics.ERRORS, source="view") == view_before + 1
        assert _counter_value(metrics.ERRORS, source="modal") == modal_before + 1


class TestCommandErrors:
    """Unhandled app command errors produce an ephemeral fallback response."""

    async def test_app_command_error_sends_ephemeral_fallback(
        self,
        simcord_member_channel: SimcordMemberChannel,
        crabstero_bot: Crabstero,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A failing slash command is captured and answered by tree.on_error."""
        member = simcord_member_channel.member
        channel = simcord_member_channel.channel

        async def raise_on_set_flag(
            _entity_type: str,
            _entity_id: str,
            _flag_name: str,
        ) -> None:
            raise RuntimeError("database unavailable")

        monkeypatch.setattr(crabstero_bot.db, "set_flag", raise_on_set_flag)
        errors_before = _counter_value(metrics.ERRORS, source="command")

        result = await member.slash(channel, "pingme")

        assert result.response is not None
        assert result.response.ephemeral is True
        assert result.response.content == (
            "I encountered an error while processing this command."
            " Please try again later."
        )
        assert _counter_value(metrics.ERRORS, source="command") == errors_before + 1

    async def test_app_command_error_mentions_developer_when_metrics_are_enabled(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        make_simcord_member_channel: MakeSimcordMemberChannel,
    ) -> None:
        """Configured metrics switch command errors to the notified-developer copy."""
        bot = Crabstero(
            str(tmp_path / "metrics-command-errors.db"),
            metrics_address=metrics.TcpMetricsAddress("127.0.0.1", 0),
        )
        try:
            async with run(bot) as env:
                context = await make_simcord_member_channel(env)
                member = context.member
                channel = context.channel

                async def raise_on_set_flag(
                    _entity_type: str,
                    _entity_id: str,
                    _flag_name: str,
                ) -> None:
                    raise RuntimeError("database unavailable")

                monkeypatch.setattr(bot.db, "set_flag", raise_on_set_flag)
                errors_before = _counter_value(metrics.ERRORS, source="command")

                result = await member.slash(channel, "pingme")

                assert result.response is not None
                assert result.response.ephemeral is True
                assert result.response.content == (
                    "I encountered an error while processing this command."
                    " The developer has been notified,"
                    " please try again later."
                )
                assert (
                    _counter_value(metrics.ERRORS, source="command")
                    == errors_before + 1
                )
        finally:
            bot.ws = None  # type: ignore[assignment]
            await bot.close()

    async def test_app_command_error_after_defer_sends_ephemeral_followup(
        self,
        tmp_path: Path,
        make_simcord_member_channel: MakeSimcordMemberChannel,
    ) -> None:
        """An error after deferring completes the original ephemeral response."""
        bot = Crabstero(str(tmp_path / "deferred-command-errors.db"))

        @app_commands.command(
            name="deferboom",
            description="Fail after acknowledging the interaction.",
        )
        async def deferboom(interaction: discord.Interaction) -> None:
            await interaction.response.defer(ephemeral=True)
            raise RuntimeError("deferred command failed")

        bot.tree.add_command(deferboom)
        try:
            async with run(bot) as env:
                context = await make_simcord_member_channel(env)
                errors_before = _counter_value(metrics.ERRORS, source="command")

                result = await context.member.slash(context.channel, "deferboom")

                callback_types = [
                    request.json["type"]
                    for request in env.http_requests
                    if request.path.endswith("/callback") and request.json is not None
                ]
                assert callback_types == [
                    discord.InteractionResponseType.deferred_channel_message.value
                ]
                assert result.acknowledged is True
                assert result.deferred is False
                assert result.followups == []
                assert result.response is not None
                assert result.response.ephemeral is True
                assert result.response.content == (
                    "I encountered an error while processing this command."
                    " Please try again later."
                )
                assert (
                    _counter_value(metrics.ERRORS, source="command")
                    == errors_before + 1
                )
        finally:
            bot.ws = None  # type: ignore[assignment]
            await bot.close()
