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

"""TTL cache for recently ingested messages, enabling uningest on delete."""

import asyncio
import contextlib
import time
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class CachedMessage:
    """Data stored for a recently ingested message.

    :param channel_id: The Discord channel ID the message was ingested into.
    :param user_id: The Discord user ID of the message author.
    :param content: The text content of the message, if any.
    :param embed_texts: Titles and descriptions extracted from embeds.
    :param image_urls: Image URLs extracted from embeds.
    """

    channel_id: int
    user_id: int
    content: str | None
    embed_texts: list[str]
    image_urls: list[str]


class IngestCache:
    """In-memory TTL cache mapping message IDs to their ingested data.

    Entries expire after ``ttl_seconds`` and are evicted by a periodic
    background task.

    :param ttl_seconds: How long entries remain valid, in seconds.
    :param cleanup_interval_seconds: How often the background task runs.
    """

    __slots__ = ("_cleanup_interval", "_entries", "_task", "_ttl")

    def __init__(
        self,
        ttl_seconds: float = 120,
        cleanup_interval_seconds: float = 30,
    ) -> None:
        """Create a new cache with the given TTL and cleanup interval.

        :param ttl_seconds: How long entries remain valid, in seconds.
        :param cleanup_interval_seconds: How often the background task runs.
        """
        self._ttl = ttl_seconds
        self._cleanup_interval = cleanup_interval_seconds
        self._entries: dict[int, tuple[float, CachedMessage]] = {}
        self._task: asyncio.Task[None] | None = None

    def put(self, message_id: int, entry: CachedMessage) -> None:
        """Store a cache entry for a message.

        :param message_id: The Discord message ID.
        :param entry: The cached message data.
        """
        self._entries[message_id] = (time.monotonic(), entry)

    def pop(self, message_id: int) -> CachedMessage | None:
        """Remove and return a cache entry if it exists and has not expired.

        :param message_id: The Discord message ID.
        :return: The cached message data, or None.
        """
        pair = self._entries.pop(message_id, None)
        if pair is None:
            return None
        stored_at, entry = pair
        if time.monotonic() - stored_at > self._ttl:
            return None
        return entry

    def _cleanup(self) -> None:
        """Remove all expired entries from the cache."""
        now = time.monotonic()
        expired = [
            mid
            for mid, (stored_at, _) in self._entries.items()
            if now - stored_at > self._ttl
        ]
        for mid in expired:
            del self._entries[mid]

    def start(self) -> None:
        """Start the periodic background cleanup task."""
        if self._task is not None:
            return
        self._task = asyncio.create_task(
            self._cleanup_loop(), name="ingest-cache-cleanup"
        )

    async def stop(self) -> None:
        """Stop the periodic background cleanup task and wait for it to finish."""
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None

    async def _cleanup_loop(self) -> None:
        """Run cleanup on a fixed interval until cancelled."""
        while True:
            await asyncio.sleep(self._cleanup_interval)
            self._cleanup()
