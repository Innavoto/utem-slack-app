"""Tests for the durable OAuth-state / welcome-dedupe store (G80).

The store replaces two module-level in-memory containers that were lost on
restart and collided across the 2 Uvicorn workers the Dockerfile runs
(`--workers 2`) as well as across pod replicas. These tests prove the
Redis-backed store:

  * survives a simulated process restart (a fresh StateStore backed by a
    fresh client on the SAME Redis server sees state written by the prior
    instance) — i.e. it is persisted, not in-process;
  * dedupes welcomes across independent instances (replicas);
  * still functions (single-process only) when no Redis is configured.
"""
from __future__ import annotations

import fakeredis.aioredis
import pytest

from app.services.state_store import StateStore


def _new_client(server):
    return fakeredis.aioredis.FakeRedis(server=server, decode_responses=True)


@pytest.mark.asyncio
async def test_oauth_state_survives_restart():
    """State written by one instance is readable by a fresh instance on the
    same Redis (== a process restart / a different replica)."""
    server = fakeredis.aioredis.FakeServer()

    store_a = StateStore(client=_new_client(server))
    await store_a.put_oauth_state("state-xyz", tenant_id=77)

    # Simulate a restart: brand-new store + brand-new client, same Redis server.
    store_b = StateStore(client=_new_client(server))
    assert await store_b.pop_oauth_state("state-xyz") == 77


@pytest.mark.asyncio
async def test_two_workspaces_keep_distinct_tenants():
    """Distinct states resolve to their own tenant — never a shared default."""
    server = fakeredis.aioredis.FakeServer()
    store = StateStore(client=_new_client(server))

    await store.put_oauth_state("state-acme", tenant_id=42)
    await store.put_oauth_state("state-globex", tenant_id=99)

    assert await store.pop_oauth_state("state-acme") == 42
    assert await store.pop_oauth_state("state-globex") == 99


@pytest.mark.asyncio
async def test_oauth_state_is_single_use():
    server = fakeredis.aioredis.FakeServer()
    store = StateStore(client=_new_client(server))

    await store.put_oauth_state("state-once", tenant_id=5)
    assert await store.pop_oauth_state("state-once") == 5
    # second pop must miss — GETDEL removed it
    assert await store.pop_oauth_state("state-once") is None


@pytest.mark.asyncio
async def test_pop_unknown_state_returns_none():
    server = fakeredis.aioredis.FakeServer()
    store = StateStore(client=_new_client(server))
    assert await store.pop_oauth_state("never-created") is None


@pytest.mark.asyncio
async def test_welcome_dedupe_across_instances():
    """First mark wins; a second instance (another replica) sees the user as
    already welcomed."""
    server = fakeredis.aioredis.FakeServer()
    store_a = StateStore(client=_new_client(server))
    store_b = StateStore(client=_new_client(server))

    assert await store_a.mark_welcomed("T1:U1") is True
    # Different replica, same Redis — must NOT re-welcome.
    assert await store_b.mark_welcomed("T1:U1") is False
    # A different user is still welcomed.
    assert await store_b.mark_welcomed("T1:U2") is True


@pytest.mark.asyncio
async def test_memory_fallback_without_redis_still_works_in_process():
    """With no Redis configured the store degrades to per-process memory: it
    works within one instance but is explicitly NOT durable across a restart."""
    store = StateStore(redis_url="")
    await store.put_oauth_state("mem-state", tenant_id=3)
    assert await store.pop_oauth_state("mem-state") == 3
    assert await store.mark_welcomed("T:U") is True
    assert await store.mark_welcomed("T:U") is False

    # A fresh in-memory store does NOT see the prior instance's state
    # (documents why Redis is required in multi-worker / multi-replica prod).
    fresh = StateStore(redis_url="")
    assert await fresh.pop_oauth_state("mem-state") is None


@pytest.mark.asyncio
async def test_redis_outage_falls_back_to_memory():
    """If the Redis client raises, the store degrades to memory instead of
    breaking the OAuth request."""

    class _BoomClient:
        async def set(self, *a, **k):
            raise ConnectionError("redis down")

        async def getdel(self, *a, **k):
            raise ConnectionError("redis down")

    store = StateStore(client=_BoomClient())
    # put falls back to memory; pop (same instance) reads it back
    await store.put_oauth_state("s", tenant_id=8)
    assert await store.pop_oauth_state("s") == 8


@pytest.mark.asyncio
async def test_lazy_builds_real_redis_client_from_url():
    """A configured REDIS_URL lazily builds a redis.asyncio client (connection
    is deferred, so no live server is required just to construct it)."""
    store = StateStore(redis_url="redis://localhost:6390/0")
    client = await store._get_client()
    assert client is not None
    # cached on second call
    assert await store._get_client() is client
    await store.close()


@pytest.mark.asyncio
async def test_close_is_safe_without_client():
    store = StateStore(redis_url="")
    await store.close()  # no client built — must not raise
