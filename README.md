# Crabstero

The simple nonversation Discord bot.

Crabstero is a Discord bot that learns from messages in each channel and uses
Markov chains to generate responses when it is mentioned.

## Overview

Crabstero is intended for Discord servers that want a lightweight chatbot with
channel-local, randomly generated replies. It does not use a large language
model; it learns from the channels it can read and uses that history to assemble
replies when mentioned.

This repository and README are for running your own instance of Crabstero. To
invite the official hosted instance instead of running your own bot, see the
[Crabstero project page](https://logal.dev/projects/crabstero/).

## Features

- Per-channel Markov chain generation for Discord messages.
- Automatic ingestion of accessible channel history and new messages.
- Mention-triggered replies based on each channel's learned message history.
- `/pingme` user control for opting into pings from generated mentions.
- `/forgetme` user data deletion and future message ingestion opt-out.
- SQLite-backed storage for learned message data.
- Optional Prometheus metrics over TCP or a Unix socket.
- systemd notification and watchdog support when run as a service.

## Requirements

- A Linux host or virtual machine.
- [uv](https://docs.astral.sh/uv/getting-started/installation/) for Python
  management and the install/run commands below.
- A Discord application with a bot token and the Message Content intent enabled.
- Discord channel permissions to view channels, read message history, and send
  messages where Crabstero should operate.

## Discord setup

In the [Discord Developer Portal](https://discord.com/developers/applications),
create an application, add a bot, and enable the Message Content intent for the
bot. Crabstero reads message content and embed text to learn channel-local
chains, so it cannot ingest channel history normally without that privileged
intent.

Invite the application with these OAuth2 scopes:

| Scope | Why Crabstero uses it |
|-------|------------------------|
| `bot` | Add the bot user to the selected server. |
| `applications.commands` | Install the `/pingme` and `/forgetme` slash commands. |

Recommended bot permissions:

| Permission | Why Crabstero uses it |
|------------|------------------------|
| View Channels | See the channels it should learn from and reply in. |
| Send Messages | Send generated replies when mentioned. |
| Send Messages in Threads | Reply when mentioned inside threads. |
| Embed Links | Send occasional generated embed replies. |
| Read Message History | Ingest accessible channel history on startup. |
| Use External Emojis | Preserve learned custom emoji tokens in generated replies. |

The combined permissions integer for the recommended set is
`274878254080`.

Example self-hosted invite URL:

```text
https://discord.com/oauth2/authorize?client_id=YOUR_CLIENT_ID&scope=bot%20applications.commands&permissions=274878254080
```

Replace `YOUR_CLIENT_ID` with the application ID from the Discord Developer
Portal. Channel permission overwrites still apply, so you can limit where
Crabstero learns and replies by restricting the bot role per channel.

## Installation

Create a directory for Crabstero, create a Python 3.14 virtual environment, and
install the package from the project's Gitea package index:

```bash
mkdir crabstero && cd crabstero
uv venv --python 3.14
uv pip install crabstero \
  --index https://git.logal.dev/api/packages/LogalDeveloper/pypi/simple/ \
  --default-index https://pypi.org/simple
```

## Usage

Run Crabstero with a Discord bot token from the environment:

```bash
TOKEN="your-discord-bot-token" uv run crabstero --database-path crabstero.db
```

On startup, Crabstero opens or creates the configured SQLite database, connects
to Discord, and begins ingesting channel history and new messages it can read.
Mention the bot in a channel to receive a generated reply once enough channel
data has been collected.

## Configuration

Crabstero is configured through CLI flags, environment variables, and systemd
credentials. CLI flags take precedence over environment defaults where both are
available.

| Setting | CLI flag | Environment variable | Fallback/default | Description |
|---------|----------|----------------------|------------------|-------------|
| Discord token | `--token` | `TOKEN` | systemd credential `token` | Required Discord bot token. |
| Database path | `--database-path`, `--database` | `DATABASE_PATH` | `crabstero.db` | SQLite database file path. |
| Ingest-only mode | `--ingest-only` | | disabled | Ingest history and real-time messages without replying. |
| Metrics listener | `--listen-metrics` | `LISTEN_METRICS` | disabled | Prometheus metrics address, either `HOST:PORT` or `unix:/path.sock`. |
| Metrics socket mode | `--metrics-unix-socket-mode` | `METRICS_UNIX_SOCKET_MODE` | unchanged | Octal file mode for Unix socket metrics listeners. |

When running under systemd, Crabstero can read the bot token from a credential
named `token`. It also sends readiness, stopping, and watchdog notifications
when the relevant systemd environment variables are present.

## Development

Clone the Git repository and use [uv](https://docs.astral.sh/uv/) to sync the
project environment from the lockfile:

```bash
git clone https://git.logal.dev/LogalDeveloper/Crabstero
cd Crabstero
uv sync --locked
```

The development toolchain uses [Ruff](https://docs.astral.sh/ruff/) for
formatting and linting, [mypy](https://mypy.readthedocs.io/en/stable/) for type
checking, [codespell](https://github.com/codespell-project/codespell) for typo
detection, [pip-audit](https://github.com/pypa/pip-audit) for dependency
auditing, and [pytest](https://docs.pytest.org/en/stable/) with
[pytest-cov](https://pytest-cov.readthedocs.io/en/latest/) and
[Coverage.py](https://coverage.readthedocs.io/) for tests and
coverage reporting.

Run the common checks:

```bash
uv run ruff format --check --diff .
uv run ruff check .
uv run mypy .
uv run codespell
uv run pip-audit --skip-editable
uv run pytest
```

Run tests with coverage reporting:

```bash
uv run pytest -v --cov --cov-report=
uv run coverage report
```

Tests are organized under `tests/unit` and `tests/integration`. The pytest
configuration marks tests automatically based on those directories.

## Links

- Project page: <https://logal.dev/projects/crabstero/>
- Source repository: <https://git.logal.dev/LogalDeveloper/Crabstero>

## License

Crabstero is licensed under the Apache License 2.0. See
[LICENSE.txt](LICENSE.txt) for the full license text.
