from __future__ import annotations

from pydantic import BaseModel


class Finding(BaseModel):
    id: str = ""
    title: str = ""
    severity: str = "info"
    description: str = ""
    status: str = "open"
    asset: str | None = None
    source: str | None = None
    category: str | None = None
    cve: str | None = None
    target: str | None = None
    engagement_id: str | None = None


class FindingsList(BaseModel):
    items: list[Finding] = []
    total: int = 0
    page: int = 1
    page_size: int = 50


class ScanTriggerResponse(BaseModel):
    scan_id: str = ""
    status: str = "queued"
    message: str = ""


class HealthSummary(BaseModel):
    services_total: int = 0
    services_healthy: int = 0
    open_findings: dict[str, int] = {}
    last_scan_at: str | None = None
    compliance_score: float | None = None


class SlackConfig(BaseModel):
    bot_token: str = ""
    default_channel: str = ""
    team_id: str | None = None
    team_name: str | None = None
    bot_user_id: str | None = None
    routing_rules: dict[str, str | None] | None = None
    severity_threshold: str | None = None
    tenant_id: int | None = None


class FindingNotification(BaseModel):
    finding_id: str
    title: str
    severity: str
    description: str = ""
    asset: str | None = None
    source: str | None = None
    category: str | None = None
    tenant_id: str | int = ""
