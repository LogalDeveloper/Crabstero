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

Tests cover the CLI helpers and runtime orchestration from crabstero.cli.
"""

import asyncio
import os
import signal
import sys
from typing import TYPE_CHECKING, Any, ClassVar, Self

import pytest

from crabstero import cli
from crabstero.cli import (
    _parse_args,
    _read_credential,
    _sd_notify,
    _watchdog_interval,
    _watchdog_loop,
)
from crabstero.metrics import TcpMetricsAddress, UnixMetricsAddress

if TYPE_CHECKING:
    from collections.abc import Callable, Coroutine
    from pathlib import Path
    from types import TracebackType


class _FakeCrabstero:
    """Fake bot that records CLI lifecycle calls without touching Discord.

    Tests can inject callbacks during login/connect and exceptions during
    login/close to exercise CLI shutdown ordering and propagation paths.
    """

    events: ClassVar[list[object]] = []
    login_error: ClassVar[BaseException | None] = None
    login_callback: ClassVar[Callable[[], None] | None] = None
    close_error: ClassVar[BaseException | None] = None
    connect_callback: ClassVar[Callable[[], None] | None] = None

    @classmethod
    def reset(
        cls,
        *,
        login_error: BaseException | None = None,
        close_error: BaseException | None = None,
    ) -> list[object]:
        """Clear scenario state and return the shared lifecycle event log.

        The event log is shared across fake instances so tests can assert the
        ordering of construction, login, notifications, connect, and close.
        """
        cls.events = []
        cls.login_error = login_error
        cls.login_callback = None
        cls.close_error = close_error
        cls.connect_callback = None
        return cls.events

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
        callback = type(self).login_callback
        if callback is not None:
            callback()
            await asyncio.sleep(0)
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


@pytest.fixture
def prepared_main(
    monkeypatch: pytest.MonkeyPatch,
) -> Callable[..., list[object]]:
    """Return a factory that prepares cli.main for fake-bot lifecycle tests.

    Each factory call resets the fake bot, routes systemd notifications into
    the event log, disables watchdog setup, and makes uvloop.run execute the
    coroutine synchronously through asyncio.run.
    """

    def prepare(
        *,
        login_error: BaseException | None = None,
        close_error: BaseException | None = None,
    ) -> list[object]:
        events = _FakeCrabstero.reset(
            login_error=login_error,
            close_error=close_error,
        )

        def sd_notify(state: str) -> None:
            events.append(("notify", state))

        monkeypatch.setattr(sys, "argv", ["crabstero", "--token", "cli-token"])
        monkeypatch.setattr(cli, "Crabstero", _FakeCrabstero)
        monkeypatch.setattr("crabstero.cli.uvloop.run", asyncio.run)
        monkeypatch.setattr(cli, "_sd_notify", sd_notify)
        monkeypatch.setattr(cli, "_watchdog_interval", lambda: None)

        return events

    return prepare


@pytest.fixture
def signal_handlers(
    monkeypatch: pytest.MonkeyPatch,
) -> dict[int, Callable[[], None]]:
    """Patch signal registration and return captured signal callbacks.

    The returned mapping lets tests invoke registered SIGINT/SIGTERM handlers
    directly and assert that cleanup removes them.
    """
    handlers: dict[int, Callable[[], None]] = {}

    class SignalLoop:
        """Minimal event-loop facade that stores installed signal handlers."""

        def add_signal_handler(
            self,
            sig: int,
            callback: Callable[..., None],
            *args: object,
        ) -> None:
            handlers[sig] = lambda: callback(*args)

        def remove_signal_handler(self, sig: int) -> None:
            handlers.pop(sig, None)

    monkeypatch.setattr(asyncio, "get_running_loop", SignalLoop)
    return handlers


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
        assert args.metrics_address == TcpMetricsAddress("127.0.0.1", 9090)

    def test_listen_metrics_default_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Omitting --listen-metrics defaults to None."""
        monkeypatch.delenv("LISTEN_METRICS", raising=False)
        args = _parse_args(["--token", "test"])
        assert args.metrics_address is None

    def test_listen_metrics_ipv6(self) -> None:
        """--listen-metrics [::1]:PORT parses IPv6 address correctly."""
        args = _parse_args(["--token", "test", "--listen-metrics", "[::1]:9090"])
        assert args.metrics_address == TcpMetricsAddress("[::1]", 9090)

    def test_listen_metrics_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """LISTEN_METRICS env var is used when --listen-metrics is omitted."""
        monkeypatch.setenv("LISTEN_METRICS", "127.0.0.1:8080")
        args = _parse_args(["--token", "test"])
        assert args.metrics_address == TcpMetricsAddress("127.0.0.1", 8080)

    def test_listen_metrics_unix_socket(self) -> None:
        """--listen-metrics unix:/path sets listen_metrics to a Unix socket."""
        args = _parse_args(
            ["--token", "test", "--listen-metrics", "unix:/run/crabstero.sock"],
        )
        assert args.metrics_address == UnixMetricsAddress("/run/crabstero.sock")

    def test_listen_metrics_unix_socket_mode(self) -> None:
        """--metrics-unix-socket-mode sets the Unix socket file mode."""
        args = _parse_args(
            [
                "--token",
                "test",
                "--listen-metrics",
                "unix:/run/crabstero.sock",
                "--metrics-unix-socket-mode",
                "0666",
            ],
        )
        assert args.metrics_address == UnixMetricsAddress(
            "/run/crabstero.sock",
            mode=0o666,
        )

    def test_listen_metrics_unix_socket_from_env(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """LISTEN_METRICS can enable metrics on a Unix socket."""
        monkeypatch.setenv("LISTEN_METRICS", "unix:/run/crabstero.sock")
        args = _parse_args(["--token", "test"])
        assert args.metrics_address == UnixMetricsAddress("/run/crabstero.sock")

    def test_listen_metrics_unix_socket_mode_from_env(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """METRICS_UNIX_SOCKET_MODE sets the Unix socket file mode."""
        monkeypatch.setenv("LISTEN_METRICS", "unix:/run/crabstero.sock")
        monkeypatch.setenv("METRICS_UNIX_SOCKET_MODE", "666")
        args = _parse_args(["--token", "test"])
        assert args.metrics_address == UnixMetricsAddress(
            "/run/crabstero.sock",
            mode=0o666,
        )

    def test_listen_metrics_invalid_format(self) -> None:
        """--listen-metrics with no colon raises SystemExit."""
        with pytest.raises(SystemExit):
            _parse_args(["--token", "test", "--listen-metrics", "bad"])

    def test_listen_metrics_invalid_port(self) -> None:
        """--listen-metrics with non-integer port raises SystemExit."""
        with pytest.raises(SystemExit):
            _parse_args(["--token", "test", "--listen-metrics", "127.0.0.1:abc"])

    def test_listen_metrics_empty_unix_socket_path(self) -> None:
        """--listen-metrics unix: with no path raises SystemExit."""
        with pytest.raises(SystemExit):
            _parse_args(["--token", "test", "--listen-metrics", "unix:"])

    @pytest.mark.parametrize(
        "mode",
        [
            pytest.param("bad", id="not-octal"),
            pytest.param("0888", id="invalid-octal-digit"),
            pytest.param("1000", id="too-large"),
            pytest.param("-1", id="negative"),
        ],
    )
    def test_metrics_unix_socket_mode_invalid(self, mode: str) -> None:
        """Invalid Unix socket modes fail argument parsing."""
        with pytest.raises(SystemExit):
            _parse_args(
                [
                    "--token",
                    "test",
                    "--listen-metrics",
                    "unix:/run/crabstero.sock",
                    "--metrics-unix-socket-mode",
                    mode,
                ],
            )

    def test_metrics_unix_socket_mode_requires_unix_socket(self) -> None:
        """Unix socket mode cannot be used with a TCP metrics listener."""
        with pytest.raises(SystemExit):
            _parse_args(
                [
                    "--token",
                    "test",
                    "--listen-metrics",
                    "127.0.0.1:9090",
                    "--metrics-unix-socket-mode",
                    "0666",
                ],
            )

    def test_metrics_unix_socket_mode_requires_metrics_listener(self) -> None:
        """Unix socket mode cannot be used without a metrics listener."""
        with pytest.raises(SystemExit):
            _parse_args(
                ["--token", "test", "--metrics-unix-socket-mode", "0666"],
            )

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
        monkeypatch.delenv("WATCHDOG_PID", raising=False)
        assert _watchdog_interval() == 2

    def test_watchdog_interval_valid_for_matching_pid(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """WATCHDOG_PID enables watchdog pings for the current process."""
        monkeypatch.setenv("WATCHDOG_USEC", "4000000")
        monkeypatch.setenv("WATCHDOG_PID", str(os.getpid()))
        assert _watchdog_interval() == 2

    def test_watchdog_interval_ignores_different_pid(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """WATCHDOG_PID disables pings for non-matching processes."""
        monkeypatch.setenv("WATCHDOG_USEC", "4000000")
        monkeypatch.setenv("WATCHDOG_PID", str(os.getpid() + 1))
        assert _watchdog_interval() is None

    def test_watchdog_interval_ignores_invalid_pid(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Invalid WATCHDOG_PID disables watchdog pings."""
        monkeypatch.setenv("WATCHDOG_USEC", "4000000")
        monkeypatch.setenv("WATCHDOG_PID", "invalid")
        assert _watchdog_interval() is None

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

    def test_constructs_bot_without_token(
        self,
        prepared_main: Callable[..., list[object]],
    ) -> None:
        """The CLI keeps the Discord token out of Crabstero construction."""
        events = prepared_main()

        assert cli.main() == 0

        init_event = events[0]
        assert isinstance(init_event, tuple)
        assert init_event == (
            "init",
            {
                "database_path": "crabstero.db",
                "ingest_only": False,
                "metrics_address": None,
            },
        )
        init_kwargs = init_event[1]
        assert isinstance(init_kwargs, dict)
        assert "token" not in init_kwargs

    def test_main_accepts_explicit_argv(
        self,
        prepared_main: Callable[..., list[object]],
    ) -> None:
        """main(argv) runs from explicit arguments instead of sys.argv."""
        events = prepared_main()

        assert cli.main(["--token", "argv-token", "--database-path", "/custom.db"]) == 0

        assert events[0] == (
            "init",
            {
                "database_path": "/custom.db",
                "ingest_only": False,
                "metrics_address": None,
            },
        )
        assert ("login", "argv-token") in events

    def test_ready_sent_after_login_before_connect(
        self,
        prepared_main: Callable[..., list[object]],
    ) -> None:
        """READY=1 is sent after login succeeds and before connect starts."""
        events = prepared_main()

        assert cli.main() == 0

        assert events.index(("login", "cli-token")) < events.index(
            ("notify", "READY=1"),
        )
        assert events.index(("notify", "READY=1")) < events.index(
            ("connect", {"reconnect": True}),
        )

    def test_stopping_sent_after_clean_connect_return(
        self,
        prepared_main: Callable[..., list[object]],
    ) -> None:
        """STOPPING=1 is sent when the CLI run exits cleanly after readiness."""
        events = prepared_main()

        assert cli.main() == 0

        assert events.index(("connect", {"reconnect": True})) < events.index(
            ("notify", "STOPPING=1"),
        )

    def test_ready_not_sent_when_login_fails(
        self,
        prepared_main: Callable[..., list[object]],
    ) -> None:
        """A login failure exits without reporting readiness."""
        events = prepared_main(login_error=RuntimeError("login failed"))

        with pytest.raises(RuntimeError, match="login failed"):
            cli.main()

        assert ("notify", "READY=1") not in events
        assert ("notify", "STOPPING=1") not in events

    def test_shutdown_task_exception_is_propagated(
        self,
        prepared_main: Callable[..., list[object]],
        signal_handlers: dict[int, Callable[[], None]],
    ) -> None:
        """A completed shutdown task exception is not silently dropped."""
        events = prepared_main(close_error=RuntimeError("close failed"))

        def request_shutdown() -> None:
            signal_handlers[signal.SIGTERM]()

        _FakeCrabstero.connect_callback = request_shutdown

        with pytest.raises(RuntimeError, match="close failed"):
            cli.main()

        assert events.index(("notify", "STOPPING=1")) < events.index("close")

    @pytest.mark.parametrize(
        ("sig", "exit_code"),
        [
            pytest.param(signal.SIGINT, 130, id="sigint"),
            pytest.param(signal.SIGTERM, 0, id="sigterm"),
        ],
    )
    def test_registered_signal_handler_sets_exit_code(
        self,
        sig: signal.Signals,
        exit_code: int,
        prepared_main: Callable[..., list[object]],
        signal_handlers: dict[int, Callable[[], None]],
    ) -> None:
        """Signal callbacks preserve SIGINT and SIGTERM CLI exit semantics."""
        events = prepared_main()

        def request_shutdown() -> None:
            signal_handlers[sig]()

        _FakeCrabstero.connect_callback = request_shutdown

        assert cli.main() == exit_code
        assert ("notify", "STOPPING=1") in events
        assert "close" in events
        assert signal_handlers == {}

    def test_registered_signal_during_login_exits_before_ready(
        self,
        prepared_main: Callable[..., list[object]],
        signal_handlers: dict[int, Callable[[], None]],
    ) -> None:
        """A signal during login cancels startup without reporting readiness."""
        events = prepared_main()

        def request_shutdown() -> None:
            signal_handlers[signal.SIGINT]()

        _FakeCrabstero.login_callback = request_shutdown

        assert cli.main() == 130
        assert ("notify", "READY=1") not in events
        assert ("notify", "STOPPING=1") in events
        assert "close" in events
        assert signal_handlers == {}

    def test_keyboard_interrupt_returns_130(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """KeyboardInterrupt maps to the conventional interrupted exit code."""

        def interrupt(coro: Coroutine[Any, Any, None]) -> None:
            coro.close()
            raise KeyboardInterrupt

        monkeypatch.setattr(sys, "argv", ["crabstero", "--token", "cli-token"])
        monkeypatch.setattr("crabstero.cli.uvloop.run", interrupt)

        assert cli.main() == 130
