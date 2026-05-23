from __future__ import annotations

from app.models.schemas import Finding, FindingsList, HealthSummary, ScanTriggerResponse

SEVERITY_EMOJI = {
    "critical": "\U0001f6a8",  # 🚨
    "high": "⚠️",    # ⚠️
    "medium": "\U0001f7e1",    # 🟡
    "low": "ℹ️",     # ℹ️
    "info": "\U0001f4dd",      # 📝
}

SEVERITY_COLOR = {
    "critical": "#b22222",
    "high": "#d2691e",
    "medium": "#daa520",
    "low": "#4682b4",
    "info": "#808080",
}


def build_finding_card(finding: Finding, *, with_actions: bool = True) -> list[dict]:
    emoji = SEVERITY_EMOJI.get(finding.severity, "ℹ️")
    blocks: list[dict] = [
        {
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": f"{emoji} {finding.title}"[:150],
            },
        },
    ]

    if finding.description:
        blocks.append({
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": finding.description[:3000],
            },
        })

    fields = [
        {"type": "mrkdwn", "text": f"*Severity:*\n{finding.severity.upper()}"},
        {"type": "mrkdwn", "text": f"*Status:*\n{finding.status}"},
    ]
    if finding.source:
        fields.append({"type": "mrkdwn", "text": f"*Source:*\n{finding.source}"})
    if finding.asset:
        fields.append({"type": "mrkdwn", "text": f"*Asset:*\n{finding.asset}"})
    if finding.cve:
        fields.append({"type": "mrkdwn", "text": f"*CVE:*\n{finding.cve}"})
    if finding.category:
        fields.append({"type": "mrkdwn", "text": f"*Category:*\n{finding.category}"})

    blocks.append({"type": "section", "fields": fields[:10]})

    if with_actions:
        blocks.append({
            "type": "actions",
            "elements": [
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "Acknowledge"},
                    "style": "primary",
                    "action_id": f"utem_ack_{finding.id}",
                    "value": finding.id,
                },
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "Dismiss"},
                    "style": "danger",
                    "action_id": f"utem_dismiss_{finding.id}",
                    "value": finding.id,
                },
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "Escalate"},
                    "action_id": f"utem_escalate_{finding.id}",
                    "value": finding.id,
                },
            ],
        })

    blocks.append({"type": "divider"})
    blocks.append({
        "type": "context",
        "elements": [
            {"type": "mrkdwn", "text": f"UTEM Platform | Finding `{finding.id}`"},
        ],
    })
    return blocks


def build_findings_list(data: FindingsList) -> list[dict]:
    if not data.items:
        return [{
            "type": "section",
            "text": {"type": "mrkdwn", "text": ":white_check_mark: No open findings."},
        }]

    blocks: list[dict] = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": f"Open Findings ({data.total} total)"},
        },
    ]

    for f in data.items:
        emoji = SEVERITY_EMOJI.get(f.severity, "ℹ️")
        line = f"{emoji} *{f.severity.upper()}* — {f.title}"
        if f.asset:
            line += f" (`{f.asset}`)"
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": line},
            "accessory": {
                "type": "button",
                "text": {"type": "plain_text", "text": "Details"},
                "action_id": f"utem_detail_{f.id}",
                "value": f.id,
            },
        })

    if data.total > len(data.items):
        blocks.append({
            "type": "context",
            "elements": [{
                "type": "mrkdwn",
                "text": f"Showing {len(data.items)} of {data.total}. "
                        f"Use `/utem findings --limit N` for more.",
            }],
        })
    return blocks


def build_scan_result(scan: ScanTriggerResponse) -> list[dict]:
    return [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": (
                    f":rocket: *Scan triggered*\n"
                    f"Scan ID: `{scan.scan_id}`\n"
                    f"Status: {scan.status}\n"
                    f"{scan.message}"
                ),
            },
        },
    ]


def build_health_summary(health: HealthSummary) -> list[dict]:
    svc_status = (
        f":white_check_mark: {health.services_healthy}/{health.services_total} services healthy"
        if health.services_healthy == health.services_total
        else f":warning: {health.services_healthy}/{health.services_total} services healthy"
    )

    findings_lines = []
    for sev in ("critical", "high", "medium", "low", "info"):
        count = health.open_findings.get(sev, 0)
        if count:
            emoji = SEVERITY_EMOJI.get(sev, "")
            findings_lines.append(f"{emoji} {sev.capitalize()}: {count}")
    findings_text = "\n".join(findings_lines) if findings_lines else "No open findings"

    blocks: list[dict] = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": "UTEM Platform Status"},
        },
        {
            "type": "section",
            "fields": [
                {"type": "mrkdwn", "text": f"*Services:*\n{svc_status}"},
                {"type": "mrkdwn", "text": f"*Last scan:*\n{health.last_scan_at or 'N/A'}"},
            ],
        },
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"*Open findings:*\n{findings_text}"},
        },
    ]
    if health.compliance_score is not None:
        blocks.append({
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"*Compliance score:* {health.compliance_score:.0f}%",
            },
        })
    return blocks


def build_error(message: str) -> list[dict]:
    return [{
        "type": "section",
        "text": {"type": "mrkdwn", "text": f":x: {message}"},
    }]


def build_help() -> list[dict]:
    return [{
        "type": "section",
        "text": {
            "type": "mrkdwn",
            "text": (
                "*UTEM Slash Commands*\n\n"
                "`/utem findings [critical|high|medium|low]` — List open findings\n"
                "`/utem scan [target]` — Trigger a security scan\n"
                "`/utem status` — Platform health summary\n"
                "`/utem help` — Show this message"
            ),
        },
    }]
