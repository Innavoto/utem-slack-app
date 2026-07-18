from __future__ import annotations

import hashlib
import hmac
import secrets
import time

import structlog
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from prometheus_client import generate_latest, CONTENT_TYPE_LATEST
from slack_bolt.adapter.fastapi.async_handler import AsyncSlackRequestHandler
from slack_bolt.async_app import AsyncApp
from slack_sdk.web.async_client import AsyncWebClient
from starlette.responses import Response

from app.config import settings
from app.handlers.commands import register_commands
from app.handlers.events import register_events
from app.handlers.interactions import register_interactions
from app.models.schemas import FindingNotification
from app.services.backend_client import BackendClient
from app.services.block_kit import build_finding_card
from app.services.channel_router import ChannelRouter
from app.services.state_store import StateStore
from app import metrics

log = structlog.get_logger()

# -- Slack Bolt app ----------------------------------------------------------

slack_app = AsyncApp(
    signing_secret=settings.SLACK_SIGNING_SECRET,
    token=None,
    process_before_response=True,
)

backend = BackendClient()
router = ChannelRouter()

# Durable OAuth-state + welcome-dedupe store (G80). Backed by the shared
# cluster Redis so state survives restarts and is consistent across the 2
# Uvicorn workers the Dockerfile runs (`--workers 2`) and across replicas.
state_store = StateStore(redis_url=settings.REDIS_URL)

register_commands(slack_app, backend)
register_interactions(slack_app, backend)
register_events(slack_app, backend, state_store)

slack_handler = AsyncSlackRequestHandler(slack_app)

# -- FastAPI app -------------------------------------------------------------

app = FastAPI(title="UTEM Slack App", version="1.0.0")


@app.get("/healthz")
async def healthz():
    return {"status": "ok"}


@app.get("/readyz")
async def readyz():
    return {"status": "ok"}


@app.get("/metrics")
async def prometheus_metrics():
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


# -- Slack event routes (proxied from utem-ui-admin) ----------------------

@app.post("/slack/events")
async def slack_events(request: Request):
    return await slack_handler.handle(request)


@app.post("/slack/commands")
async def slack_commands(request: Request):
    return await slack_handler.handle(request)


@app.post("/slack/interactions")
async def slack_interactions(request: Request):
    return await slack_handler.handle(request)


# -- OAuth 2.0 V2 install flow ----------------------------------------------

@app.get("/oauth/install")
async def oauth_install(tenant_id: str = Query(default="")):
    parsed_tenant_id = _parse_tenant_id(tenant_id)
    if parsed_tenant_id is None:
        log.warning("oauth_install_missing_tenant_id", tenant_id=tenant_id)
        return HTMLResponse(
            _error_page(
                "Missing or invalid tenant_id. Install UTEM for Slack from "
                "the UTEM dashboard's Extensions page so your account can be "
                "linked correctly."
            ),
            status_code=400,
        )
    tenant_id = parsed_tenant_id

    state = secrets.token_urlsafe(32)
    await state_store.put_oauth_state(state, tenant_id)

    scopes = (
        "chat:write,commands,app_mentions:read,"
        "channels:read,groups:read,im:read,mpim:read,users:read"
    )
    url = (
        f"https://slack.com/oauth/v2/authorize"
        f"?client_id={settings.SLACK_CLIENT_ID}"
        f"&scope={scopes}"
        f"&redirect_uri={settings.SLACK_OAUTH_REDIRECT_URI}"
        f"&state={state}"
    )
    return RedirectResponse(url, status_code=302)


@app.get("/oauth/callback")
async def oauth_callback(code: str = "", state: str = "", error: str = ""):
    if error:
        return HTMLResponse(_error_page(f"Slack authorization failed: {error}"))

    # Tenant resolution: the CSRF `state` created by /oauth/install carries
    # the installing tenant's id. A request with no state at all is the
    # Slack-Marketplace "direct install" bypass
    # (utem-ui-admin/app/api/slack-install/route.ts hits Slack's
    # oauth/v2/authorize directly, skipping /oauth/install) — there is no
    # way to safely know which tenant that install belongs to, so it MUST be
    # rejected rather than attributed to a guessed/default tenant (that was
    # previously hardcoded to tenant_id=1, causing every workspace's install
    # to read/write tenant 1's data — see G61).
    if state:
        tenant_id = await state_store.pop_oauth_state(state)
        if tenant_id is None:
            return HTMLResponse(_error_page("Invalid or expired state. Please try again."))
    else:
        log.warning("oauth_install_rejected_no_tenant")
        metrics.oauth_installs.labels(status="rejected_no_tenant").inc()
        return HTMLResponse(
            _error_page(
                "Could not determine which UTEM tenant this install belongs "
                "to. Please install UTEM for Slack from the UTEM dashboard's "
                "Extensions page (Settings → Integrations → Slack) "
                "instead of Slack's App Directory, so your account can be "
                "linked correctly."
            )
        )

    try:
        sdk = AsyncWebClient()
        resp = await sdk.oauth_v2_access(
            client_id=settings.SLACK_CLIENT_ID,
            client_secret=settings.SLACK_CLIENT_SECRET,
            code=code,
            redirect_uri=settings.SLACK_OAUTH_REDIRECT_URI,
        )
    except Exception as exc:
        log.exception("oauth_exchange_failed")
        metrics.oauth_installs.labels(status="failed").inc()
        return HTMLResponse(_error_page(f"Token exchange failed: {exc}"))

    team_id = resp.get("team", {}).get("id", "")
    team_name = resp.get("team", {}).get("name", "")
    bot_token = resp.get("access_token", "")
    bot_user_id = resp.get("bot_user_id", "")
    channel = resp.get("incoming_webhook", {}).get("channel", "#general")

    try:
        await backend.save_oauth_token(
            tenant_id=tenant_id,
            team_id=team_id,
            team_name=team_name,
            bot_token=bot_token,
            bot_user_id=bot_user_id,
            default_channel=channel,
        )
        metrics.oauth_installs.labels(status="success").inc()
    except Exception:
        log.exception("oauth_save_failed")
        metrics.oauth_installs.labels(status="failed").inc()
        return HTMLResponse(_error_page("Installation succeeded but config save failed. Contact support."))

    return HTMLResponse(
        f"""<!DOCTYPE html>
<html><head><title>UTEM Installed</title>
<style>body{{font-family:sans-serif;max-width:600px;margin:80px auto;text-align:center}}
h1{{color:#1a1a2e}}a{{color:#4682b4}}</style></head>
<body>
<h1>UTEM has been installed!</h1>
<p>Workspace: <strong>{team_name}</strong></p>
<p>Try <code>/utem status</code> in any channel to get started.</p>
<p><a href="https://utem.innavoto.com">Return to UTEM Dashboard</a></p>
</body></html>"""
    )


# -- Internal notification webhook (utem-platform-backend pushes here) ----------------

@app.post("/api/v1/notify")
async def receive_notification(request: Request, payload: FindingNotification):
    token = request.headers.get("X-Internal-Service-Token", "")
    if not hmac.compare_digest(token, settings.UTEM_INTERNAL_TOKEN):
        raise HTTPException(status_code=401, detail="unauthorized")

    start = time.monotonic()

    config = await backend.get_slack_config(payload.tenant_id)
    if not config or not config.bot_token:
        metrics.notifications_sent.labels(
            severity=payload.severity, status="skipped"
        ).inc()
        return {"ok": False, "reason": "no_slack_config"}

    if not router.should_notify(payload.severity, config.severity_threshold):
        metrics.notifications_sent.labels(
            severity=payload.severity, status="skipped"
        ).inc()
        return {"ok": False, "reason": "below_threshold"}

    channel = router.resolve_channel(
        payload.severity, config.routing_rules, config.default_channel
    )
    if not channel:
        metrics.notifications_sent.labels(
            severity=payload.severity, status="skipped"
        ).inc()
        return {"ok": False, "reason": "channel_suppressed"}

    from app.models.schemas import Finding
    finding = Finding(
        id=payload.finding_id,
        title=payload.title,
        severity=payload.severity,
        description=payload.description,
        asset=payload.asset,
        source=payload.source,
        category=payload.category,
    )
    blocks = build_finding_card(finding, with_actions=True)

    try:
        slack_client = AsyncWebClient(token=config.bot_token)
        resp = await slack_client.chat_postMessage(
            channel=channel,
            blocks=blocks,
            text=f"{payload.severity.upper()}: {payload.title}",
        )
        metrics.notifications_sent.labels(
            severity=payload.severity, status="success"
        ).inc()
        metrics.notification_duration.observe(time.monotonic() - start)
        return {"ok": True, "message_ts": resp.get("ts")}
    except Exception as exc:
        log.exception("notification_send_failed")
        metrics.notifications_sent.labels(
            severity=payload.severity, status="failed"
        ).inc()
        return {"ok": False, "error": str(exc)}


# -- helpers -----------------------------------------------------------------

def _parse_tenant_id(raw: str) -> int | None:
    """Validate a tenant_id query param. Returns None if missing/invalid."""
    if not raw:
        return None
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


def _error_page(message: str) -> str:
    return f"""<!DOCTYPE html>
<html><head><title>UTEM — Error</title>
<style>body{{font-family:sans-serif;max-width:600px;margin:80px auto;text-align:center}}
h1{{color:#b22222}}a{{color:#4682b4}}</style></head>
<body>
<h1>Installation Error</h1>
<p>{message}</p>
<p><a href="/oauth/install">Try again</a></p>
</body></html>"""


from app.core.tracing import setup_tracing
setup_tracing(app, service_name="utem-slack-app")


@app.on_event("startup")
async def startup():
    log.info("utem_slack_app_starting", port=settings.APP_PORT)


@app.on_event("shutdown")
async def shutdown():
    await backend.close()
    await state_store.close()
    log.info("utem_slack_app_stopped")
