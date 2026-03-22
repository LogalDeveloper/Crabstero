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

"""Unit tests for the IngestCache TTL cache."""

import asyncio
import time

from crabstero.cache import CachedMessage, IngestCache


class TestIngestCache:
    """IngestCache put, pop, and expiry behavior."""

    def test_put_and_pop(self) -> None:
        """A cached message can be retrieved by message ID."""
        cache = IngestCache()
        entry = CachedMessage(
            channel_id=1,
            user_id=100,
            content="Hello world",
            embed_texts=["embed title"],
            image_urls=["https://example.com/cat.png"],
        )
        cache.put(12345, entry)
        assert cache.pop(12345) is entry

    def test_pop_removes_entry(self) -> None:
        """Popping an entry removes it from the cache."""
        cache = IngestCache()
        entry = CachedMessage(
            channel_id=1,
            user_id=100,
            content="Hello",
            embed_texts=[],
            image_urls=[],
        )
        cache.put(12345, entry)
        cache.pop(12345)
        assert cache.pop(12345) is None

    def test_pop_returns_none_for_missing(self) -> None:
        """Popping a nonexistent key returns None."""
        cache = IngestCache()
        assert cache.pop(99999) is None

    def test_expired_entry_not_returned(self) -> None:
        """An expired entry is not returned by pop."""
        cache = IngestCache(ttl_seconds=0.01)
        entry = CachedMessage(
            channel_id=1,
            user_id=100,
            content="Hello",
            embed_texts=[],
            image_urls=[],
        )
        cache.put(12345, entry)
        time.sleep(0.02)
        assert cache.pop(12345) is None

    def test_cleanup_removes_expired(self) -> None:
        """Cleanup evicts all expired entries."""
        cache = IngestCache(ttl_seconds=0.01)
        entry = CachedMessage(
            channel_id=1,
            user_id=100,
            content="Hello",
            embed_texts=[],
            image_urls=[],
        )
        cache.put(1, entry)
        cache.put(2, entry)
        time.sleep(0.02)
        cache._cleanup()
        assert cache.pop(1) is None
        assert cache.pop(2) is None

    def test_cleanup_keeps_unexpired(self) -> None:
        """Cleanup does not evict entries that are still valid."""
        cache = IngestCache(ttl_seconds=300)
        entry = CachedMessage(
            channel_id=1,
            user_id=100,
            content="Hello",
            embed_texts=[],
            image_urls=[],
        )
        cache.put(1, entry)
        cache._cleanup()
        assert cache.pop(1) is entry

    async def test_start_and_stop(self) -> None:
        """The background cleanup task can be started and stopped."""
        cache = IngestCache(ttl_seconds=0, cleanup_interval_seconds=0.01)
        entry = CachedMessage(
            channel_id=1,
            user_id=100,
            content="Hello",
            embed_texts=[],
            image_urls=[],
        )
        cache.put(1, entry)
        cache.start()
        await asyncio.sleep(0.05)
        cache.stop()
        # Entry should have been cleaned up by the background task.
        assert cache.pop(1) is None
