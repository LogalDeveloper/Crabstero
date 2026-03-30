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

"""Entry point for the Crabstero Discord bot.

Provides an argparse CLI with environment variable and systemd credential
fallbacks for --token and --database-path.
"""

import argparse
import asyncio
import logging
import os
import signal
from pathlib import Path

from crabstero import __version__ as crabstero_version
from crabstero.bot import Crabstero

logger = logging.getLogger("crabstero")


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


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse command-line arguments with environment variable fallbacks.

    :param argv: Optional argument list (defaults to sys.argv[1:]).
    :return: Parsed arguments namespace.
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
            "Enable Prometheus metrics endpoint on HOST:PORT"
            " (e.g. 127.0.0.1:9090). Disabled by default."
            " (default: LISTEN_METRICS environment variable)."
        ),
    )

    args = parser.parse_args(argv)

    if args.listen_metrics is not None:
        host, sep, port_str = args.listen_metrics.rpartition(":")
        if not sep or not host:
            parser.error(
                "--listen-metrics must be in HOST:PORT format (e.g. 127.0.0.1:9090)",
            )
        try:
            args.listen_metrics = (host, int(port_str))
        except ValueError:
            parser.error(f"--listen-metrics port must be an integer, got '{port_str}'")

    if args.token is None:
        parser.error(
            "a Discord bot token is required via --token,"
            " the TOKEN environment variable,"
            " or a systemd credential named 'token'",
        )

    return args


def main() -> None:
    """Run the bot. Parse arguments, configure logging, and start the bot."""
    args = _parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="[%(asctime)s] [%(name)s] [%(levelname)s] %(message)s",
    )

    logger.info("Starting Crabstero %s...", crabstero_version)

    bot = Crabstero(
        token=args.token,
        database_path=args.database_path,
        ingest_only=args.ingest_only,
        metrics_address=args.listen_metrics,
    )

    async def _run() -> None:
        async with bot:
            await bot.start()

    # Make SIGTERM behave like SIGINT so systemd stop triggers the same
    # clean shutdown path (context manager __aexit__ -> bot.close()).
    signal.signal(signal.SIGTERM, signal.default_int_handler)

    try:
        asyncio.run(_run())
    except KeyboardInterrupt:
        logger.info("Interrupted.")


if __name__ == "__main__":
    main()
