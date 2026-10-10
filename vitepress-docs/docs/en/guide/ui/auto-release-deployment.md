# Stable release deployment (PoropsK fork)

This fork uses **cfmail Stable Release Update** for the existing `mail-api` Worker and Direct Upload Pages project `cfmail`. Policy lives in `.github/config/cfmail_release.json`; the reviewed baseline is v1.12.0 with D1 v0.0.9. The controller never syncs upstream main, initializes/migrates D1, or sends email.

## Eligibility

The schedule checks at minute 0 of every hour, every day (`0 * * * *`; 00:00, 01:00 through 23:00 Taipei). GitHub interprets schedules in UTC by default; Taipei's offset is a whole number of hours, so both time zones align on the hour. Only published, non-prerelease `vX.Y.Z` releases qualify. Resolve the tag to an immutable commit SHA. The production Pages deployment's commit hash records the deployed source; verify its Worker version, D1 version and reviewed baseline. Worker must use the reviewed initial version ID or a later deployment with matching tag/SHA annotations; unreviewed Dashboard edits and split deployments stop.

The allowlist covers documentation, CSS/styles, specified image/font extensions, Vue style-content-only edits and version-only changes in package files and Worker constants. Candidate upstream workflows are neither executed nor merged into this fork's controller.

Stop for DB/SQL/migration changes, authentication/email/API logic, template/script edits, dependencies/lockfiles/build scripts, bindings/configuration, unknown files, symlinks/executable changes, rewritten tags, downgrades, inconsistent versions/sources or a database needing migration. A migration-file edit stops even when DB_VERSION is unchanged. Review, back up and rehearse those releases separately before explicitly updating policy; do not bypass the gate.

## Configuration and operation

### Workers AI email extraction

This deployment policy reviews the `AI` binding of type `ai`. Both `BACKEND_TOML` and the policy's `wrangler.ai` must contain `[ai] binding = "AI"`. `keep_vars=true` preserves variables; it does not replace the AI resource binding declaration.

Use text variables `ENABLE_AI_EMAIL_EXTRACT=true` and `AI_EXTRACT_MODEL=@cf/meta/llama-3.1-8b-instruct-fast`. The controller verifies these against `worker_variables`. Cloudflare's default `project=<catalog>` marker is normalized; other additional fields, missing, duplicate or renamed AI bindings and changed model/flag values stop deployment. Unknown resources and encrypted bindings still stop. All variables and bindings, including the original JWT, remain covered by the before/after comparison.

Update the live Worker, policy and `BACKEND_TOML` together. After testing JSON extraction, validate main with `check_only=true` and `validate_build=true`. A version created by a configuration update must retain the matching application SHA/tag annotations; the controller branch commit is not the application source. Adding AI only in the Dashboard is insufficient for future deployments.

### Actions settings

Reuse the six repository Secrets: `CLOUDFLARE_ACCOUNT_ID`, `CLOUDFLARE_API_TOKEN`, `BACKEND_TOML`, `FRONTEND_NAME`, `FRONTEND_BRANCH`, `FRONTEND_ENV`. Values must match policy. BACKEND_TOML has `keep_vars=true` and no vars, preserving Dashboard JWT/password values. Never commit tokens.

Set the repository Variable `CFMAIL_AUTO_RELEASE_ENABLED=true` to allow deployment. Deployment also requires main, an eligible update, and a schedule or non-check-only manual invocation; push validation never deploys. Turning this variable off still permits inspection. Policy and the scheduled workflow must exist on main. Main retains the controller baseline; application candidates are checked out from fixed upstream SHAs instead of replacing main via gh repo sync.

Actions → **cfmail Stable Release Update** → **Run workflow**:

1. Select main.
2. Keep `check_only=true` for inspection/builds; `validate_build=true` builds even when the release is already deployed.
3. To update immediately under identical rules, uncheck check_only. This never bypasses a gate.
4. Read the run Summary's `no_update`, `eligible` or `blocked` decision and reason. `no_update` does not redeploy.

Build with frozen lockfiles, Node 24 and pnpm 10.10.0. Dependency installation/build steps have no deployment token. Recheck production and release with GET requests immediately before deployment. Deploy Worker, verify unchanged variables/JWT/bindings, DB health and version, then deploy the built Pages with the identical source commit hash. Finally compare live JS bytes on both custom and deployment domains with the build. A single concurrency group serializes production updates.

Keep legacy Deploy Backend/Frontend, page-function deployment and Upstream Sync disabled in this fork to avoid bypasses and old-source redeployments. The replacement offers a manual entry point. It never updates Dashboard variables, bindings or compatibility dates; unexpected configuration stops.

## Stops and recovery

For a blocked release, inspect the reason, pin its SHA and perform the required review/rehearsal. Do not initialize the database. If Worker succeeds but Pages fails, the next run stops on inconsistent versions rather than blindly retrying or restoring data. Inspect both actual deployments before choosing a redeployment or code rollback.

Disable automatic deployment in Settings → Secrets and variables → Actions → Variables by setting `CFMAIL_AUTO_RELEASE_ENABLED=false`; alternatively disable the workflow in Actions. Worker rollback does not restore D1/KV data. Backups contain mail and credentials and remain private.

GitHub schedules can be delayed. GitHub identifies the start of each hour as a high-load period, when some queued jobs may be dropped; this schedule does not guarantee an exact start time. See the [official schedule documentation](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule). Schedules can also be disabled after 60 days of inactivity in public repos. Inspect the latest successful run periodically; an up-to-date deployment should still show `no_update`. Version/DB health checks do not replace old-mail and actual delivery acceptance for substantial upgrades.
