"""Regression tests for false-positive deployment eligibility, using no network."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import cfmail_release as release


class GateTests(unittest.TestCase):
    def guard(self, path, before="", after="", status="modified"):
        release.guard_diff([{"filename": path, "status": status}],
                           lambda _: before, lambda _: after, "v1.12.0", "v1.12.1")

    def test_database_change_stops_even_if_version_is_unchanged(self):
        for path in ("db/schema.sql", "worker/src/admin_api/db_api.ts", "new-migration.sql"):
            with self.subTest(path=path), self.assertRaises(release.Stop) as result:
                self.guard(path)
            self.assertEqual(result.exception.code, "DATABASE_SCHEMA_OR_MIGRATION_CHANGE")

    def test_unrecognized_runtime_auth_and_lockfile_changes_stop(self):
        for path in ("worker/src/email/index.ts", "worker/src/worker.ts",
                     "frontend/src/api/index.js", "frontend/src/views/common/Login.vue",
                     "worker/pnpm-lock.yaml", "worker/wrangler.toml.template", "new.js"):
            with self.subTest(path=path), self.assertRaises(release.Stop):
                self.guard(path, "before", "after")

    def test_constants_allow_only_exact_version_replacement(self):
        before = "VERSION: 'v' + '1.12.0', DB_VERSION: 'v0.0.9'"
        self.guard("worker/src/constants.ts", before, before.replace("1.12.0", "1.12.1"))
        for after in (before.replace("v0.0.9", "v0.0.10").replace("1.12.0", "1.12.1"),
                      before.replace("1.12.0", "1.12.1") + ", NEW_SETTING: true"):
            with self.assertRaises(release.Stop):
                self.guard("worker/src/constants.ts", before, after)

    def test_package_version_changes_are_allowed_but_dependency_scripts_are_not(self):
        old = {"version": "1.12.0", "dependencies": {"hono": "4.13.7"}, "scripts": {"build": "safe"}}
        new = {**old, "version": "1.12.1"}
        self.guard("worker/package.json", json.dumps(old), json.dumps(new))
        for field, value in (("dependencies", {"hono": "5.0.0"}), ("scripts", {"build": "new command"})):
            with self.assertRaises(release.Stop):
                self.guard("worker/package.json", json.dumps(old), json.dumps({**new, field: value}))

    def test_vue_style_only_is_allowed_and_script_template_changes_stop(self):
        old = '<template>Login</template><script>safe()</script><style scoped>.a{color:red}</style>'
        new = old.replace("color:red", "color:blue")
        self.guard("frontend/src/views/Login.vue", old, new)
        for changed in (new.replace("safe()", "different()"), new.replace("Login", "other"),
                        new.replace("scoped", ""), new + "<style>.x{}</style>"):
            with self.assertRaises(release.Stop):
                self.guard("frontend/src/views/Login.vue", old, changed)

    def test_docs_css_and_images_are_allowed(self):
        for path in ("CHANGELOG.md", "vitepress-docs/docs/en/guide/ui.md",
                     "frontend/src/style.css", "frontend/public/icon.png"):
            with self.subTest(path=path):
                self.guard(path)

    def test_style_looking_text_inside_script_is_not_a_style_only_change(self):
        before = '<script>const x="<style>red</style>"</script><style>.a{color:red}</style>'
        with self.assertRaises(release.Stop):
            self.guard("frontend/src/App.vue", before, before.replace('>red<', '>new code<'))
        with self.assertRaises(release.Stop):
            self.guard("frontend/src/App.vue", '<custom><style>a</style></custom>',
                       '<custom><style>b</style></custom>')

    def test_candidate_workflows_are_ignored_but_build_patches_are_not(self):
        self.guard(".github/workflows/backend_deploy.yaml")
        with self.assertRaises(release.Stop):
            self.guard(".github/config/mail-parser-wasm-worker.patch")

    def test_rename_and_truncated_change_lists_stop(self):
        with self.assertRaises(release.Stop):
            self.guard("README.md", status="renamed")
        with self.assertRaises(release.Stop):
            release.guard_diff([{"filename": "README.md", "status": "modified"}] * 300,
                               lambda _: "", lambda _: "", "v1.12.0", "v1.12.1")

    def plan(self, old_tag="v1.12.0", new_tag="v1.12.0", old_sha="a" * 40, new_sha="a" * 40):
        policy = {"approved_baseline_sha": old_sha}
        with patch.object(release, "check_other_deployments"), \
             patch.object(release, "live_state", return_value={"deployed_sha": old_sha,
                 "live_tag": old_tag, "db_version": "v0.0.9", "pages_deployment_id": "page"}), \
             patch.object(release, "source_versions", side_effect=lambda _, sha: old_tag if sha == old_sha else new_tag), \
             patch.object(release, "latest_release", return_value=(new_tag, new_sha)), \
             patch.object(release, "validate_diff") as diff:
            return release.make_plan(policy), diff.call_count

    def test_current_release_is_not_redeployed(self):
        plan, calls = self.plan()
        self.assertEqual(plan["decision"], "no_update")
        self.assertEqual(calls, 0)

    def test_new_release_requires_change_guard(self):
        plan, calls = self.plan(new_tag="v1.12.1", new_sha="b" * 40)
        self.assertEqual(plan["decision"], "eligible")
        self.assertEqual(calls, 1)

    def test_downgrade_and_rewritten_tag_stop(self):
        for tag in ("v1.11.0", "v1.12.0"):
            with self.subTest(tag=tag), self.assertRaises(release.Stop):
                self.plan(new_tag=tag, new_sha="b" * 40)

    def test_database_mismatch_blocks_live_state(self):
        policy = release.read_json(release.POLICY)
        settings = {"compatibility_date": "2025-09-01", "compatibility_flags": ["nodejs_compat"], "bindings": [
            {"type": "d1", "name": "DB", "id": policy["wrangler"]["d1_databases"][0]["database_id"]},
            {"type": "kv_namespace", "name": "KV", "namespace_id": policy["wrangler"]["kv_namespaces"][0]["id"]},
            {"type": "plain_text", "name": "JWT_SECRET", "text": "fake"},
            {"type": "json", "name": "ADMIN_PASSWORDS", "json": ["fake-admin"]}]}
        with patch.dict(release.os.environ, {"CLOUDFLARE_ACCOUNT_ID": policy["wrangler"]["account_id"]}), \
             patch.object(release, "cf", return_value=settings), \
             patch.object(release, "get", return_value={"current_db_version": "v0.0.8",
                 "code_db_version": "v0.0.9", "need_migration": True, "need_initialization": False}), \
             self.assertRaises(release.Stop) as result:
            release.live_state(policy)
        self.assertEqual(result.exception.code, "LIVE_DATABASE_NEEDS_REVIEW")

    def test_production_change_during_build_stops_before_deploy(self):
        with tempfile.TemporaryDirectory() as folder:
            with patch.object(release, "make_plan", return_value={"decision": "no_update"}), \
                 self.assertRaises(release.Stop):
                release.before_deploy({}, {"decision": "eligible"}, Path(folder) / "snapshot.json")
            self.assertFalse((Path(folder) / "snapshot.json").exists())

    def test_variable_or_jwt_change_after_worker_blocks_frontend(self):
        with tempfile.TemporaryDirectory() as folder:
            snapshot = Path(folder) / "snapshot.json"
            release.write_json(snapshot, {"bindings_fingerprint": "original"})
            with patch.object(release, "live_state", return_value={"live_tag": "v1.12.1",
                    "deployed_sha": "a" * 40, "bindings_fingerprint": "changed"}), \
                 self.assertRaises(release.Stop) as result:
                release.after_worker({}, {"candidate_tag": "v1.12.1", "deployed_sha": "a" * 40}, snapshot)
            self.assertEqual(result.exception.code, "VARIABLE_OR_BINDING_CHANGED_AFTER_DEPLOY")

    def test_symlink_change_stops(self):
        policy = {"upstream": "test/repo"}
        responses = [{"status": "ahead", "ahead_by": 1, "files": [{"filename": "README.md", "status": "modified"}]},
                     {"truncated": False, "tree": [{"path": "README.md", "mode": "120000"}]}]
        with patch.object(release, "gh", side_effect=responses), self.assertRaises(release.Stop):
            release.validate_diff(policy, "a" * 40, "b" * 40, "v1.12.0", "v1.12.1")


if __name__ == "__main__":
    unittest.main(verbosity=2)
