from app.models.schemas import Finding, FindingsList, HealthSummary, ScanTriggerResponse
from app.services.block_kit import (
    build_error,
    build_finding_card,
    build_findings_list,
    build_health_summary,
    build_help,
    build_scan_result,
)


def _make_finding(**overrides):
    defaults = {
        "id": "F-001",
        "title": "SQL Injection in /api/login",
        "severity": "critical",
        "description": "Parameterized query not used",
        "status": "open",
        "asset": "api.example.com",
        "source": "burp",
        "cve": "CVE-2024-1234",
        "category": "injection",
    }
    defaults.update(overrides)
    return Finding(**defaults)


class TestBuildFindingCard:
    def test_has_header_section_actions_context(self):
        blocks = build_finding_card(_make_finding())
        types = [b["type"] for b in blocks]
        assert "header" in types
        assert "actions" in types
        assert "context" in types

    def test_action_ids_contain_finding_id(self):
        blocks = build_finding_card(_make_finding(id="F-999"))
        actions = [b for b in blocks if b["type"] == "actions"][0]
        ids = [e["action_id"] for e in actions["elements"]]
        assert "utem_ack_F-999" in ids
        assert "utem_dismiss_F-999" in ids
        assert "utem_escalate_F-999" in ids

    def test_without_actions(self):
        blocks = build_finding_card(_make_finding(), with_actions=False)
        types = [b["type"] for b in blocks]
        assert "actions" not in types

    def test_truncates_long_title(self):
        blocks = build_finding_card(_make_finding(title="X" * 200))
        header = blocks[0]["text"]["text"]
        assert len(header) <= 150


class TestBuildFindingsList:
    def test_empty_list(self):
        blocks = build_findings_list(FindingsList())
        assert "No open findings" in blocks[0]["text"]["text"]

    def test_shows_count(self):
        data = FindingsList(
            items=[_make_finding(id=f"F-{i}") for i in range(3)],
            total=10,
        )
        blocks = build_findings_list(data)
        header = blocks[0]["text"]["text"]
        assert "10" in header


class TestBuildScanResult:
    def test_contains_scan_id(self):
        blocks = build_scan_result(
            ScanTriggerResponse(scan_id="SCAN-001", status="queued", message="Started")
        )
        assert "SCAN-001" in blocks[0]["text"]["text"]


class TestBuildHealthSummary:
    def test_all_healthy(self):
        blocks = build_health_summary(
            HealthSummary(services_total=5, services_healthy=5)
        )
        text = str(blocks)
        assert "5/5" in text

    def test_findings_by_severity(self):
        blocks = build_health_summary(
            HealthSummary(
                services_total=5,
                services_healthy=5,
                open_findings={"critical": 3, "high": 7},
            )
        )
        text = str(blocks)
        assert "Critical: 3" in text
        assert "High: 7" in text


class TestBuildError:
    def test_contains_message(self):
        blocks = build_error("Something broke")
        assert "Something broke" in blocks[0]["text"]["text"]


class TestBuildHelp:
    def test_lists_commands(self):
        blocks = build_help()
        text = blocks[0]["text"]["text"]
        assert "/utem findings" in text
        assert "/utem scan" in text
        assert "/utem status" in text
