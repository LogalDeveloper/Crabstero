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

import pytest

from crabstero.cache import CachedMessage, IngestCache


class _FakeClock:
    """Mutable monotonic clock for deterministic cache expiry tests."""

    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        """Return the current fake monotonic timestamp."""
        return self.now

    def advance(self, seconds: float) -> None:
        """Move the fake clock forward by the given number of seconds."""
        self.now += seconds


@pytest.fixture
def cached_message() -> CachedMessage:
    """Return a representative cache entry for put/pop behavior tests."""
    return CachedMessage(
        channel_id=1,
        user_id=100,
        content="Hello world",
        embed_texts=["embed title"],
        image_urls=["https://example.com/cat.png"],
    )


@pytest.fixture
def fake_clock(monkeypatch: pytest.MonkeyPatch) -> _FakeClock:
    """Patch the cache's clock and return a controllable timestamp source."""
    clock = _FakeClock()
    monkeypatch.setattr("crabstero.cache.time.monotonic", clock)
    return clock


class TestIngestCache:
    """IngestCache put, pop, and expiry behavior."""

    def test_put_and_pop(self, cached_message: CachedMessage) -> None:
        """A cached message can be retrieved by message ID."""
        cache = IngestCache()
        cache.put(12345, cached_message)
        assert cache.pop(12345) is cached_message

    def test_pop_removes_entry(self, cached_message: CachedMessage) -> None:
        """Popping an entry removes it from the cache."""
        cache = IngestCache()
        cache.put(12345, cached_message)
        cache.pop(12345)
        assert cache.pop(12345) is None

    def test_pop_returns_none_for_missing(self) -> None:
        """Popping a nonexistent key returns None."""
        cache = IngestCache()
        assert cache.pop(99999) is None

    def test_expired_entry_not_returned(
        self,
        cached_message: CachedMessage,
        fake_clock: _FakeClock,
    ) -> None:
        """An expired entry is not returned by pop."""
        cache = IngestCache(ttl_seconds=10)
        cache.put(12345, cached_message)
        fake_clock.advance(11)
        assert cache.pop(12345) is None

    def test_cleanup_removes_expired(
        self,
        cached_message: CachedMessage,
        fake_clock: _FakeClock,
    ) -> None:
        """Cleanup evicts all expired entries."""
        cache = IngestCache(ttl_seconds=10)
        cache.put(1, cached_message)
        cache.put(2, cached_message)
        fake_clock.advance(11)
        cache._cleanup()
        assert cache.pop(1) is None
        assert cache.pop(2) is None

    def test_cleanup_keeps_unexpired(self, cached_message: CachedMessage) -> None:
        """Cleanup does not evict entries that are still valid."""
        cache = IngestCache(ttl_seconds=300)
        cache.put(1, cached_message)
        cache._cleanup()
        assert cache.pop(1) is cached_message

    async def test_start_and_stop(self) -> None:
        """The background cleanup task can be started and stopped."""
        cache = IngestCache()
        cache.start()
        assert cache._task is not None

        await cache.stop()

        assert cache._task is None

    async def test_start_is_idempotent(self) -> None:
        """Starting an already-started cache keeps the existing cleanup task."""
        cache = IngestCache()
        cache.start()
        try:
            task = cache._task
            cache.start()
            assert cache._task is task
        finally:
            await cache.stop()
