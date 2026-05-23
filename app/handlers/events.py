from __future__ import annotations

import re

import structlog
from slack_bolt.async_app import AsyncApp

from app.services.backend_client import BackendClient
from app.services.block_kit import (
    build_error,
    build_finding_card,
    build_health_summary,
    build_help,
)

log = structlog.get_logger()

_CVE_RE = re.compile(r"CVE-\d{4}-\d{4,}", re.IGNORECASE)


_WELCOMED_USERS: set[str] = set()


def register_events(app: AsyncApp, client: BackendClient):

    @app.event("app_home_opened")
    async def handle_home_opened(event, client_sdk):
        user_id = event.get("user", "")
        tab = event.get("tab", "")
        if tab != "messages" or user_id in _WELCOMED_USERS:
            return
        _WELCOMED_USERS.add(user_id)
        try:
            await client_sdk.chat_postMessage(
                channel=user_id,
                blocks=[
                    {
                        "type": "header",
                        "text": {"type": "plain_text", "text": "Welcome to UTEM Security"},
                    },
                    {
                        "type": "section",
                        "text": {
                            "type": "mrkdwn",
                            "text": (
                                "Hi there! :wave: I'm the UTEM Security bot. "
                                "Here's what I can do:\n\n"
                                ":mag: `/utem findings` — List open security findings\n"
                                ":rocket: `/utem scan` — Trigger a security scan\n"
                                ":bar_chart: `/utem status` — Platform health summary\n"
                                ":question: `/utem help` — Show all commands\n\n"
                                "You can also mention me in any channel — "
                                "try `@UTEM Security status` or `@UTEM Security CVE-2024-1234`."
                            ),
                        },
                    },
                    {
                        "type": "context",
                        "elements": [
                            {
                                "type": "mrkdwn",
                                "text": "Powered by UTEM — Unified Threat Exposure Management by Innavoto India Pvt Ltd",
                            }
                        ],
                    },
                ],
                text="Welcome to UTEM Security",
            )
        except Exception:
            log.exception("welcome_message_failed", user_id=user_id)

    @app.event("app_mention")
    async def handle_mention(event, say):
        text = event.get("text", "")
        team_id = event.get("team", "")
        thread_ts = event.get("ts")

        config = await client.get_slack_config_by_team(team_id)
        if not config or not config.tenant_id:
            await say(
                blocks=build_error("UTEM is not configured for this workspace."),
                thread_ts=thread_ts,
            )
            return

        tenant_id = config.tenant_id

        # strip the @mention itself
        cleaned = re.sub(r"<@[A-Z0-9]+>", "", text).strip().lower()

        try:
            cve_match = _CVE_RE.search(cleaned)
            if cve_match:
                await _handle_cve_query(say, client, tenant_id, cve_match.group(), thread_ts)
            elif "status" in cleaned:
                health = await client.get_health(tenant_id)
                await say(blocks=build_health_summary(health), thread_ts=thread_ts)
            elif cleaned.startswith("finding"):
                fid = cleaned.replace("finding", "").strip()
                if fid:
                    await _handle_finding_query(say, client, tenant_id, fid, thread_ts)
                else:
                    await say(
                        blocks=build_error("Usage: `@UTEM finding <ID>`"),
                        thread_ts=thread_ts,
                    )
            elif "help" in cleaned or not cleaned:
                await say(blocks=build_help(), thread_ts=thread_ts)
            else:
                await say(blocks=build_help(), thread_ts=thread_ts)
        except Exception:
            log.exception("mention_error", text=cleaned)
            await say(
                blocks=build_error("Something went wrong processing your query."),
                thread_ts=thread_ts,
            )


async def _handle_cve_query(say, client: BackendClient, tenant_id, cve: str, thread_ts: str):
    data = await client.list_findings(tenant_id, page_size=5)
    matches = [f for f in data.items if f.cve and cve.upper() in f.cve.upper()]
    if matches:
        for f in matches[:3]:
            await say(
                blocks=build_finding_card(f, with_actions=True),
                thread_ts=thread_ts,
            )
    else:
        await say(
            text=f":white_check_mark: No open findings for {cve}.",
            thread_ts=thread_ts,
        )


async def _handle_finding_query(say, client: BackendClient, tenant_id, finding_id: str, thread_ts: str):
    try:
        finding = await client.get_finding(tenant_id, finding_id)
        await say(
            blocks=build_finding_card(finding, with_actions=True),
            thread_ts=thread_ts,
        )
    except Exception:
        await say(
            blocks=build_error(f"Finding `{finding_id}` not found."),
            thread_ts=thread_ts,
        )
