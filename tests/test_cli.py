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

"""Unit tests for CLI argument parsing and credential reading.

Tests cover _parse_args and _read_credential from crabstero.__main__.
"""

from typing import TYPE_CHECKING

import pytest

from crabstero.__main__ import _parse_args, _read_credential

if TYPE_CHECKING:
    from pathlib import Path


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

    def test_ingestion_workers_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Omitting --ingestion-workers defaults to 4."""
        monkeypatch.delenv("INGESTION_WORKERS", raising=False)
        args = _parse_args(["--token", "test"])
        assert args.ingestion_workers == 4

    def test_ingestion_workers_override(self) -> None:
        """--ingestion-workers overrides the default."""
        args = _parse_args(["--token", "test", "--ingestion-workers", "8"])
        assert args.ingestion_workers == 8

    def test_ingest_only_flag(self) -> None:
        """--ingest-only sets ingest_only to True."""
        args = _parse_args(["--token", "test", "--ingest-only"])
        assert args.ingest_only is True

    def test_missing_token_exits(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Missing token causes SystemExit."""
        monkeypatch.delenv("TOKEN", raising=False)
        monkeypatch.delenv("CREDENTIALS_DIRECTORY", raising=False)
        with pytest.raises(SystemExit):
            _parse_args([])


class TestReadCredential:
    """Systemd credential file reading."""

    def test_reads_credential_file(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
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
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Returns None when the credential file does not exist."""
        monkeypatch.setenv("CREDENTIALS_DIRECTORY", str(tmp_path))
        assert _read_credential("nonexistent") is None
