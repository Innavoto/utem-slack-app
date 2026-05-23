from __future__ import annotations

import re

import structlog
from slack_bolt.async_app import AsyncApp
from slack_sdk.web.async_client import AsyncWebClient

from app.services.backend_client import BackendClient
from app.services.block_kit import build_finding_card
from app import metrics

log = structlog.get_logger()

_ACK_RE = re.compile(r"^utem_ack_(.+)$")
_DISMISS_RE = re.compile(r"^utem_dismiss_(.+)$")
_ESCALATE_RE = re.compile(r"^utem_escalate_(.+)$")
_DETAIL_RE = re.compile(r"^utem_detail_(.+)$")


def register_interactions(app: AsyncApp, client: BackendClient):

    @app.action(_ACK_RE)
    async def handle_acknowledge(ack, action, body, say):
        await ack()
        finding_id = action["value"]
        team_id = body.get("team", {}).get("id", "")
        user_name = body.get("user", {}).get("username", "someone")
        metrics.interactions_total.labels(action_type="acknowledge").inc()

        config = await client.get_slack_config_by_team(team_id)
        if not config or not config.tenant_id:
            return

        try:
            await client.update_finding_status(
                config.tenant_id,
                finding_id,
                "acknowledged",
                user_note=f"Acknowledged via Slack by @{user_name}",
            )
            token = config.bot_token
            slack_client = AsyncWebClient(token=token)
            await slack_client.chat_update(
                channel=body["channel"]["id"],
                ts=body["message"]["ts"],
                blocks=_replace_actions_with_status(
                    body["message"]["blocks"],
                    f":white_check_mark: Acknowledged by @{user_name}",
                ),
                text=f"Finding {finding_id} acknowledged",
            )
        except Exception:
            log.exception("ack_failed", finding_id=finding_id)

    @app.action(_DISMISS_RE)
    async def handle_dismiss(ack, action, body, client_sdk):
        await ack()
        finding_id = action["value"]
        metrics.interactions_total.labels(action_type="dismiss").inc()

        await client_sdk.views_open(
            trigger_id=body["trigger_id"],
            view={
                "type": "modal",
                "callback_id": "utem_dismiss_modal",
                "private_metadata": f"{finding_id}|{body['channel']['id']}|{body['message']['ts']}",
                "title": {"type": "plain_text", "text": "Dismiss Finding"},
                "submit": {"type": "plain_text", "text": "Dismiss"},
                "blocks": [
                    {
                        "type": "input",
                        "block_id": "reason_block",
                        "element": {
                            "type": "static_select",
                            "action_id": "dismiss_reason",
                            "options": [
                                {"text": {"type": "plain_text", "text": "False positive"}, "value": "false_positive"},
                                {"text": {"type": "plain_text", "text": "Accepted risk"}, "value": "accepted_risk"},
                                {"text": {"type": "plain_text", "text": "Duplicate"}, "value": "duplicate"},
                                {"text": {"type": "plain_text", "text": "Other"}, "value": "other"},
                            ],
                        },
                        "label": {"type": "plain_text", "text": "Reason"},
                    },
                    {
                        "type": "input",
                        "block_id": "note_block",
                        "optional": True,
                        "element": {
                            "type": "plain_text_input",
                            "action_id": "dismiss_note",
                            "multiline": True,
                            "placeholder": {"type": "plain_text", "text": "Additional context..."},
                        },
                        "label": {"type": "plain_text", "text": "Note (optional)"},
                    },
                ],
            },
        )

    @app.view("utem_dismiss_modal")
    async def handle_dismiss_submit(ack, view, body):
        await ack()
        meta = view["private_metadata"].split("|")
        finding_id, channel_id, message_ts = meta[0], meta[1], meta[2]
        team_id = body.get("team", {}).get("id", "")
        user_name = body.get("user", {}).get("username", "someone")

        reason = view["state"]["values"]["reason_block"]["dismiss_reason"]["selected_option"]["value"]
        note = view["state"]["values"]["note_block"]["dismiss_note"].get("value", "")

        config = await client.get_slack_config_by_team(team_id)
        if not config or not config.tenant_id:
            return

        try:
            await client.update_finding_status(
                config.tenant_id,
                finding_id,
                "dismissed",
                user_note=f"Dismissed ({reason}) by @{user_name}: {note}".strip(),
            )
            slack_client = AsyncWebClient(token=config.bot_token)
            resp = await slack_client.conversations_history(
                channel=channel_id, latest=message_ts, inclusive=True, limit=1
            )
            if resp["messages"]:
                await slack_client.chat_update(
                    channel=channel_id,
                    ts=message_ts,
                    blocks=_replace_actions_with_status(
                        resp["messages"][0].get("blocks", []),
                        f":no_entry_sign: Dismissed by @{user_name} ({reason})",
                    ),
                    text=f"Finding {finding_id} dismissed",
                )
        except Exception:
            log.exception("dismiss_failed", finding_id=finding_id)

    @app.action(_ESCALATE_RE)
    async def handle_escalate(ack, action, body, client_sdk):
        await ack()
        finding_id = action["value"]
        team_id = body.get("team", {}).get("id", "")
        user_name = body.get("user", {}).get("username", "someone")
        metrics.interactions_total.labels(action_type="escalate").inc()

        config = await client.get_slack_config_by_team(team_id)
        if not config or not config.tenant_id:
            return

        try:
            finding = await client.get_finding(config.tenant_id, finding_id)
            card = build_finding_card(finding, with_actions=False)
            escalation_text = f":rotating_light: Escalated by @{user_name}"
            card.append({
                "type": "context",
                "elements": [{"type": "mrkdwn", "text": escalation_text}],
            })

            slack_client = AsyncWebClient(token=config.bot_token)
            await slack_client.chat_postMessage(
                channel=body["channel"]["id"],
                thread_ts=body["message"]["ts"],
                blocks=card,
                text=f"Escalated: {finding.title}",
            )
            await slack_client.chat_update(
                channel=body["channel"]["id"],
                ts=body["message"]["ts"],
                blocks=_replace_actions_with_status(
                    body["message"]["blocks"],
                    f":rotating_light: Escalated by @{user_name}",
                ),
                text=f"Finding {finding_id} escalated",
            )
        except Exception:
            log.exception("escalate_failed", finding_id=finding_id)

    @app.action(_DETAIL_RE)
    async def handle_detail(ack, action, body, respond):
        await ack()
        finding_id = action["value"]
        team_id = body.get("team", {}).get("id", "")

        config = await client.get_slack_config_by_team(team_id)
        if not config or not config.tenant_id:
            return

        try:
            finding = await client.get_finding(config.tenant_id, finding_id)
            card = build_finding_card(finding, with_actions=True)
            await respond(blocks=card, response_type="ephemeral")
        except Exception:
            log.exception("detail_failed", finding_id=finding_id)


def _replace_actions_with_status(blocks: list[dict], status_text: str) -> list[dict]:
    result = []
    for block in blocks:
        if block.get("type") == "actions":
            result.append({
                "type": "context",
                "elements": [{"type": "mrkdwn", "text": status_text}],
            })
        else:
            result.append(block)
    return result
