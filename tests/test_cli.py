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

"""Unit tests for CLI argument parsing, credentials, and systemd integration.

Tests cover the CLI helpers and runtime orchestration from crabstero.__main__.
"""

import asyncio
import signal
import socket
import sys
from typing import TYPE_CHECKING, Any, ClassVar, Self

import pytest

from crabstero import __main__ as cli
from crabstero.__main__ import (
    _parse_args,
    _read_credential,
    _sd_notify,
    _watchdog_interval,
    _watchdog_loop,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Coroutine
    from pathlib import Path
    from types import TracebackType


class _FakeCrabstero:
    events: ClassVar[list[object]] = []
    login_error: ClassVar[BaseException | None] = None
    close_error: ClassVar[BaseException | None] = None
    connect_callback: ClassVar[Callable[[], None] | None] = None

    def __init__(self, **kwargs: object) -> None:
        self._closed = False
        self.events.append(("init", kwargs))

    async def __aenter__(self) -> Self:
        self.events.append("enter")
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.events.append("exit")
        await self.close()

    async def login(self, token: str) -> None:
        self.events.append(("login", token))
        if self.login_error is not None:
            raise self.login_error

    async def connect(self, **kwargs: object) -> None:
        self.events.append(("connect", kwargs))
        callback = type(self).connect_callback
        if callback is not None:
            callback()
            await asyncio.sleep(0)

    async def close(self) -> None:
        if self._closed:
            return
        self.events.append("close")
        self._closed = True
        if self.close_error is not None:
            raise self.close_error

    def is_closed(self) -> bool:
        return self._closed


def _run_coroutine(coro: Coroutine[Any, Any, None]) -> None:
    asyncio.run(coro)


def _prepare_main_test(
    monkeypatch: pytest.MonkeyPatch,
    *,
    login_error: BaseException | None = None,
    close_error: BaseException | None = None,
) -> list[object]:
    _FakeCrabstero.events = []
    _FakeCrabstero.login_error = login_error
    _FakeCrabstero.close_error = close_error
    _FakeCrabstero.connect_callback = None

    def sd_notify(state: str) -> None:
        _FakeCrabstero.events.append(("notify", state))

    monkeypatch.setattr(sys, "argv", ["crabstero", "--token", "cli-token"])
    monkeypatch.setattr(cli, "Crabstero", _FakeCrabstero)
    monkeypatch.setattr("crabstero.__main__.uvloop.run", _run_coroutine)
    monkeypatch.setattr(cli, "_sd_notify", sd_notify)
    monkeypatch.setattr(cli, "_watchdog_interval", lambda: None)

    return _FakeCrabstero.events


class TestParseArgs:
    """Argument parsing with defaults and overrides."""

    def test_token_from_arg(self) -> None:
        """--token flag sets the token."""
        args = _parse_args(["--token", "abc"])
        assert args.token == "abc"  # noqa: S105

    def test_database_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Omitting --database-path defaults to 'crabstero.db'."""
        monkeypatch.delenv("DATABASE_PATH", raising=False)
        args = _parse_args(["--token", "test"])
        assert args.database_path == "crabstero.db"

    def test_database_override(self) -> None:
        """--database-path overrides the default."""
        args = _parse_args(["--token", "test", "--database-path", "/custom.db"])
        assert args.database_path == "/custom.db"

    def test_ingest_only_flag(self) -> None:
        """--ingest-only sets ingest_only to True."""
        args = _parse_args(["--token", "test", "--ingest-only"])
        assert args.ingest_only is True

    def test_listen_metrics_parses_address(self) -> None:
        """--listen-metrics HOST:PORT sets listen_metrics to (host, port)."""
        args = _parse_args(["--token", "test", "--listen-metrics", "127.0.0.1:9090"])
        assert args.listen_metrics == ("127.0.0.1", 9090)

    def test_listen_metrics_default_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Omitting --listen-metrics defaults to None."""
        monkeypatch.delenv("LISTEN_METRICS", raising=False)
        args = _parse_args(["--token", "test"])
        assert args.listen_metrics is None

    def test_listen_metrics_ipv6(self) -> None:
        """--listen-metrics [::1]:PORT parses IPv6 address correctly."""
        args = _parse_args(["--token", "test", "--listen-metrics", "[::1]:9090"])
        assert args.listen_metrics == ("[::1]", 9090)

    def test_listen_metrics_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """LISTEN_METRICS env var is used when --listen-metrics is omitted."""
        monkeypatch.setenv("LISTEN_METRICS", "127.0.0.1:8080")
        args = _parse_args(["--token", "test"])
        assert args.listen_metrics == ("127.0.0.1", 8080)

    def test_listen_metrics_invalid_format(self) -> None:
        """--listen-metrics with no colon raises SystemExit."""
        with pytest.raises(SystemExit):
            _parse_args(["--token", "test", "--listen-metrics", "bad"])

    def test_listen_metrics_invalid_port(self) -> None:
        """--listen-metrics with non-integer port raises SystemExit."""
        with pytest.raises(SystemExit):
            _parse_args(["--token", "test", "--listen-metrics", "127.0.0.1:abc"])

    def test_missing_token_exits(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Missing token causes SystemExit."""
        monkeypatch.delenv("TOKEN", raising=False)
        monkeypatch.delenv("CREDENTIALS_DIRECTORY", raising=False)
        with pytest.raises(SystemExit):
            _parse_args([])


class TestReadCredential:
    """Systemd credential file reading."""

    def test_reads_credential_file(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Reads and strips the credential value from the file."""
        (tmp_path / "mytoken").write_text("  secret123  \n")
        monkeypatch.setenv("CREDENTIALS_DIRECTORY", str(tmp_path))
        assert _read_credential("mytoken") == "secret123"

    def test_returns_none_without_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Returns None when CREDENTIALS_DIRECTORY is not set."""
        monkeypatch.delenv("CREDENTIALS_DIRECTORY", raising=False)
        assert _read_credential("anything") is None

    def test_returns_none_for_missing_file(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Returns None when the credential file does not exist."""
        monkeypatch.setenv("CREDENTIALS_DIRECTORY", str(tmp_path))
        assert _read_credential("nonexistent") is None


class TestSystemdNotify:
    """Systemd notification helper behavior."""

    def test_sd_notify_noop_without_socket(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Missing NOTIFY_SOCKET is a no-op."""
        monkeypatch.delenv("NOTIFY_SOCKET", raising=False)
        _sd_notify("READY=1")

    def test_sd_notify_sends_datagram(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """_sd_notify sends the payload to NOTIFY_SOCKET."""
        socket_path = tmp_path / "notify.sock"

        with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as server:
            server.bind(str(socket_path))
            server.settimeout(1)
            monkeypatch.setenv("NOTIFY_SOCKET", str(socket_path))

            _sd_notify("READY=1")

            assert server.recv(1024) == b"READY=1"


class TestWatchdog:
    """Systemd watchdog helper behavior."""

    def test_watchdog_interval_missing(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Missing WATCHDOG_USEC disables watchdog pings."""
        monkeypatch.delenv("WATCHDOG_USEC", raising=False)
        assert _watchdog_interval() is None

    def test_watchdog_interval_valid(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """WATCHDOG_USEC is converted to half the interval in seconds."""
        monkeypatch.setenv("WATCHDOG_USEC", "4000000")
        assert _watchdog_interval() == 2

    @pytest.mark.parametrize(
        "value",
        [
            pytest.param("invalid", id="not-integer"),
            pytest.param("0", id="zero"),
            pytest.param("-1", id="negative"),
        ],
    )
    def test_watchdog_interval_invalid(
        self,
        value: str,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Invalid WATCHDOG_USEC values disable watchdog pings."""
        monkeypatch.setenv("WATCHDOG_USEC", value)
        assert _watchdog_interval() is None

    async def test_watchdog_loop_sends_until_stopped(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """_watchdog_loop sends WATCHDOG=1 and exits when stopped."""
        notifications: list[str] = []
        stop = asyncio.Event()
        monkeypatch.setattr(cli, "_sd_notify", notifications.append)

        task = asyncio.create_task(_watchdog_loop(60, stop))
        await asyncio.sleep(0)
        stop.set()
        await asyncio.wait_for(task, timeout=1)

        assert notifications == ["WATCHDOG=1"]


class TestMainLifecycle:
    """CLI runtime orchestration."""

    def test_ready_sent_after_login_before_connect(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """READY=1 is sent after login succeeds and before connect starts."""
        events = _prepare_main_test(monkeypatch)

        cli.main()

        assert events.index(("login", "cli-token")) < events.index(
            ("notify", "READY=1"),
        )
        assert events.index(("notify", "READY=1")) < events.index(
            ("connect", {"reconnect": True}),
        )

    def test_stopping_sent_after_clean_connect_return(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """STOPPING=1 is sent when the CLI run exits cleanly after readiness."""
        events = _prepare_main_test(monkeypatch)

        cli.main()

        assert events.index(("connect", {"reconnect": True})) < events.index(
            ("notify", "STOPPING=1"),
        )

    def test_ready_not_sent_when_login_fails(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A login failure exits without reporting readiness."""
        events = _prepare_main_test(
            monkeypatch,
            login_error=RuntimeError("login failed"),
        )

        with pytest.raises(RuntimeError, match="login failed"):
            cli.main()

        assert ("notify", "READY=1") not in events
        assert ("notify", "STOPPING=1") not in events

    def test_shutdown_task_exception_is_propagated(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A completed shutdown task exception is not silently dropped."""
        signal_handlers: dict[int, Callable[[], None]] = {}

        class SignalLoop:
            def add_signal_handler(
                self,
                sig: int,
                callback: Callable[[], None],
            ) -> None:
                signal_handlers[sig] = callback

            def remove_signal_handler(self, sig: int) -> None:
                signal_handlers.pop(sig, None)

        events = _prepare_main_test(
            monkeypatch,
            close_error=RuntimeError("close failed"),
        )

        def request_shutdown() -> None:
            signal_handlers[signal.SIGTERM]()

        monkeypatch.setattr(asyncio, "get_running_loop", SignalLoop)
        _FakeCrabstero.connect_callback = request_shutdown

        with pytest.raises(RuntimeError, match="close failed"):
            cli.main()

        assert events.index(("notify", "STOPPING=1")) < events.index("close")
