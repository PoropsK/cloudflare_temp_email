# Stable release deployment (PoropsK fork)

This fork uses **cfmail Stable Release Update** for the existing `mail-api` Worker and Direct Upload Pages project `cfmail`. Policy lives in `.github/config/cfmail_release.json`; the reviewed baseline is v1.12.0 with D1 v0.0.9. The controller never syncs upstream main, initializes/migrates D1, or sends email.

## Eligibility

The weekly schedule is Monday 00:17 UTC (08:17 Taipei). Only published, non-prerelease `vX.Y.Z` releases qualify. Resolve the tag to an immutable commit SHA. The production Pages deployment's commit hash records the deployed source; verify its Worker version, D1 version and reviewed baseline.

The allowlist covers documentation, CSS/styles, specified image/font extensions, Vue style-content-only edits and version-only changes in package files and Worker constants. Candidate upstream workflows are neither executed nor merged into this fork's controller.

Stop for DB/SQL/migration changes, authentication/email/API logic, template/script edits, dependencies/lockfiles/build scripts, bindings/configuration, unknown files, symlinks/executable changes, rewritten tags, downgrades, inconsistent versions/sources or a database needing migration. A migration-file edit stops even when DB_VERSION is unchanged. Review, back up and rehearse those releases separately before explicitly updating policy; do not bypass the gate.

## Configuration and operation

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

GitHub schedules can be delayed and can be disabled after 60 days of inactivity in public repos. Inspect the latest successful run periodically; an up-to-date deployment should still show `no_update`. Version/DB health checks do not replace old-mail and actual delivery acceptance for substantial upgrades.
