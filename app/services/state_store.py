"""Durable OAuth-state / welcome-dedupe store (G80).

Replaces two module-level in-memory containers that were lost on restart and
did not survive the 2 Uvicorn workers the Dockerfile runs (`--workers 2`),
let alone multiple pod replicas:

  * OAuth CSRF `state` → tenant_id  (10-minute round-trip window)
  * "already welcomed this user"    (dedupe the app_home_opened greeting)

Both are now backed by the shared cluster Redis
(`utem-redis-master.utem-system.svc.cluster.local:6379`). Redis TTLs replace
the old manual cleanup, and `GETDEL` / `SET NX` make pop / dedupe atomic
across workers and replicas.

If `REDIS_URL` is unset, or Redis is unreachable at call time, the store
degrades to a per-process in-memory map (with a WARN log + metric) so local
dev and unit tests still run — that fallback is explicitly NOT durable and
must never be relied on in multi-worker / multi-replica production.
"""
from __future__ import annotations

import time
from typing import Optional

import structlog

from app import metrics

log = structlog.get_logger()

_STATE_PREFIX = "slack:oauth:state:"
_WELCOME_PREFIX = "slack:welcomed:"

# OAuth authorize round-trip window. Slack redirects the user back well within
# this; anything older is a stale/abandoned attempt and should not validate.
STATE_TTL_SECONDS = 600
# How long to remember that a user was already greeted (bounded so the set
# cannot grow forever; a re-greet after a month is acceptable).
WELCOME_TTL_SECONDS = 60 * 60 * 24 * 30

# Short timeouts so a Redis outage degrades quickly to the memory fallback
# instead of stalling an OAuth callback or a Slack event ack.
_SOCKET_TIMEOUT = 2


class StateStore:
    """Redis-backed store with a per-process in-memory fallback.

    A caller may inject a pre-built async Redis-like ``client`` (used by tests
    with fakeredis); otherwise a client is lazily built from ``redis_url``.
    """

    def __init__(self, redis_url: str = "", client=None):
        self._redis_url = redis_url or ""
        self._client = client
        self._client_ready = client is not None
        # key -> (expiry_epoch, value) fallback map
        self._mem: dict[str, tuple[float, str]] = {}

    # -- client management ---------------------------------------------------

    async def _get_client(self):
        if self._client_ready:
            return self._client
        if not self._redis_url:
            return None
        try:
            import redis.asyncio as aioredis

            self._client = aioredis.from_url(
                self._redis_url,
                decode_responses=True,
                socket_connect_timeout=_SOCKET_TIMEOUT,
                socket_timeout=_SOCKET_TIMEOUT,
            )
        except Exception:
            log.warning("state_store_redis_init_failed", redis_url=self._redis_url)
            self._client = None
        self._client_ready = True
        return self._client

    async def close(self) -> None:
        client = self._client
        if client is not None and hasattr(client, "aclose"):
            try:
                await client.aclose()
            except Exception:  # pragma: no cover - best-effort teardown
                pass

    # -- OAuth state ---------------------------------------------------------

    async def put_oauth_state(self, state: str, tenant_id: int) -> None:
        key = _STATE_PREFIX + state
        client = await self._get_client()
        if client is not None:
            try:
                await client.set(key, str(tenant_id), ex=STATE_TTL_SECONDS)
                metrics.state_store_ops.labels(op="put_state", backend="redis").inc()
                return
            except Exception:
                log.warning("state_store_redis_error", op="put_state")
        self._mem_set(key, str(tenant_id), STATE_TTL_SECONDS)
        metrics.state_store_ops.labels(op="put_state", backend="memory").inc()

    async def pop_oauth_state(self, state: str) -> Optional[int]:
        key = _STATE_PREFIX + state
        client = await self._get_client()
        if client is not None:
            try:
                value = await client.getdel(key)
                metrics.state_store_ops.labels(op="pop_state", backend="redis").inc()
                return int(value) if value is not None else None
            except Exception:
                log.warning("state_store_redis_error", op="pop_state")
        metrics.state_store_ops.labels(op="pop_state", backend="memory").inc()
        item = self._mem_pop(key)
        return int(item) if item is not None else None

    # -- welcome dedupe ------------------------------------------------------

    async def mark_welcomed(self, dedupe_key: str) -> bool:
        """Atomically record that ``dedupe_key`` was welcomed.

        Returns True if this is the first time (caller should send the
        welcome), False if it was already recorded (skip).
        """
        key = _WELCOME_PREFIX + dedupe_key
        client = await self._get_client()
        if client is not None:
            try:
                added = await client.set(key, "1", nx=True, ex=WELCOME_TTL_SECONDS)
                metrics.state_store_ops.labels(op="mark_welcomed", backend="redis").inc()
                return bool(added)
            except Exception:
                log.warning("state_store_redis_error", op="mark_welcomed")
        metrics.state_store_ops.labels(op="mark_welcomed", backend="memory").inc()
        if self._mem_get(key) is not None:
            return False
        self._mem_set(key, "1", WELCOME_TTL_SECONDS)
        return True

    # -- in-memory fallback --------------------------------------------------

    def _mem_purge(self) -> None:
        now = time.time()
        expired = [k for k, (exp, _v) in self._mem.items() if exp <= now]
        for k in expired:
            self._mem.pop(k, None)

    def _mem_set(self, key: str, value: str, ttl: int) -> None:
        self._mem_purge()
        self._mem[key] = (time.time() + ttl, value)

    def _mem_get(self, key: str) -> Optional[str]:
        self._mem_purge()
        item = self._mem.get(key)
        return item[1] if item else None

    def _mem_pop(self, key: str) -> Optional[str]:
        self._mem_purge()
        item = self._mem.pop(key, None)
        return item[1] if item else None
