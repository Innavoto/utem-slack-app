"""Tests for the OAuth install/callback flow in app/main.py.

Covers the G61 fix: /oauth/callback must never silently attribute a new
Slack workspace install to a hardcoded tenant id. Tenant identity must flow
through the CSRF `state` created by /oauth/install; installs that arrive
with no state (the Slack-Marketplace direct-install bypass) must be
rejected rather than mis-attributed.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch
from urllib.parse import parse_qs, urlparse

import pytest
from httpx import ASGITransport, AsyncClient

from app import main as main_module
from app import metrics

_STATE_PREFIX = "slack:oauth:state:"


def _oauth_installs_value(status: str) -> float:
    for metric in metrics.oauth_installs.collect():
        for sample in metric.samples:
            if sample.name.endswith("_total") and sample.labels.get("status") == status:
                return sample.value
    return 0.0


def _stored_states() -> dict[str, int]:
    """State-key -> tenant_id currently held by the (memory-fallback) store."""
    store = main_module.state_store
    return {
        k[len(_STATE_PREFIX):]: int(v)
        for k, (_exp, v) in store._mem.items()
        if k.startswith(_STATE_PREFIX)
    }


def _state_from_redirect(location: str) -> str:
    return parse_qs(urlparse(location).query)["state"][0]


@pytest.fixture
def client():
    transport = ASGITransport(app=main_module.app)
    return AsyncClient(transport=transport, base_url="http://test")


@pytest.fixture(autouse=True)
def _clear_oauth_states():
    main_module.state_store._mem.clear()
    yield
    main_module.state_store._mem.clear()


class TestOauthInstall:
    @pytest.mark.asyncio
    async def test_missing_tenant_id_rejected(self, client):
        async with client as c:
            resp = await c.get("/oauth/install")
        assert resp.status_code == 400
        assert "tenant" in resp.text.lower()
        assert len(_stored_states()) == 0

    @pytest.mark.asyncio
    async def test_non_positive_tenant_id_rejected(self, client):
        async with client as c:
            resp = await c.get("/oauth/install", params={"tenant_id": 0})
        assert resp.status_code == 400
        assert len(_stored_states()) == 0

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
        stored = _stored_states()
        assert len(stored) == 1
        state, tenant_id = next(iter(stored.items()))
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
            state = _state_from_redirect(install_resp.headers["location"])

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
        assert state not in _stored_states()

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
            state = _state_from_redirect(install_resp.headers["location"])

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
            state = _state_from_redirect(install_resp.headers["location"])

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


class TestInstallRouteRegistered:
    """checked-by (c): this service's install route is registered and does not
    404 at the app boundary. (`/api/slack-install` — cited in the audit — is a
    Next.js route that lives in utem-ui-admin, a separate repo; the route this
    service actually serves is `/oauth/install`.)"""

    def test_oauth_install_route_is_registered(self):
        paths = {route.path for route in main_module.app.routes}
        assert "/oauth/install" in paths
        assert "/oauth/callback" in paths

    @pytest.mark.asyncio
    async def test_install_route_does_not_404(self, client):
        # Missing tenant_id → 400 (a real, registered response), never 404.
        async with client as c:
            resp = await c.get("/oauth/install")
        assert resp.status_code != 404
        assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_install_route_redirects_with_valid_tenant(self, client):
        async with client as c:
            resp = await c.get(
                "/oauth/install", params={"tenant_id": 1234}, follow_redirects=False
            )
        assert resp.status_code == 302
        assert "slack.com/oauth/v2/authorize" in resp.headers["location"]
