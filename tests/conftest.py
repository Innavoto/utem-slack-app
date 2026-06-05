import os

os.environ.setdefault("SLACK_SIGNING_SECRET", "test-signing-secret")
os.environ.setdefault("SLACK_CLIENT_ID", "test-client-id")
os.environ.setdefault("SLACK_CLIENT_SECRET", "test-client-secret")
os.environ.setdefault("UTEM_INTERNAL_TOKEN", "test-internal-token")
os.environ.setdefault("UTEM_BACKEND_URL", "http://localhost:8000")
