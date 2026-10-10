"""Fail-closed release controller. All network calls here are GET requests.

Deployment is performed by explicit Wrangler steps after this controller passes.
No D1 migrations, mail delivery, credential rotation or Git ref writes are used.
Only sanitized decisions are written to Actions logs and summaries.
"""
import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import time
import tomllib
import urllib.error
import urllib.parse
import urllib.request

TAG = re.compile(r"v(\d+)\.(\d+)\.(\d+)\Z")
SHA = re.compile(r"[0-9a-f]{40}\Z")
VERSION_LINE = re.compile(r"(VERSION:\s*'v'\s*\+\s*')([0-9]+\.[0-9]+\.[0-9]+)(')")
DB_VERSION = re.compile(r'\bDB_VERSION:\s*[\'"](v[0-9.]+)[\'"]')
POLICY = Path(__file__).resolve().parents[1] / "config/cfmail_release.json"


class Stop(Exception):
    def __init__(self, code, details=None):
        self.code, self.details = code, details or {}
        super().__init__(code)


def require(ok, code, details=None):
    if not ok:
        raise Stop(code, details)


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write_json(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    os.chmod(path, 0o600)


def get(url, headers=None, binary=False):
    request = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 cfmail-release-guard",
        "Cache-Control": "no-cache", **(headers or {})}, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            body = response.read()
        return body if binary else json.loads(body)
    except urllib.error.HTTPError as error:
        raise Stop("HTTP_READ_FAILED", {"host": urllib.parse.urlparse(url).hostname,
                                         "http_status": error.code}) from None
    except (urllib.error.URLError, TimeoutError):
        raise Stop("NETWORK_READ_FAILED") from None
    except (ValueError, UnicodeError):
        raise Stop("INVALID_API_RESPONSE") from None


def gh(path):
    token = os.environ.get("GH_TOKEN")
    headers = {"Accept": "application/vnd.github+json"}
    if token:
        headers["Authorization"] = "Bearer " + token
    return get("https://api.github.com/" + path, headers)


def cf(policy, path):
    token = os.environ.get("CLOUDFLARE_API_TOKEN")
    require(bool(token), "MISSING_CLOUDFLARE_TOKEN")
    body = get("https://api.cloudflare.com/client/v4" + path,
               {"Authorization": "Bearer " + token})
    require(body.get("success") is True, "CLOUDFLARE_READ_FAILED")
    return body["result"]


def source_file(policy, sha, path):
    require(bool(SHA.fullmatch(sha)), "INVALID_SOURCE_SHA")
    obj = gh(f"repos/{policy['upstream']}/contents/{path}?ref={sha}")
    require(obj.get("type") == "file" and obj.get("encoding") == "base64",
            "UNREADABLE_SOURCE_FILE", {"path": path})
    return base64.b64decode(obj["content"], validate=False).decode("utf-8")


def normalize_constants(text, version):
    matches = list(VERSION_LINE.finditer(text))
    require(len(matches) == 1 and matches[0][2] == version,
            "UNKNOWN_VERSION_FORMAT")
    return VERSION_LINE.sub(r"\1VERSION_PLACEHOLDER\3", text)


def style_only(before, after):
    # Attribute changes, template/script edits and added/removed style blocks stop.
    pattern = re.compile(r"(<style\b[^>]*>)(.*?)(</style\s*>)", re.DOTALL)
    protected = re.compile(r"<script\b[^>]*>.*?</script\s*>|<template\b[^>]*>.*</template\s*>", re.DOTALL)
    left_code, right_code = list(protected.finditer(before)), list(protected.finditer(after))
    if [m[0] for m in left_code] != [m[0] for m in right_code]:
        return False
    left, right = list(pattern.finditer(before)), list(pattern.finditer(after))
    if not left or len(left) != len(right):
        return False
    for text, styles, code in ((before, left, left_code), (after, right, right_code)):
        if any(block.start() < style.start() < block.end() for style in styles for block in code):
            return False
        residual = text
        for block in sorted([*styles, *code], key=lambda m: m.start(), reverse=True):
            residual = residual[:block.start()] + residual[block.end():]
        if re.sub(r"<!--.*?-->", "", residual, flags=re.DOTALL).strip():
            return False
    normalize = lambda text: pattern.sub(lambda m: m[1] + "STYLE_PLACEHOLDER" + m[3], text)
    return normalize(before) == normalize(after)


def check_package(before, after, old_tag, new_tag):
    old, new = json.loads(before), json.loads(after)
    require(old.pop("version", None) == old_tag[1:] and
            new.pop("version", None) == new_tag[1:], "PACKAGE_VERSION_MISMATCH")
    require(old == new, "DEPENDENCY_OR_BUILD_SETTING_CHANGE")


def guard_diff(files, load_before, load_after, old_tag, new_tag):
    require(len(files) < 300, "CHANGE_LIST_MAY_BE_TRUNCATED")
    unknown = []
    for item in files:
        path = item["filename"]
        require(item.get("status") in {"modified", "added", "removed", "renamed"},
                "UNKNOWN_CHANGE_TYPE")
        if item.get("status") == "renamed":
            unknown.append(path)
            continue
        if path.startswith("db/") or path.endswith(".sql") or path == "worker/src/admin_api/db_api.ts":
            raise Stop("DATABASE_SCHEMA_OR_MIGRATION_CHANGE", {"path": path})
        if path in {"worker/package.json", "frontend/package.json"}:
            require(item["status"] == "modified", "PACKAGE_FILE_REMOVED_OR_ADDED")
            check_package(load_before(path), load_after(path), old_tag, new_tag)
        elif path == "worker/src/constants.ts":
            require(normalize_constants(load_before(path), old_tag[1:]) ==
                    normalize_constants(load_after(path), new_tag[1:]),
                    "DATABASE_OR_RUNTIME_CONSTANT_CHANGE")
        elif path.endswith(".md") and ("/" not in path or path.startswith(("docs/", "vitepress-docs/docs/"))):
            pass
        elif path.startswith(".github/workflows/") and path.endswith((".yml", ".yaml")):
            # Candidate workflows are never run or merged into the controller.
            pass
        elif path.startswith("frontend/src/") and path.endswith((".css", ".scss", ".sass", ".less")):
            pass
        elif path.startswith("frontend/src/") and path.endswith(".vue") and item["status"] == "modified":
            if not style_only(load_before(path), load_after(path)):
                unknown.append(path)
        elif path.startswith(("frontend/public/", "frontend/src/assets/")) and path.endswith((".png", ".jpg", ".jpeg", ".webp", ".ico", ".woff", ".woff2")):
            pass
        else:
            unknown.append(path)
    require(not unknown, "UNKNOWN_OR_RUNTIME_CHANGE", {"paths": unknown[:30]})


def validate_diff(policy, base, candidate, old_tag, new_tag):
    if base == candidate:
        return
    comparison = gh(f"repos/{policy['upstream']}/compare/{base}...{candidate}")
    require(comparison.get("status") == "ahead" and comparison.get("ahead_by", 0) > 0,
            "RELEASE_NOT_DESCENDANT_OF_DEPLOYED_SOURCE")
    files = comparison.get("files")
    require(isinstance(files, list) and files, "MISSING_CHANGE_LIST")
    for sha in (base, candidate):
        tree = gh(f"repos/{policy['upstream']}/git/trees/{sha}?recursive=1")
        require(tree.get("truncated") is False, "SOURCE_TREE_TRUNCATED")
        modes = {entry["path"]: entry.get("mode") for entry in tree["tree"]}
        for item in files:
            path = item["filename"]
            if path in modes:
                require(modes[path] == "100644", "SYMLINK_OR_EXECUTABLE_CHANGE", {"path": path})
    guard_diff(files, lambda p: source_file(policy, base, p),
               lambda p: source_file(policy, candidate, p), old_tag, new_tag)


def source_versions(policy, sha):
    versions = [json.loads(source_file(policy, sha, p))["version"]
                for p in ("worker/package.json", "frontend/package.json")]
    constants = source_file(policy, sha, "worker/src/constants.ts")
    require(len(set(versions)) == 1 and TAG.fullmatch("v" + versions[0]),
            "SOURCE_VERSIONS_DISAGREE")
    normalize_constants(constants, versions[0])
    db = DB_VERSION.search(constants)
    require(db and db[1] == policy["db_version"], "DATABASE_VERSION_CHANGE")
    return "v" + versions[0]


def string_list(value):
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            value = [value]
    return value if isinstance(value, list) and all(isinstance(x, str) for x in value) else []


def bindings_fingerprint(settings):
    # Full logical binding values, including plaintext JWT, stay in memory.
    bindings = []
    for binding in settings["bindings"]:
        copy = dict(binding)
        if copy.get("type") == "json" and isinstance(copy.get("json"), str):
            copy["json"] = json.loads(copy["json"])
        bindings.append(copy)
    raw = json.dumps(sorted(bindings, key=lambda b: b["name"]), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode()).hexdigest()


def verify_worker_source(policy, version, sha, tag):
    if version.get("id") == policy["approved_worker_version_id"]:
        require(sha == policy["approved_baseline_sha"], "BASELINE_WORKER_SOURCE_DISAGREES")
    else:
        annotations = version.get("annotations", {})
        require(annotations.get("workers/message") == sha and
                annotations.get("workers/tag") == tag, "UNREVIEWED_WORKER_VERSION")


def verify_ai_configuration(policy, bindings, variables):
    config = policy["wrangler"].get("ai")
    expected = [{"name": config["binding"], "type": "ai"}] if config else []
    actual = []
    for binding in bindings:
        if binding.get("type") != "ai":
            continue
        normalized = dict(binding)
        # Cloudflare adds this catalog marker to the native AI binding.
        if normalized.get("project") == "<catalog>":
            normalized.pop("project")
        actual.append(normalized)
    require(actual == expected, "AI_BINDING_CHANGED")
    for name, value in policy.get("worker_variables", {}).items():
        require(variables.get(name) == value, "AI_VARIABLE_CHANGED", {"name": name})


def live_state(policy, expected_worker_sha=None):
    config = policy["wrangler"]
    account, worker = config["account_id"], config["name"]
    require(os.environ.get("CLOUDFLARE_ACCOUNT_ID") == account, "ACCOUNT_SETTING_CHANGED")
    settings = cf(policy, f"/accounts/{account}/workers/scripts/{worker}/settings")
    require(settings.get("compatibility_date") == config["compatibility_date"] and
            settings.get("compatibility_flags") == config["compatibility_flags"],
            "RUNTIME_COMPATIBILITY_CHANGED")
    bindings = settings["bindings"]
    d1 = [b for b in bindings if b.get("type") == "d1"]
    kv = [b for b in bindings if b.get("type") == "kv_namespace"]
    require(len(d1) == 1 and d1[0].get("name") == "DB" and
            d1[0].get("id") == config["d1_databases"][0]["database_id"], "D1_BINDING_CHANGED")
    require(len(kv) == 1 and kv[0].get("name") == "KV" and
            kv[0].get("namespace_id") == config["kv_namespaces"][0]["id"], "KV_BINDING_CHANGED")
    require(all(b.get("type") in {"d1", "kv_namespace", "plain_text", "json", "ai"} for b in bindings),
            "UNKNOWN_OR_ENCRYPTED_BINDING")
    variables = {}
    for b in bindings:
        if b["type"] == "plain_text":
            variables[b["name"]] = b["text"]
        elif b["type"] == "json":
            variables[b["name"]] = json.loads(b["json"]) if isinstance(b["json"], str) else b["json"]
    verify_ai_configuration(policy, bindings, variables)
    require(bool(variables.get("JWT_SECRET")), "JWT_NOT_READABLE")
    admins = string_list(variables.get("ADMIN_PASSWORDS"))
    require(bool(admins), "ADMIN_HEALTH_CHECK_NOT_AVAILABLE")
    headers = {"x-admin-auth": admins[0], "x-lang": "en"}
    passwords = string_list(variables.get("PASSWORDS", []))
    if passwords:
        headers["x-custom-auth"] = passwords[0]
    db = get(policy["api_url"] + "/admin/db_version", headers)
    require(db.get("current_db_version") == policy["db_version"] and
            db.get("code_db_version") == policy["db_version"] and
            db.get("need_initialization") is False and db.get("need_migration") is False,
            "LIVE_DATABASE_NEEDS_REVIEW")
    public_headers = {k: v for k, v in headers.items() if k != "x-admin-auth"}
    api = get(policy["api_url"] + "/open_api/settings", public_headers)
    require(TAG.fullmatch(api.get("version", "")), "LIVE_VERSION_UNREADABLE")
    pages = cf(policy, f"/accounts/{account}/pages/projects/{policy['pages_project']}")
    require(pages.get("name") == policy["pages_project"] and pages.get("source") is None and
            pages.get("production_branch") == policy["pages_branch"], "PAGES_TARGET_CHANGED")
    deployment = pages["canonical_deployment"]
    sha = deployment["deployment_trigger"]["metadata"]["commit_hash"]
    require(SHA.fullmatch(sha) and deployment.get("environment") == "production" and
            deployment["latest_stage"].get("status") == "success", "PAGES_DEPLOYMENT_UNVERIFIED")
    deployments = cf(policy, f"/accounts/{account}/workers/scripts/{worker}/deployments")
    active = max(deployments["deployments"], key=lambda d: d["created_on"])["versions"]
    require(len(active) == 1 and active[0].get("percentage") == 100, "SPLIT_WORKER_DEPLOYMENT")
    version = cf(policy, f"/accounts/{account}/workers/scripts/{worker}/versions/{active[0]['version_id']}")
    verify_worker_source(policy, version, expected_worker_sha or sha, api["version"])
    domains = cf(policy, f"/accounts/{account}/workers/domains")
    require(any(d.get("service") == worker and d.get("hostname") == "mail-api.jpri.top" for d in domains),
            "WORKER_DOMAIN_CHANGED")
    return {"deployed_sha": sha, "live_tag": api["version"],
            "db_version": db["current_db_version"],
            "worker_version_id": version["id"],
            "pages_deployment_id": deployment["id"],
            "bindings_fingerprint": bindings_fingerprint(settings)}


def latest_release(policy):
    release = gh(f"repos/{policy['upstream']}/releases/latest")
    tag = release.get("tag_name", "")
    require(TAG.fullmatch(tag) and release.get("draft") is False and
            release.get("prerelease") is False, "NOT_A_STABLE_RELEASE")
    ref = gh(f"repos/{policy['upstream']}/git/ref/tags/{tag}")["object"]
    for _ in range(5):
        if ref["type"] == "commit":
            break
        require(ref["type"] == "tag", "UNKNOWN_TAG_TARGET")
        ref = gh(f"repos/{policy['upstream']}/git/tags/{ref['sha']}")["object"]
    require(ref["type"] == "commit" and SHA.fullmatch(ref["sha"]), "INVALID_RELEASE_SHA")
    return tag, ref["sha"]


def check_other_deployments(policy):
    for status in ("queued", "in_progress", "waiting", "pending", "requested"):
        runs = gh(f"repos/{policy['repository']}/actions/runs?status={status}&per_page=100")["workflow_runs"]
        require(len(runs) < 100, "RUN_LIST_MAY_BE_TRUNCATED")
        for run in runs:
            if str(run["id"]) != os.environ.get("GITHUB_RUN_ID") and run["name"] in {
                    "Deploy Backend", "Deploy Frontend", "Deploy Frontend with page function",
                    "cfmail Stable Release Update"}:
                raise Stop("OTHER_DEPLOYMENT_ACTIVE", {"run_id": run["id"]})


def make_plan(policy):
    check_other_deployments(policy)
    state = live_state(policy)
    deployed_tag = source_versions(policy, state["deployed_sha"])
    require(state["live_tag"] == deployed_tag, "FRONTEND_BACKEND_VERSION_DISAGREE")
    floor = policy["approved_baseline_sha"]
    if state["deployed_sha"] != floor:
        floor_tag = source_versions(policy, floor)
        validate_diff(policy, floor, state["deployed_sha"], floor_tag, deployed_tag)
    tag, sha = latest_release(policy)
    require(source_versions(policy, sha) == tag, "RELEASE_TAG_VERSION_MISMATCH")
    if tag == deployed_tag:
        require(sha == state["deployed_sha"], "RELEASE_TAG_REWRITTEN")
        decision = "no_update"
    else:
        require(tuple(map(int, TAG.fullmatch(tag).groups())) >
                tuple(map(int, TAG.fullmatch(deployed_tag).groups())), "RELEASE_DOWNGRADE_BLOCKED")
        validate_diff(policy, state["deployed_sha"], sha, deployed_tag, tag)
        decision = "eligible"
    # Do not log or publish binding digests, auth headers or variable values.
    return {"decision": decision, "candidate_tag": tag, "candidate_sha": sha,
            "deployed_tag": deployed_tag, "deployed_sha": state["deployed_sha"],
            "worker_version_id": state["worker_version_id"],
            "db_version": state["db_version"], "pages_deployment_id": state["pages_deployment_id"]}


def prepare(policy, candidate):
    raw = os.environ.get("BACKEND_TOML", "")
    require(tomllib.loads(raw) == policy["wrangler"], "BACKEND_CONFIG_CHANGED")
    require(os.environ.get("FRONTEND_NAME") == policy["pages_project"] and
            os.environ.get("FRONTEND_BRANCH") == policy["pages_branch"], "FRONTEND_TARGET_CHANGED")
    env_raw = os.environ.get("FRONTEND_ENV", "")
    env = dict(line.split("=", 1) for line in env_raw.splitlines() if line.strip() and not line.startswith("#"))
    require(env == policy["frontend_env"], "FRONTEND_CONFIG_CHANGED")
    (candidate / "worker/wrangler.toml").write_text(raw, encoding="utf-8")
    (candidate / "frontend/.env.prod").write_text(env_raw, encoding="utf-8")


def verify_build(policy, candidate, plan):
    scripts = list((candidate / "frontend/dist/assets").glob("*.js"))
    require(bool(scripts), "NO_FRONTEND_BUNDLE")
    text = "\n".join(path.read_text(encoding="utf-8") for path in scripts)
    require(plan["candidate_tag"] in text and policy["api_url"] in text and
            "http://127.0.0.1:8787" not in text and "http://localhost:8787" not in text,
            "FRONTEND_BUNDLE_TARGET_MISMATCH")
    require(len(list((candidate / "frontend/dist/assets").glob("index-*.js"))) == 1,
            "UNKNOWN_FRONTEND_ENTRY")


def before_deploy(policy, plan, snapshot):
    require(plan["decision"] == "eligible", "NO_ELIGIBLE_UPDATE")
    require(make_plan(policy) == plan, "PRODUCTION_OR_RELEASE_CHANGED_DURING_BUILD")
    state = live_state(policy)
    write_json(snapshot, state)


def after_worker(policy, plan, snapshot):
    state = live_state(policy, expected_worker_sha=plan["candidate_sha"])
    before = read_json(snapshot)
    require(state["live_tag"] == plan["candidate_tag"], "BACKEND_VERSION_NOT_UPDATED")
    require(state["deployed_sha"] == plan["deployed_sha"], "PAGES_CHANGED_DURING_BACKEND_DEPLOY")
    require(state["bindings_fingerprint"] == before["bindings_fingerprint"],
            "VARIABLE_OR_BINDING_CHANGED_AFTER_DEPLOY")


def after_frontend(policy, plan, snapshot, candidate):
    state = live_state(policy)
    require(state["live_tag"] == plan["candidate_tag"] and
            state["deployed_sha"] == plan["candidate_sha"], "DEPLOYED_SOURCE_VERSION_MISMATCH")
    require(state["bindings_fingerprint"] == read_json(snapshot)["bindings_fingerprint"],
            "VARIABLE_OR_BINDING_CHANGED_AFTER_DEPLOY")
    local = next((candidate / "frontend/dist/assets").glob("index-*.js"))
    for site in (policy["frontend_url"], f"https://{state['pages_deployment_id']}.cfmail-3jz.pages.dev"):
        html = get(site, binary=True).decode("utf-8")
        scripts = re.findall(r'\bsrc=[\'"](/assets/index-[A-Za-z0-9_-]+\.js)[\'"]', html)
        require(scripts == ["/assets/" + local.name], "LIVE_FRONTEND_ENTRY_MISMATCH")
        remote = get(site + scripts[0], binary=True)
        require(hashlib.sha256(remote).digest() == hashlib.sha256(local.read_bytes()).digest(),
                "LIVE_FRONTEND_BUNDLE_MISMATCH")


def summary(value):
    print("RELEASE_GUARD=" + json.dumps(value, ensure_ascii=True))
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if path:
        with open(path, "a", encoding="utf-8") as out:
            out.write("\n```json\n" + json.dumps(value, ensure_ascii=False, indent=2) + "\n```\n")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["plan", "prepare", "verify-build", "before", "after-worker", "after-frontend"])
    args = parser.parse_args()
    policy = read_json(POLICY)
    require(os.environ.get("GITHUB_REPOSITORY") == policy["repository"], "WRONG_REPOSITORY")
    plan_path = Path(os.environ["PLAN_FILE"])
    snapshot = Path(os.environ["SNAPSHOT_FILE"])
    candidate = Path(os.environ["CANDIDATE_DIR"])
    if args.command == "plan":
        plan = make_plan(policy)
        write_json(plan_path, plan)
        output = os.environ.get("GITHUB_OUTPUT")
        if output:
            with open(output, "a", encoding="utf-8") as out:
                for key in ("decision", "candidate_tag", "candidate_sha"):
                    out.write(key + "=" + plan[key] + "\n")
        summary(plan)
    elif args.command == "prepare":
        prepare(policy, candidate)
        summary({"phase": "prepare", "settings_match": True})
    else:
        plan = read_json(plan_path)
        if args.command == "verify-build":
            verify_build(policy, candidate, plan)
        elif args.command == "before":
            before_deploy(policy, plan, snapshot)
        else:
            # Account for normal edge propagation; never retry a deployment.
            for attempt in range(12):
                try:
                    if args.command == "after-worker":
                        after_worker(policy, plan, snapshot)
                    else:
                        after_frontend(policy, plan, snapshot, candidate)
                    break
                except Stop:
                    if attempt == 11:
                        raise
                    time.sleep(5)
        summary({"phase": args.command, "passed": True, "candidate_tag": plan["candidate_tag"]})


if __name__ == "__main__":
    try:
        main()
    except Stop as error:
        summary({"decision": "blocked", "reason": error.code, **error.details,
                 "automatic_database_write": False})
        raise SystemExit(1)
    except Exception as error:
        # Never print response bodies, variables, request headers or traceback.
        summary({"decision": "blocked", "reason": "UNEXPECTED_CHECK_FAILURE",
                 "error_type": type(error).__name__, "automatic_database_write": False})
        raise SystemExit(1)
