from __future__ import annotations

SEVERITY_ORDER = ["critical", "high", "medium", "low", "info"]


class ChannelRouter:
    def resolve_channel(
        self,
        severity: str,
        routing_rules: dict[str, str | None] | None,
        default_channel: str,
    ) -> str | None:
        if not routing_rules:
            return default_channel
        channel = routing_rules.get(severity.lower())
        if channel is None and severity.lower() in routing_rules:
            return None
        return channel or default_channel

    def should_notify(self, severity: str, threshold: str | None) -> bool:
        if not threshold:
            return True
        sev = severity.lower()
        thr = threshold.lower()
        if sev not in SEVERITY_ORDER or thr not in SEVERITY_ORDER:
            return True
        return SEVERITY_ORDER.index(sev) <= SEVERITY_ORDER.index(thr)
