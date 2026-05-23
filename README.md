# UTEM Slack App

> Interactive Slack App for the UTEM security platform.
> Slash commands, finding cards with action buttons, per-channel severity routing.

Built on [Slack Bolt for Python](https://slack.dev/bolt-python/) + FastAPI.

---

## Features

| Feature | Description |
|---------|-------------|
| `/utem findings [severity]` | List open findings, optionally filtered by severity |
| `/utem scan [target]` | Trigger a security scan |
| `/utem status` | Platform health summary |
| `/utem help` | Show available commands |
| **Finding cards** | Interactive Block Kit cards with Acknowledge / Dismiss / Escalate buttons |
| **@UTEM mentions** | Query findings, CVEs, or status by mentioning the bot |
| **Per-channel routing** | Route different severity levels to different channels |
| **OAuth V2 install** | "Add to Slack" button for customer self-service |

---

## Install (for customers)

1. Visit the UTEM Extensions page → UTEM Slack App → **Add to Slack**
2. Authorize UTEM to access your workspace
3. Configure severity routing in Settings → Integrations → Slack Bot
4. Try `/utem status` in any channel

---

## Architecture

```
Slack API ←→ ctem-admin-react (proxy) ←→ utem-slack-app:8098 ←→ ctem-backend:8000
```

- OAuth callback, slash commands, interactions, and events are proxied through
  `https://utem.innavoto.com/api/proxy/slack-app/*`
- utem-slack-app is stateless — all data lives in ctem-backend
- Bot tokens stored encrypted in `slack_bot_integrations` table

---

## Development

```bash
pip install -r requirements.txt

# Set env vars
export SLACK_CLIENT_ID=...
export SLACK_CLIENT_SECRET=...
export SLACK_SIGNING_SECRET=...
export CTEM_BACKEND_URL=http://localhost:8000
export CTEM_INTERNAL_TOKEN=...

# Run
uvicorn app.main:app --port 8098 --reload

# Tests
pytest tests/ -v --cov=app --cov-report=term-missing
```

---

## Slack App Setup

Import `slack-app-manifest.yaml` at https://api.slack.com/apps to create the app.
The manifest configures scopes, slash commands, event subscriptions, and interactivity URLs.

---

## References

- [Slack Bolt for Python](https://slack.dev/bolt-python/)
- [Slack Block Kit Builder](https://app.slack.com/block-kit-builder)
- [Slack API Methods](https://api.slack.com/methods)
- [Slack App Manifest](https://api.slack.com/reference/manifests)
- [Slack App Directory](https://slack.com/apps)

**Publisher:** Innavoto India Pvt Ltd | **License:** Apache 2.0
