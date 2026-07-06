import os

os.environ.setdefault("SLACK_SIGNING_SECRET", "test-signing-secret")
os.environ.setdefault("SLACK_CLIENT_ID", "test-client-id")
os.environ.setdefault("SLACK_CLIENT_SECRET", "test-client-secret")
os.environ.setdefault("UTEM_INTERNAL_TOKEN", "test-internal-token")
os.environ.setdefault("UTEM_BACKEND_URL", "http://localhost:8000")
# Force the StateStore's in-memory fallback in unit tests (no live Redis).
# The Redis-backed durability path is proven separately in test_state_store.py
# with fakeredis. Setting this to "" avoids per-call connection timeouts.
os.environ["REDIS_URL"] = ""
