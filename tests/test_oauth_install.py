"""Tests for the OAuth install/callback flow in app/main.py.

Covers the G61 fix: /oauth/callback must never silently attribute a new
Slack workspace install to a hardcoded tenant id. Tenant identity must flow
through the CSRF `state` created by /oauth/install; installs that arrive
with no state (the Slack-Marketplace direct-install bypass) must be
rejected rather than mis-attributed.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from app import main as main_module
from app import metrics


def _oauth_installs_value(status: str) -> float:
    for metric in metrics.oauth_installs.collect():
        for sample in metric.samples:
            if sample.name.endswith("_total") and sample.labels.get("status") == status:
                return sample.value
    return 0.0


@pytest.fixture
def client():
    transport = ASGITransport(app=main_module.app)
    return AsyncClient(transport=transport, base_url="http://test")


@pytest.fixture(autouse=True)
def _clear_oauth_states():
    main_module._oauth_states.clear()
    yield
    main_module._oauth_states.clear()


class TestOauthInstall:
    @pytest.mark.asyncio
    async def test_missing_tenant_id_rejected(self, client):
        async with client as c:
            resp = await c.get("/oauth/install")
        assert resp.status_code == 400
        assert "tenant" in resp.text.lower()
        assert len(main_module._oauth_states) == 0

    @pytest.mark.asyncio
    async def test_non_positive_tenant_id_rejected(self, client):
        async with client as c:
            resp = await c.get("/oauth/install", params={"tenant_id": 0})
        assert resp.status_code == 400
        assert len(main_module._oauth_states) == 0

    @pytest.mark.asyncio
    async def test_non_integer_tenant_id_rejected(self, client):
        async with client as c:
            resp = await c.get("/oauth/install", params={"tenant_id": "abc"})
        assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_valid_tenant_id_redirects_and_stores_state(self, client):
        async with client as c:
            resp = await c.get(
                "/oauth/install", params={"tenant_id": 42}, follow_redirects=False
            )
        assert resp.status_code == 302
        assert "slack.com/oauth/v2/authorize" in resp.headers["location"]
        assert len(main_module._oauth_states) == 1
        (state, (_, tenant_id)) = next(iter(main_module._oauth_states.items()))
        assert tenant_id == 42
        assert f"state={state}" in resp.headers["location"]


class TestOauthCallback:
    @pytest.mark.asyncio
    async def test_error_param_short_circuits(self, client):
        async with client as c:
            resp = await c.get("/oauth/callback", params={"error": "access_denied"})
        assert resp.status_code == 200
        assert "authorization failed" in resp.text.lower()

    @pytest.mark.asyncio
    async def test_invalid_or_expired_state_rejected(self, client):
        async with client as c:
            resp = await c.get(
                "/oauth/callback", params={"code": "abc", "state": "not-a-real-state"}
            )
        assert resp.status_code == 200
        assert "invalid or expired state" in resp.text.lower()

    @pytest.mark.asyncio
    async def test_valid_state_uses_its_own_tenant_id_not_hardcoded(self, client):
        # Seed a state as /oauth/install would, for tenant 77 (deliberately
        # NOT tenant 1, to prove the old hardcoded-1 default is gone).
        async with client as c:
            install_resp = await c.get(
                "/oauth/install", params={"tenant_id": 77}, follow_redirects=False
            )
            state = next(iter(main_module._oauth_states.keys()))

            fake_oauth_resp = {
                "team": {"id": "T123", "name": "Acme Corp"},
                "access_token": "xoxb-fake",
                "bot_user_id": "U999",
                "incoming_webhook": {"channel": "#security"},
            }
            with patch.object(
                main_module.AsyncWebClient,
                "oauth_v2_access",
                new=AsyncMock(return_value=fake_oauth_resp),
            ), patch.object(
                main_module.backend, "save_oauth_token", new=AsyncMock(return_value={})
            ) as save_mock:
                resp = await c.get(
                    "/oauth/callback", params={"code": "goodcode", "state": state}
                )

        assert resp.status_code == 200
        assert "installed" in resp.text.lower()
        save_mock.assert_awaited_once()
        _, kwargs = save_mock.call_args
        assert kwargs["tenant_id"] == 77
        assert kwargs["team_id"] == "T123"
        # state must be single-use
        assert state not in main_module._oauth_states

    @pytest.mark.asyncio
    async def test_no_state_direct_install_is_rejected_not_defaulted(self, client):
        before = _oauth_installs_value("rejected_no_tenant")
        async with client as c:
            with patch.object(
                main_module.backend, "save_oauth_token", new=AsyncMock(return_value={})
            ) as save_mock:
                resp = await c.get(
                    "/oauth/callback", params={"code": "goodcode", "state": ""}
                )

        assert resp.status_code == 200
        save_mock.assert_not_awaited()
        assert "tenant" in resp.text.lower()
        after = _oauth_installs_value("rejected_no_tenant")
        assert after == before + 1

    @pytest.mark.asyncio
    async def test_token_exchange_failure_still_handled(self, client):
        async with client as c:
            install_resp = await c.get(
                "/oauth/install", params={"tenant_id": 5}, follow_redirects=False
            )
            state = next(iter(main_module._oauth_states.keys()))

            with patch.object(
                main_module.AsyncWebClient,
                "oauth_v2_access",
                new=AsyncMock(side_effect=RuntimeError("boom")),
            ), patch.object(
                main_module.backend, "save_oauth_token", new=AsyncMock(return_value={})
            ) as save_mock:
                resp = await c.get(
                    "/oauth/callback", params={"code": "badcode", "state": state}
                )

        assert resp.status_code == 200
        assert "token exchange failed" in resp.text.lower()
        save_mock.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_backend_save_failure_returns_error_page(self, client):
        async with client as c:
            install_resp = await c.get(
                "/oauth/install", params={"tenant_id": 9}, follow_redirects=False
            )
            state = next(iter(main_module._oauth_states.keys()))

            fake_oauth_resp = {
                "team": {"id": "T1", "name": "Beta"},
                "access_token": "xoxb-fake",
                "bot_user_id": "U1",
                "incoming_webhook": {"channel": "#general"},
            }
            with patch.object(
                main_module.AsyncWebClient,
                "oauth_v2_access",
                new=AsyncMock(return_value=fake_oauth_resp),
            ), patch.object(
                main_module.backend,
                "save_oauth_token",
                new=AsyncMock(side_effect=RuntimeError("db down")),
            ):
                resp = await c.get(
                    "/oauth/callback", params={"code": "goodcode", "state": state}
                )

        assert resp.status_code == 200
        assert "config save failed" in resp.text.lower()
