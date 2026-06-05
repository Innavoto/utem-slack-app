from __future__ import annotations

import structlog
import httpx

from app.config import settings
from app.models.schemas import (
    Finding,
    FindingsList,
    HealthSummary,
    ScanTriggerResponse,
    SlackConfig,
)
from app import metrics

log = structlog.get_logger()

_MAX_RETRIES = 3
_BACKOFF_MS = [200, 400, 800]


class BackendClient:
    def __init__(self, base_url: str | None = None, token: str | None = None):
        self._base = (base_url or settings.UTEM_BACKEND_URL).rstrip("/")
        self._token = token or settings.UTEM_INTERNAL_TOKEN
        self._client = httpx.AsyncClient(
            base_url=self._base,
            timeout=30.0,
            headers={
                "X-Internal-Service-Token": self._token,
                "Accept": "application/json",
                "User-Agent": "utem-slack-app/1.0.0",
            },
        )

    async def list_findings(
        self,
        tenant_id: str | int,
        severity: str | None = None,
        status: str = "open",
        page: int = 1,
        page_size: int = 5,
    ) -> FindingsList:
        params: dict = {
            "status": status,
            "page": page,
            "page_size": page_size,
        }
        if severity:
            params["severity"] = severity
        data = await self._get(
            "/api/v1/findings", params=params, tenant_id=tenant_id
        )
        return FindingsList.model_validate(data)

    async def get_finding(
        self, tenant_id: str | int, finding_id: str
    ) -> Finding:
        data = await self._get(
            f"/api/v1/findings/{finding_id}", tenant_id=tenant_id
        )
        return Finding.model_validate(data)

    async def update_finding_status(
        self,
        tenant_id: str | int,
        finding_id: str,
        status: str,
        user_note: str | None = None,
    ) -> dict:
        body: dict = {"status": status}
        if user_note:
            body["note"] = user_note
        return await self._patch(
            f"/api/v1/findings/{finding_id}/status",
            json=body,
            tenant_id=tenant_id,
        )

    async def trigger_scan(
        self, tenant_id: str | int, target_id: str | None = None
    ) -> ScanTriggerResponse:
        body: dict = {}
        if target_id:
            body["target_id"] = target_id
        data = await self._post(
            "/api/v1/scans/trigger", json=body, tenant_id=tenant_id
        )
        return ScanTriggerResponse.model_validate(data)

    async def get_health(self, tenant_id: str | int) -> HealthSummary:
        data = await self._get(
            "/api/v1/health/summary", tenant_id=tenant_id
        )
        return HealthSummary.model_validate(data)

    async def get_slack_config(self, tenant_id: str | int) -> SlackConfig | None:
        try:
            data = await self._get(
                "/api/v1/integrations/slack_bot/config",
                tenant_id=tenant_id,
            )
            return SlackConfig.model_validate(data)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                return None
            raise

    async def get_slack_config_by_team(self, team_id: str) -> SlackConfig | None:
        try:
            data = await self._get(
                f"/api/v1/integrations/slack_bot/by-team/{team_id}"
            )
            return SlackConfig.model_validate(data)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                return None
            raise

    async def save_oauth_token(
        self,
        tenant_id: str | int,
        team_id: str,
        team_name: str,
        bot_token: str,
        bot_user_id: str,
        default_channel: str,
    ) -> dict:
        return await self._post(
            "/api/v1/integrations/slack_bot/config",
            json={
                "bot_token": bot_token,
                "default_channel": default_channel,
                "team_id": team_id,
                "team_name": team_name,
                "bot_user_id": bot_user_id,
                "installed_via_oauth": True,
            },
            tenant_id=tenant_id,
        )

    # -- transport helpers ---------------------------------------------------

    async def _get(
        self,
        path: str,
        params: dict | None = None,
        tenant_id: str | int | None = None,
    ) -> dict:
        return await self._request("GET", path, params=params, tenant_id=tenant_id)

    async def _post(
        self,
        path: str,
        json: dict | None = None,
        tenant_id: str | int | None = None,
    ) -> dict:
        return await self._request("POST", path, json=json, tenant_id=tenant_id)

    async def _patch(
        self,
        path: str,
        json: dict | None = None,
        tenant_id: str | int | None = None,
    ) -> dict:
        return await self._request("PATCH", path, json=json, tenant_id=tenant_id)

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict | None = None,
        json: dict | None = None,
        tenant_id: str | int | None = None,
    ) -> dict:
        headers = {}
        if tenant_id is not None:
            headers["X-Tenant-Id"] = str(tenant_id)

        last_exc: Exception | None = None
        for attempt in range(_MAX_RETRIES):
            try:
                resp = await self._client.request(
                    method, path, params=params, json=json, headers=headers
                )
                label = "ok" if resp.is_success else str(resp.status_code)
                metrics.backend_requests.labels(
                    endpoint=path.split("?")[0], status=label
                ).inc()
                resp.raise_for_status()
                return resp.json()
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code < 500:
                    metrics.backend_requests.labels(
                        endpoint=path.split("?")[0],
                        status=str(exc.response.status_code),
                    ).inc()
                    raise
                last_exc = exc
            except httpx.HTTPError as exc:
                last_exc = exc

            if attempt < _MAX_RETRIES - 1:
                import asyncio
                await asyncio.sleep(_BACKOFF_MS[attempt] / 1000)
                log.warning("backend_retry", path=path, attempt=attempt + 1)

        metrics.backend_requests.labels(
            endpoint=path.split("?")[0], status="retry_exhausted"
        ).inc()
        raise last_exc or httpx.HTTPError("backend unreachable")

    async def close(self):
        await self._client.aclose()
