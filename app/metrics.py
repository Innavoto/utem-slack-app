from prometheus_client import Counter, Gauge, Histogram

commands_total = Counter(
    "ctem_slack_app_commands_total",
    "Slash commands received",
    ["subcommand"],
)
command_duration = Histogram(
    "ctem_slack_app_command_duration_seconds",
    "Slash command processing time",
    ["subcommand"],
)
interactions_total = Counter(
    "ctem_slack_app_interactions_total",
    "Interactive actions received",
    ["action_type"],
)
notifications_sent = Counter(
    "ctem_slack_app_notifications_sent_total",
    "Notifications sent to Slack",
    ["severity", "status"],
)
notification_duration = Histogram(
    "ctem_slack_app_notification_duration_seconds",
    "Notification delivery time",
)
oauth_installs = Counter(
    "ctem_slack_app_oauth_installs_total",
    "OAuth install completions",
    ["status"],
)
backend_requests = Counter(
    "ctem_slack_app_backend_requests_total",
    "Requests to utem-platform-backend",
    ["endpoint", "status"],
)
active_workspaces = Gauge(
    "ctem_slack_app_active_workspaces",
    "Slack workspaces with active install",
)
