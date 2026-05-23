from __future__ import annotations

import time

import structlog

from slack_bolt.async_app import AsyncApp

from app.services.backend_client import BackendClient
from app.services.block_kit import (
    build_error,
    build_findings_list,
    build_health_summary,
    build_help,
    build_scan_result,
)
from app import metrics

log = structlog.get_logger()


def register_commands(app: AsyncApp, client: BackendClient):

    @app.command("/utem")
    async def handle_utem(ack, command, respond):
        await ack()

        text = (command.get("text") or "").strip()
        parts = text.split()
        subcommand = parts[0].lower() if parts else "help"
        args = parts[1:]

        start = time.monotonic()
        metrics.commands_total.labels(subcommand=subcommand).inc()

        team_id = command.get("team_id", "")
        config = await client.get_slack_config_by_team(team_id)
        if not config or not config.tenant_id:
            await respond(
                blocks=build_error(
                    "UTEM is not configured for this workspace. "
                    "Ask your admin to install via the UTEM Extensions page."
                ),
                response_type="ephemeral",
            )
            return

        tenant_id = config.tenant_id

        try:
            if subcommand == "findings":
                await _handle_findings(respond, client, tenant_id, args)
            elif subcommand == "scan":
                await _handle_scan(respond, client, tenant_id, args)
            elif subcommand == "status":
                await _handle_status(respond, client, tenant_id)
            elif subcommand == "help":
                await respond(blocks=build_help(), response_type="ephemeral")
            else:
                await respond(blocks=build_help(), response_type="ephemeral")
        except Exception:
            log.exception("command_error", subcommand=subcommand)
            await respond(
                blocks=build_error("Something went wrong. Try again later."),
                response_type="ephemeral",
            )
        finally:
            metrics.command_duration.labels(subcommand=subcommand).observe(
                time.monotonic() - start
            )


async def _handle_findings(respond, client: BackendClient, tenant_id, args: list[str]):
    severity = None
    limit = 5
    for i, arg in enumerate(args):
        if arg.startswith("--limit") and i + 1 < len(args):
            limit = min(int(args[i + 1]), 20)
        elif arg.lower() in ("critical", "high", "medium", "low", "info"):
            severity = arg.lower()

    data = await client.list_findings(
        tenant_id, severity=severity, page_size=limit
    )
    await respond(blocks=build_findings_list(data), response_type="ephemeral")


async def _handle_scan(respond, client: BackendClient, tenant_id, args: list[str]):
    target_id = args[0] if args else None
    result = await client.trigger_scan(tenant_id, target_id=target_id)
    await respond(blocks=build_scan_result(result), response_type="in_channel")


async def _handle_status(respond, client: BackendClient, tenant_id):
    health = await client.get_health(tenant_id)
    await respond(blocks=build_health_summary(health), response_type="ephemeral")
