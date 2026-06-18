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

"""Integration tests for the Prometheus metrics HTTP server."""

import errno
import os
import socket
from dataclasses import dataclass
from http import HTTPStatus
from typing import TYPE_CHECKING, Literal, cast

import aiohttp
import pytest

from crabstero.metrics import MetricsServer, TcpMetricsAddress, UnixMetricsAddress

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator
    from pathlib import Path


type MetricsTransport = Literal["tcp", "unix-socket"]

requires_unix_socket = pytest.mark.skipif(
    os.name != "posix" or not hasattr(socket, "AF_UNIX"),
    reason="Unix-socket metrics transports require Unix-domain socket support",
)


@dataclass(frozen=True, slots=True)
class MetricsEndpoint:
    """Client details for one running metrics transport."""

    base_url: str
    unix_socket_path: str | None = None

    def client_session(self) -> aiohttp.ClientSession:
        """Create an aiohttp client session for this metrics transport."""
        connector = (
            aiohttp.UnixConnector(path=self.unix_socket_path)
            if self.unix_socket_path is not None
            else None
        )
        return aiohttp.ClientSession(connector=connector)


@pytest.fixture(
    params=[
        pytest.param("tcp", id="tcp"),
        pytest.param("unix-socket", marks=requires_unix_socket, id="unix-socket"),
    ],
)
async def metrics_endpoint(
    request: pytest.FixtureRequest,
    tmp_path: Path,
) -> AsyncGenerator[MetricsEndpoint]:
    """Start a MetricsServer on each supported transport."""
    transport = cast("MetricsTransport", request.param)
    match transport:
        case "tcp":
            server = MetricsServer(TcpMetricsAddress("127.0.0.1", 0))
            await server.start()
            endpoint = MetricsEndpoint(f"http://127.0.0.1:{server.port}")
        case "unix-socket":
            socket_path = tmp_path / "metrics.sock"
            server = MetricsServer(UnixMetricsAddress(str(socket_path)))
            await server.start()
            endpoint = MetricsEndpoint(
                "http://crabstero",
                unix_socket_path=str(socket_path),
            )

    try:
        yield endpoint
    finally:
        await server.stop()


class TestMetricsServer:
    """HTTP server serves Prometheus metrics on /metrics."""

    @requires_unix_socket
    def test_unix_socket_server_has_no_tcp_port(self, tmp_path: Path) -> None:
        """Unix-socket metrics servers do not expose a TCP port."""
        server = MetricsServer(UnixMetricsAddress(str(tmp_path / "metrics.sock")))

        with pytest.raises(RuntimeError, match="does not have a TCP port"):
            _ = server.port

    async def test_serves_metrics_endpoint(
        self,
        metrics_endpoint: MetricsEndpoint,
    ) -> None:
        """GET /metrics returns 200 with metric output containing our metrics."""
        async with (
            metrics_endpoint.client_session() as session,
            session.get(f"{metrics_endpoint.base_url}/metrics") as resp,
        ):
            assert resp.status == HTTPStatus.OK
            body = await resp.text()
            assert "crabstero_build_info" in body

    async def test_non_metrics_path_returns_404(
        self,
        metrics_endpoint: MetricsEndpoint,
    ) -> None:
        """GET on an unknown path returns 404."""
        async with (
            metrics_endpoint.client_session() as session,
            session.get(f"{metrics_endpoint.base_url}/notfound") as resp,
        ):
            assert resp.status == HTTPStatus.NOT_FOUND

    @requires_unix_socket
    async def test_unix_socket_mode_is_applied(self, tmp_path: Path) -> None:
        """Configured Unix socket mode is applied after startup."""
        socket_path = tmp_path / "metrics.sock"
        server = MetricsServer(UnixMetricsAddress(str(socket_path), mode=0o666))
        await server.start()
        try:
            assert socket_path.stat().st_mode & 0o777 == 0o666
        finally:
            await server.stop()

    @requires_unix_socket
    async def test_stale_unix_socket_path_is_recovered(
        self,
        tmp_path: Path,
    ) -> None:
        """Startup removes an abandoned Unix socket file left by a crash."""
        socket_path = tmp_path / "metrics.sock"
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as stale_socket:
            stale_socket.bind(str(socket_path))

        server = MetricsServer(UnixMetricsAddress(str(socket_path), mode=0o666))
        await server.start()
        try:
            assert socket_path.stat().st_mode & 0o777 == 0o666
            connector = aiohttp.UnixConnector(path=str(socket_path))
            async with (
                aiohttp.ClientSession(connector=connector) as session,
                session.get("http://crabstero/metrics") as resp,
            ):
                assert resp.status == HTTPStatus.OK
                body = await resp.text()
                assert "crabstero_build_info" in body
        finally:
            await server.stop()

    @requires_unix_socket
    async def test_active_unix_socket_path_fails(
        self,
        tmp_path: Path,
    ) -> None:
        """Startup refuses to replace a Unix socket path that is still in use."""
        socket_path = tmp_path / "metrics.sock"
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as active_socket:
            active_socket.bind(str(socket_path))
            active_socket.listen(1)

            server = MetricsServer(UnixMetricsAddress(str(socket_path)))
            with pytest.raises(OSError, match="already in use") as exc_info:
                await server.start()

        assert exc_info.value.errno == errno.EADDRINUSE

    @requires_unix_socket
    async def test_non_socket_unix_path_fails(self, tmp_path: Path) -> None:
        """Startup refuses to replace a non-socket path."""
        socket_path = tmp_path / "metrics.sock"
        socket_path.write_text("")
        server = MetricsServer(UnixMetricsAddress(str(socket_path)))
        with pytest.raises(FileExistsError):
            await server.start()

    async def test_starting_server_twice_fails(self) -> None:
        """A running metrics server cannot be started twice."""
        server = MetricsServer(TcpMetricsAddress("127.0.0.1", 0))
        await server.start()
        try:
            with pytest.raises(RuntimeError, match="already running"):
                await server.start()
        finally:
            await server.stop()
