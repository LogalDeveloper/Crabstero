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

"""Command-line interface for the Crabstero Discord bot.

Provides an argparse CLI with environment variable and systemd credential
fallbacks, plus systemd notify/watchdog integration when run as a service.
"""

import argparse
import asyncio
import contextlib
import logging
import os
import signal
import socket
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, cast

import uvloop

from crabstero.bot import Crabstero
from crabstero.metrics import MetricsAddress, TcpMetricsAddress, UnixMetricsAddress

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

logger = logging.getLogger("crabstero")

_KEYBOARD_INTERRUPT_EXIT_CODE = 130
_MAX_METRICS_UNIX_SOCKET_MODE = 0o777
_HANDLED_SIGNALS = (signal.SIGINT, signal.SIGTERM)


@dataclass(frozen=True, slots=True)
class _CliConfig:
    """Resolved command-line configuration."""

    token: str
    database_path: str
    ingest_only: bool
    metrics_address: MetricsAddress | None


def _sd_notify(state: str) -> None:
    """Send a notification to systemd, if running under a systemd service.

    :param state: The systemd notification payload.
    """
    addr = os.environ.get("NOTIFY_SOCKET")
    if not addr:
        return

    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as sock:
            if addr[0] == "@":
                addr = "\0" + addr[1:]
            sock.sendto(state.encode(), addr)
    except OSError as exc:
        logger.debug("Could not send systemd notification: %s", exc)


def _watchdog_interval() -> float | None:
    """Return the systemd watchdog ping interval in seconds, if enabled."""
    usec = os.environ.get("WATCHDOG_USEC")
    if not usec:
        return None

    pid = os.environ.get("WATCHDOG_PID")
    if pid is not None:
        try:
            watchdog_pid = int(pid)
        except ValueError:
            return None
        if watchdog_pid != os.getpid():
            return None

    try:
        watchdog_usec = int(usec)
    except ValueError:
        return None

    if watchdog_usec <= 0:
        return None

    return watchdog_usec / 1_000_000 / 2


async def _watchdog_loop(interval: float, stop: asyncio.Event) -> None:
    """Send systemd watchdog pings until *stop* is set.

    :param interval: Seconds to wait between watchdog pings.
    :param stop: Event that stops the watchdog loop.
    """
    while not stop.is_set():
        _sd_notify("WATCHDOG=1")
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(stop.wait(), timeout=interval)


def _read_credential(name: str) -> str | None:
    """Read a value from a systemd credential file.

    Looks for a file named *name* inside the directory pointed to by the
    ``CREDENTIALS_DIRECTORY`` environment variable (set automatically by
    systemd when ``LoadCredential=`` or ``SetCredential=`` is used).

    :param name: Credential name to look up.
    :return: The credential value, or ``None`` if unavailable.
    """
    credentials_dir = os.environ.get("CREDENTIALS_DIRECTORY")
    if credentials_dir is None:
        return None

    try:
        return Path(credentials_dir, name).read_text().strip()
    except OSError:
        return None


@contextlib.contextmanager
def _install_signal_handlers(
    loop: asyncio.AbstractEventLoop,
    callback: Callable[[signal.Signals], None],
) -> Iterator[None]:
    """Install process signal handlers and always remove installed handlers."""
    registered_signals: list[signal.Signals] = []
    try:
        for sig in _HANDLED_SIGNALS:
            loop.add_signal_handler(sig, callback, sig)
            registered_signals.append(sig)
        yield
    finally:
        for sig in registered_signals:
            loop.remove_signal_handler(sig)


def _parse_args(argv: list[str] | None = None) -> _CliConfig:
    """Parse command-line arguments with environment variable fallbacks.

    :param argv: Optional argument list (defaults to sys.argv[1:]).
    :return: Resolved command-line configuration.
    """
    parser = argparse.ArgumentParser(
        prog="crabstero",
        description="Crabstero - the simple nonversation Discord bot.",
    )
    parser.add_argument(
        "--token",
        default=os.environ.get("TOKEN") or _read_credential("token"),
        help=(
            "Discord bot token"
            " (default: TOKEN environment variable"
            " or systemd credential 'token')."
        ),
    )
    parser.add_argument(
        "--database-path",
        "--database",
        default=os.environ.get("DATABASE_PATH", "crabstero.db"),
        help=(
            "Path to the SQLite database file"
            " (default: DATABASE_PATH environment"
            ' variable or "crabstero.db").'
        ),
    )
    parser.add_argument(
        "--ingest-only",
        action="store_true",
        default=False,
        help=(
            "Run in ingest-only mode: ingest channel"
            " history and real-time messages but"
            " never respond."
        ),
    )
    parser.add_argument(
        "--listen-metrics",
        default=os.environ.get("LISTEN_METRICS"),
        help=(
            "Enable Prometheus metrics endpoint on HOST:PORT or unix:/path.sock"
            " (e.g. 127.0.0.1:9090 or unix:/run/crabstero.sock)."
            " Disabled by default."
            " (default: LISTEN_METRICS environment variable)."
        ),
    )
    parser.add_argument(
        "--metrics-unix-socket-mode",
        default=os.environ.get("METRICS_UNIX_SOCKET_MODE"),
        help=(
            "Octal file mode to set on a metrics Unix socket"
            " (e.g. 0666). Only valid with --listen-metrics unix:/path.sock."
            " (default: METRICS_UNIX_SOCKET_MODE environment variable)."
        ),
    )

    args = parser.parse_args(argv)

    token = cast("str | None", args.token)
    database_path = cast("str", args.database_path)
    ingest_only = cast("bool", args.ingest_only)
    raw_metrics_address = cast("str | None", args.listen_metrics)
    raw_metrics_unix_socket_mode = cast(
        "str | None",
        args.metrics_unix_socket_mode,
    )

    metrics_unix_socket_mode: int | None = None
    if raw_metrics_unix_socket_mode is not None:
        try:
            metrics_unix_socket_mode = int(raw_metrics_unix_socket_mode, 8)
        except ValueError:
            parser.error(
                "--metrics-unix-socket-mode must be an octal mode"
                " between 0000 and 0777",
            )
        if not 0 <= metrics_unix_socket_mode <= _MAX_METRICS_UNIX_SOCKET_MODE:
            parser.error(
                "--metrics-unix-socket-mode must be an octal mode"
                " between 0000 and 0777",
            )

    metrics_address: MetricsAddress | None = None
    if raw_metrics_address is not None:
        if raw_metrics_address.startswith("unix:"):
            path = raw_metrics_address.removeprefix("unix:")
            if not path:
                parser.error("--listen-metrics Unix socket path must not be empty")
            metrics_address = UnixMetricsAddress(path, metrics_unix_socket_mode)
        else:
            if metrics_unix_socket_mode is not None:
                parser.error(
                    "--metrics-unix-socket-mode is only valid with"
                    " --listen-metrics unix:/path.sock",
                )
            host, sep, port_str = raw_metrics_address.rpartition(":")
            if not sep or not host:
                parser.error(
                    "--listen-metrics must be in HOST:PORT or unix:/path.sock format "
                    "(e.g. 127.0.0.1:9090)",
                )
            try:
                metrics_address = TcpMetricsAddress(host, int(port_str))
            except ValueError:
                parser.error(
                    f"--listen-metrics port must be an integer, got '{port_str}'",
                )
    elif metrics_unix_socket_mode is not None:
        parser.error(
            "--metrics-unix-socket-mode is only valid with"
            " --listen-metrics unix:/path.sock",
        )

    if token is None:
        parser.error(
            "a Discord bot token is required via --token,"
            " the TOKEN environment variable,"
            " or a systemd credential named 'token'",
        )

    return _CliConfig(
        token=token,
        database_path=database_path,
        ingest_only=ingest_only,
        metrics_address=metrics_address,
    )


async def _run(config: _CliConfig) -> signal.Signals | None:
    """Run the bot under CLI-controlled process orchestration."""
    bot = Crabstero(
        database_path=config.database_path,
        ingest_only=config.ingest_only,
        metrics_address=config.metrics_address,
    )

    loop = asyncio.get_running_loop()
    watchdog_stop = asyncio.Event()
    watchdog_task: asyncio.Task[None] | None = None
    shutdown_task: asyncio.Task[None] | None = None
    ready_notified = False
    stopping_notified = False
    cleanup_started = False
    shutdown_signal: signal.Signals | None = None
    runner_task = asyncio.current_task()
    if runner_task is None:
        msg = "Crabstero runner is not running in a task"
        raise RuntimeError(msg)

    def notify_stopping() -> None:
        nonlocal stopping_notified
        if stopping_notified:
            return
        stopping_notified = True
        _sd_notify("STOPPING=1")

    async def shutdown(sig: signal.Signals) -> None:
        logger.info("Received shutdown signal %s.", sig.name)
        notify_stopping()
        await bot.close()

    def request_shutdown(sig: signal.Signals) -> None:
        nonlocal shutdown_signal, shutdown_task
        if shutdown_signal is not None:
            return

        shutdown_signal = sig
        if not cleanup_started:
            shutdown_task = asyncio.create_task(
                shutdown(sig),
                name="crabstero-shutdown",
            )
            runner_task.cancel()

    async def cleanup() -> None:
        nonlocal cleanup_started
        if cleanup_started:
            return

        cleanup_started = True

        if ready_notified:
            notify_stopping()

        watchdog_stop.set()
        if watchdog_task is not None:
            watchdog_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await watchdog_task

        if shutdown_task is not None:
            await shutdown_task

    with _install_signal_handlers(loop, request_shutdown):
        try:
            async with bot:
                try:
                    await bot.login(config.token)
                    if bot.is_closed() or stopping_notified:
                        return shutdown_signal

                    _sd_notify("READY=1")
                    ready_notified = True

                    wd_interval = _watchdog_interval()
                    if wd_interval is not None:
                        watchdog_interval = wd_interval
                        logger.info(
                            "systemd watchdog enabled (pinging every %.1fs).",
                            watchdog_interval,
                        )
                        watchdog_task = asyncio.create_task(
                            _watchdog_loop(watchdog_interval, watchdog_stop),
                            name="systemd-watchdog",
                        )

                    await bot.connect(reconnect=True)
                finally:
                    await cleanup()
        except asyncio.CancelledError:
            if shutdown_signal is None:
                raise
        finally:
            await cleanup()

    return shutdown_signal


def main(argv: list[str] | None = None) -> int:
    """Run the bot CLI.

    :param argv: Optional argument list (defaults to sys.argv[1:]).
    :return: Process exit code.
    """
    config = _parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="[%(asctime)s] [%(name)s] [%(levelname)s] %(message)s",
    )

    try:
        shutdown_signal = uvloop.run(_run(config))
    except KeyboardInterrupt:
        logger.info("Interrupted.")
        return _KEYBOARD_INTERRUPT_EXIT_CODE

    if shutdown_signal == signal.SIGINT:
        return _KEYBOARD_INTERRUPT_EXIT_CODE
    return 0
