"""app_home_opened welcome-dedupe is durable across restarts/replicas (G80).

The greeting used to be deduped by a per-process in-memory set, so every
restart re-welcomed everyone and each worker/replica greeted independently.
It now dedupes via the shared StateStore. These tests drive the actual
registered Bolt handler to prove the wiring.
"""
from __future__ import annotations

from unittest.mock import AsyncMock

import fakeredis.aioredis
import pytest
from slack_bolt.async_app import AsyncApp

from app.handlers.events import register_events
from app.services.state_store import StateStore


def _capture_home_opened_handler(state_store):
    """Register events on a throwaway app and return the app_home_opened
    listener's underlying async function."""
    app = AsyncApp(
        signing_secret="x", token=None, process_before_response=True
    )
    register_events(app, client=AsyncMock(), state_store=state_store)
    for listener in app._async_listeners:
        fn = listener.ack_function
        if fn.__name__ == "handle_home_opened":
            return fn
    raise AssertionError("handle_home_opened not registered")


@pytest.mark.asyncio
async def test_welcome_fires_once_then_dedupes_across_restart():
    server = fakeredis.aioredis.FakeServer()

    # First "process": greet U1 in workspace T1.
    store1 = StateStore(client=fakeredis.aioredis.FakeRedis(server=server, decode_responses=True))
    handler1 = _capture_home_opened_handler(store1)
    sdk1 = AsyncMock()
    await handler1(
        event={"user": "U1", "team": "T1", "tab": "messages"}, client_sdk=sdk1
    )
    assert sdk1.chat_postMessage.await_count == 1

    # Second "process" (restart / different replica), same Redis: no re-greet.
    store2 = StateStore(client=fakeredis.aioredis.FakeRedis(server=server, decode_responses=True))
    handler2 = _capture_home_opened_handler(store2)
    sdk2 = AsyncMock()
    await handler2(
        event={"user": "U1", "team": "T1", "tab": "messages"}, client_sdk=sdk2
    )
    assert sdk2.chat_postMessage.await_count == 0


@pytest.mark.asyncio
async def test_non_messages_tab_never_welcomes():
    store = StateStore(redis_url="")
    handler = _capture_home_opened_handler(store)
    sdk = AsyncMock()
    await handler(event={"user": "U1", "team": "T1", "tab": "home"}, client_sdk=sdk)
    assert sdk.chat_postMessage.await_count == 0


@pytest.mark.asyncio
async def test_distinct_users_each_welcomed():
    store = StateStore(redis_url="")
    handler = _capture_home_opened_handler(store)
    sdk = AsyncMock()
    await handler(event={"user": "U1", "team": "T1", "tab": "messages"}, client_sdk=sdk)
    await handler(event={"user": "U2", "team": "T1", "tab": "messages"}, client_sdk=sdk)
    assert sdk.chat_postMessage.await_count == 2
