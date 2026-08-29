# Local-only: the panel is the scheduler, GitHub Actions is gone

Check-ins run in the panel process on a 30-minute loop (`panel/scheduler.py`, on unless `PANEL_SCHEDULER=0`) which runs each enabled account once per local day until it succeeds. The GitHub integration — `panel/github.py`, secret sync, `workflow_dispatch`, run history, the `Draft/Verified/Active/Removed` lifecycle that existed to gate secret writes — was deleted. This was chosen because the owner's Actions quota ran out and because the protocol path (ADR-0007) is cheap enough to run on the owner's own machine: no runner minutes, no PAT, no credentials mirrored outside the sandboxed folder.

**Consequences**:
- Check-ins only happen while the panel is running. Missing a day is a missed bonus, not a broken account — the next run is not a retry of yesterday, it is today's.
- `data/panel.db` is now the only copy of the credentials. Back it up (ADR-0003).
- The `GH_PAT` that was hardcoded as a default in `run.py` is no longer read by anything, but it remains in git history and must be revoked at GitHub.
- ADR-0001 (panel executes real check-ins locally) and ADR-0005 (test is the real check-in) survive this: there is still exactly one execution path, and it is the real one.
