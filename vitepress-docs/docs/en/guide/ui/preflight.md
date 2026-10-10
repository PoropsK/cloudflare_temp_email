# Deployment preflight

The upgrade's `cfmail Deployment Preflight` lives on the separate `upgrade/preflight-v1.12.0` branch and runs only when its workflow changes on that branch. It builds the fixed v1.12.0 commit without changing the production upgrade branch.

Using the six deployment Secrets, it performs read-only Cloudflare token, Account, Worker, Pages, Zone and route checks, verifies the production DB/KV targets, then runs a Worker dry-run and frontend build with Node 24. Token and Worker variable values are not logged.

The API may omit source for Direct Upload Pages projects; preflight accepts an absent or null field while also verifying the project and production branch.

Success confirms resource reads, settings and builds only. It does not exercise write permissions, migrate D1 or deploy. Production upgrade still requires a current backup, schema verification, ordered backend/frontend deployment and functional acceptance.
