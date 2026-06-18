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

"""Integration tests for systemd notification socket behavior."""

import logging
import os
import socket
import sys
from typing import TYPE_CHECKING

import pytest

from crabstero.cli import _sd_notify

if TYPE_CHECKING:
    from pathlib import Path


pytestmark = pytest.mark.skipif(
    os.name != "posix" or not hasattr(socket, "AF_UNIX"),
    reason="systemd notification sockets require Unix-domain socket support",
)


class TestSystemdNotifySocket:
    """Systemd notification helper behavior against real Unix-domain sockets."""

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

    def test_sd_notify_ignores_socket_errors(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """_sd_notify logs and suppresses notification socket send failures."""
        monkeypatch.setenv("NOTIFY_SOCKET", str(tmp_path / "missing.sock"))

        with caplog.at_level(logging.DEBUG, logger="crabstero"):
            _sd_notify("READY=1")

        assert "Could not send systemd notification" in caplog.text

    @pytest.mark.skipif(
        sys.platform != "linux",
        reason="abstract Unix sockets are Linux-specific",
    )
    def test_sd_notify_sends_to_abstract_socket(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """@-prefixed NOTIFY_SOCKET values target Linux abstract sockets."""
        socket_name = f"crabstero-notify-{os.getpid()}"

        with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as server:
            server.bind(f"\0{socket_name}")
            server.settimeout(1)
            monkeypatch.setenv("NOTIFY_SOCKET", f"@{socket_name}")

            _sd_notify("READY=1")

            assert server.recv(1024) == b"READY=1"
