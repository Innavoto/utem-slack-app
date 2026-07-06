# G80 — utem-slack-app multi-tenant hardening (P2 #40)

## Scope decision (after verifying current repo state on origin/main)

The 2026-06-14 audit listed three items. Verified against `origin/main`
(`ff88b8a`, which already merged PR #2 "stop attributing every Slack install
to hardcoded tenant_id=1"):

1. **OAuth tenant hardcoded `tenant_id=1`** — **ALREADY FIXED on main.** Tenant
   identity now flows through the CSRF `state` created by `/oauth/install`
   (`?tenant_id=<n>` from the UTEM dashboard). `/oauth/callback` reads the
   tenant from the state; a stateless "direct install" is *rejected*, not
   attributed to a default tenant. `tests/test_oauth_install.py` already proves
   two distinct workspaces resolve to their own tenants (77, 42, 5, 9) with no
   hardcoded 1. **No fix opened for this item.**

2. **In-memory OAuth state + welcome-dedupe** — **STILL BROKEN. Fixed here.**
   - `app/main.py::_oauth_states` was a module-level `dict` → lost on restart,
     and **already collides across the 2 Uvicorn workers the Dockerfile runs
     (`--workers 2`)** even at replicas=1, so an OAuth round-trip that lands on
     a different worker than the one that minted the state fails intermittently.
   - `app/handlers/events.py::_WELCOMED_USERS` was a module-level `set` →
     re-welcomes every user on restart and dedupes per-process only.
   - Replaced both with a durable, Redis-backed `StateStore`
     (`app/services/state_store.py`), TTL'd (`SET … EX`, `GETDEL`, `SET NX`),
     keyed `slack:oauth:state:*` / `slack:welcomed:*`, pointed at the cluster
     Redis (`utem-redis-master.utem-system.svc.cluster.local:6379`) by default.
     Falls back to per-process memory (with a WARN + metric) only when
     `REDIS_URL` is unset or Redis is unreachable, so dev/tests still run.

3. **`/api/slack-install` returns 404 at ingress** — **out of this repo.**
   `/api/slack-install` is a Next.js route in `utem-ui-admin`
   (a separate repo, out of scope). *This* service's install route is
   `/oauth/install`, which is registered and returns non-404 (302 on a valid
   `tenant_id`, 400 on a missing one). A regression test asserts that here.

## checked by
- (a) two different Slack workspaces resolve to their correct distinct tenants
  (no shared tenant_id=1) — `test_oauth_install.py` (existing) + new store test.
- (b) OAuth state survives a simulated restart (persisted, not in-memory) —
  `test_state_store.py::test_oauth_state_survives_restart` (fresh StateStore +
  fresh client on the SAME fakeredis server reads a state written by the prior
  instance).
- (c) the install route is registered and returns a non-404 —
  `test_oauth_install.py::TestInstallRouteRegistered`.

## Evidence log

Baseline (origin/main): `pytest -q` → 29 passed, 0 pre-existing failures.

Changes:
- `app/services/state_store.py` (new) — Redis-backed `StateStore`
  (`SET … EX`, `GETDEL`, `SET NX`) with per-process in-memory fallback.
- `app/main.py` — dropped module `_oauth_states` dict + `_cleanup_stale_states`;
  `/oauth/install` → `await state_store.put_oauth_state`, `/oauth/callback` →
  `await state_store.pop_oauth_state`; close store on shutdown.
- `app/handlers/events.py` — dropped module `_WELCOMED_USERS` set; welcome now
  gated by `await state_store.mark_welcomed(f"{team_id}:{user_id}")`.
- `app/config.py` — `REDIS_URL` (default cluster Redis).
- `app/metrics.py` — `ctem_slack_app_state_store_ops_total{op,backend}`.
- `requirements.txt` — `redis>=5.0.0`, `fakeredis>=2.20.0`.
- tests: `tests/test_state_store.py` (new, 9), `tests/test_events_welcome.py`
  (new, 3), `tests/test_oauth_install.py` (+3 install-route tests, refactored
  off the removed dict), `tests/conftest.py` (`REDIS_URL=""` for memory path).

Local pre-check (CHECKER re-runs independently):
- `pytest -q` → **44 passed**.
- `pytest --cov` on changed code:
  `app/services/state_store.py` **93%**;
  `app/main.py` **73%** (misses are the untouched notification webhook);
  `app/handlers/events.py::handle_home_opened` fully covered (module figure
  low only from the pre-existing untouched `app_mention` handler).

checked-by results:
- (a) distinct tenants — `test_two_workspaces_keep_distinct_tenants` (42/99) +
  existing `test_valid_state_uses_its_own_tenant_id_not_hardcoded` (77). PASS.
- (b) survives restart — `test_oauth_state_survives_restart` (fresh store +
  fresh client, same fakeredis server, reads prior instance's state). PASS.
- (c) install route registered / non-404 — `TestInstallRouteRegistered`. PASS.

Residuals / notes:
- Item #1 (hardcoded tenant_id=1) was already fixed on main — no PR for it.
- Item #3's `/api/slack-install` is a utem-ui-admin (frontend) route, a
  separate repo out of this branch's scope; verified this service's own
  `/oauth/install` is registered and non-404.
- Grafana panel + alert for `state_store_ops{backend="memory"}` belong in
  utem-devops (separate repo) — follow-up; metric + structured WARN log shipped
  here.
- The utem-devops chart does not yet pass `REDIS_URL` env to the deployment;
  the code default points at the cluster Redis so it works without a chart
  change, but a chart env entry is a recommended hardening follow-up.
