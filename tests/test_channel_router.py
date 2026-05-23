from app.services.channel_router import ChannelRouter


class TestChannelRouter:
    def setup_method(self):
        self.router = ChannelRouter()

    def test_no_rules_returns_default(self):
        ch = self.router.resolve_channel("critical", None, "#alerts")
        assert ch == "#alerts"

    def test_severity_routes_to_configured_channel(self):
        rules = {"critical": "#sec-critical", "high": "#sec-high"}
        assert self.router.resolve_channel("critical", rules, "#alerts") == "#sec-critical"
        assert self.router.resolve_channel("high", rules, "#alerts") == "#sec-high"

    def test_unconfigured_severity_falls_back_to_default(self):
        rules = {"critical": "#sec-critical"}
        assert self.router.resolve_channel("low", rules, "#alerts") == "#alerts"

    def test_null_channel_suppresses_notification(self):
        rules = {"info": None}
        assert self.router.resolve_channel("info", rules, "#alerts") is None


class TestShouldNotify:
    def setup_method(self):
        self.router = ChannelRouter()

    def test_no_threshold_always_notifies(self):
        assert self.router.should_notify("info", None) is True

    def test_at_threshold_notifies(self):
        assert self.router.should_notify("high", "high") is True

    def test_above_threshold_notifies(self):
        assert self.router.should_notify("critical", "high") is True

    def test_below_threshold_suppressed(self):
        assert self.router.should_notify("low", "high") is False
