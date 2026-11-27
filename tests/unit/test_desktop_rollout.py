"""The private channel is verified without external networks or real signing credentials."""

import hashlib
import importlib.util
import json
import sqlite3
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

_MODULE_NAME = "vortoone_desktop_rollout_server"
_SPEC = importlib.util.spec_from_file_location(
    _MODULE_NAME, Path(__file__).resolve().parents[2] / "deploy" / "desktop-rollout" / "server.py"
)
rollout = importlib.util.module_from_spec(_SPEC)
sys.modules[_MODULE_NAME] = rollout
_SPEC.loader.exec_module(rollout)

API = "/api/desktop-rollout"
ADMIN = API + "/admin"
MANAGER = "/admin/releases"
SIGN_IN = MANAGER + "/sign-in"
CHECK_STATUS = "X-VortoOne-Rollout-Status"
ORIGIN = "https://vortoone.com"
SAME_ORIGIN = {"Origin": ORIGIN}
PASSWORD = "synthetic rollout test passphrase"
MAC = "darwin-aarch64"
WINDOWS = "windows-x86_64-nsis"
LINUX = "linux-x86_64-deb"


@pytest.fixture(scope="module")
def password_hash():
    return rollout.hash_admin_password(PASSWORD)


@pytest.fixture
def sandbox(tmp_path, password_hash):
    clock = [1_800_000_000]
    app = rollout.create_app(tmp_path, password_hash, clock=lambda: clock[0])
    return app, tmp_path, clock


@pytest.fixture
def admin(sandbox):
    app, _, _ = sandbox
    client = TestClient(app, base_url=ORIGIN, headers=SAME_ORIGIN)
    response = client.post(ADMIN + "/login", json={"password": PASSWORD})
    assert response.status_code == 204
    return client


def stage(root, version="0.3.6", platforms=(MAC,)):
    directory = root / "releases" / version
    directory.mkdir(parents=True)
    files = {}
    assets = {}
    for platform in platforms:
        filename = f"VortoOne_{version}_{rollout.SUFFIXES[platform]}"
        payload = (f"{platform}:{version}\0").encode()
        (directory / filename).write_bytes(payload)
        assets[platform] = {
            "filename": filename,
            "signature": "Zml4dHVyZS1zaWduYXR1cmU=",
            "sha256": hashlib.sha256(payload).hexdigest(),
        }
        files[filename] = payload
    (directory / "release.json").write_text(json.dumps({
        "version": version, "commit": "a" * 40, "notes": "Fixture changes",
        "created_at": "2026-11-26T15:30:00Z", "platforms": assets,
    }))
    return files


def invite(admin):
    response = admin.post(ADMIN + "/enrollments", json={})
    assert response.status_code == 200
    assert response.json()["expires_at"].endswith("Z")
    return response.json()["code"]


def enroll(admin, platform=MAC, code=None, version="0.3.4"):
    payload = {"code": code or invite(admin), "name": "Fixture device", "platform": platform, "version": version}
    response = admin.post(API + "/enroll", json=payload)
    assert response.status_code == 200
    return response.json()["device_id"], {"Authorization": "Bearer " + response.json()["token"]}


def check(client, auth, platform=MAC, version="0.3.4"):
    return client.post(API + "/check", headers=auth, json={"platform": platform, "version": version})


def no_offer(response, status):
    assert response.status_code == 204
    assert response.content == b""
    assert response.headers[CHECK_STATUS] == status
    assert response.headers["Cache-Control"] == "no-store"


def report(client, auth, status="installing", release="0.3.6"):
    return client.post(API + "/report", headers=auth, json={"release": release, "status": status})


def state(admin):
    response = admin.get(ADMIN + "/state")
    assert response.headers["Cache-Control"] == "no-store"
    assert response.status_code == 200
    return response.json()


def assign(admin, device, version="0.3.6"):
    return admin.put(f"{ADMIN}/devices/{device}/assignment", json={"version": version})


def pause(admin, paused=True, version="0.3.6"):
    response = admin.patch(f"{ADMIN}/releases/{version}", json={"paused": paused})
    assert response.status_code == 204


def test_assigned_version_is_delivered_only_to_the_device_and_installed_only_after_check(sandbox, admin):
    _, root, _ = sandbox
    files = stage(root, platforms=(MAC, WINDOWS, LINUX))
    mac_id, mac = enroll(admin)
    _, other = enroll(admin)
    no_offer(check(admin, mac), "awaiting_assignment")
    wrong_platform = check(admin, mac, platform=WINDOWS)
    assert wrong_platform.status_code == 409 and CHECK_STATUS not in wrong_platform.headers
    anonymous = check(admin, {})  # An admin cookie cannot act as a device.
    assert anonymous.status_code == 401 and CHECK_STATUS not in anonymous.headers
    forged = check(admin, {"Authorization": "Bearer " + "a" * 43})
    assert forged.status_code == 401 and CHECK_STATUS not in forged.headers
    assert report(admin, mac).status_code == 409
    assert assign(admin, mac_id).status_code == 204
    no_offer(check(admin, other), "awaiting_assignment")
    with sqlite3.connect(root / "rollout.sqlite3") as conn:
        assert conn.execute("SELECT COUNT(*) FROM tickets").fetchone()[0] == 0

    offered = check(admin, mac)
    assert offered.status_code == 200 and offered.headers["Cache-Control"] == "no-store"
    assert CHECK_STATUS not in offered.headers
    manifest = offered.json()
    assert manifest["version"] == "0.3.6"
    assert manifest["notes"] == "Fixture changes"
    assert list(manifest["platforms"]) == [MAC]
    asset = manifest["platforms"][MAC]
    assert asset["signature"] == "Zml4dHVyZS1zaWduYXR1cmU="
    assert asset["url"].startswith(ORIGIN + API + "/download/")
    filename = asset["url"].rsplit("/", 1)[1]
    assert filename == "VortoOne_0.3.6_aarch64.app.tar.gz"

    response = admin.get(asset["url"], headers=mac)
    assert response.status_code == 200 and response.content == files[filename]
    assert response.headers["Cache-Control"] == "no-store"
    assert admin.head(asset["url"], headers=mac).headers["content-length"] == str(len(files[filename]))
    partial = admin.get(asset["url"], headers={**mac, "Range": "bytes=0-3"})
    assert partial.status_code == 206 and partial.content == files[filename][:4]
    other_asset = asset["url"].rsplit("/", 1)[0] + "/VortoOne_0.3.6_windows-x64.exe"
    assert admin.get(other_asset, headers=mac).status_code == 404

    assert report(admin, mac).status_code == 204
    assert report(admin, mac, status="failed").status_code == 204
    assert check(admin, mac).status_code == 200
    record = next(row for row in state(admin)["devices"] if row["id"] == mac_id)
    assert (record["current_version"], record["status"]) == ("0.3.4", "failed")
    assert report(admin, mac).status_code == 204
    no_offer(check(admin, mac, version="0.3.6"), "up_to_date")
    record = next(row for row in state(admin)["devices"] if row["id"] == mac_id)
    assert (record["current_version"], record["status"]) == ("0.3.6", "up_to_date")
    assert admin.get(asset["url"], headers=mac).status_code == 404
    no_offer(check(admin, mac, version="0.3.10"), "up_to_date")  # Version order is numeric.


@pytest.mark.parametrize(
    ("method", "range_header", "allowed_status"),
    [
        ("GET", {}, 200),
        ("HEAD", {}, 200),
        ("GET", {"Range": "bytes=0-3"}, 206),
        ("HEAD", {"Range": "bytes=0-3"}, 206),
    ],
)
def test_download_ticket_requires_its_own_device(sandbox, admin, method, range_header, allowed_status):
    app, root, _ = sandbox
    stage(root)
    device, auth = enroll(admin)
    other, other_auth = enroll(admin)
    assert assign(admin, device).status_code == 204
    assert assign(admin, other).status_code == 204
    url = check(admin, auth).json()["platforms"][MAC]["url"]
    other_url = check(admin, other_auth).json()["platforms"][MAC]["url"]
    client = TestClient(app, base_url=ORIGIN)

    assert client.request(method, url, headers=range_header).status_code == 401
    assert client.request(method, url, headers={**other_auth, **range_header}).status_code == 404
    assert client.request(method, url, headers={**auth, **range_header}).status_code == allowed_status

    assert admin.delete(f"{ADMIN}/devices/{device}").status_code == 204
    assert client.request(method, url, headers={**auth, **range_header}).status_code == 401
    assert client.request(method, other_url, headers={**other_auth, **range_header}).status_code == allowed_status


def test_pausing_reassigning_disabling_and_leaving_remove_authority(sandbox, admin):
    _, root, _ = sandbox
    stage(root)
    stage(root, "0.3.7")
    device, auth = enroll(admin)
    assert assign(admin, device).status_code == 204
    offered = check(admin, auth).json()
    assert offered["version"] == "0.3.6"  # The newer staged 0.3.7 is not assigned.
    assert report(admin, auth, release="0.3.7").status_code == 409
    first = offered["platforms"][MAC]["url"]

    pause(admin)
    assert admin.get(first, headers=auth).status_code == 404
    no_offer(check(admin, auth, version="0.3.6"), "up_to_date")
    no_offer(check(admin, auth), "paused")  # The paused assignment has no fallback.
    assert report(admin, auth).status_code == 409
    assert state(admin)["devices"][0]["status"] == "assigned"
    pause(admin, False)
    assert admin.get(first, headers=auth).status_code == 404
    second = check(admin, auth).json()["platforms"][MAC]["url"]

    assert assign(admin, device, "0.3.7").status_code == 204
    assert admin.get(second, headers=auth).status_code == 404
    assert report(admin, auth).status_code == 409
    offered = check(admin, auth).json()
    assert offered["version"] == "0.3.7"
    third = offered["platforms"][MAC]["url"]
    assert admin.post(API + "/leave", headers=auth, json={}).status_code == 422
    assert admin.post(API + "/leave", headers=auth).status_code == 204
    assert admin.post(API + "/leave", headers=auth).status_code == 401
    revoked = check(admin, auth)
    assert revoked.status_code == 401 and CHECK_STATUS not in revoked.headers
    assert report(admin, auth, release="0.3.7").status_code == 409
    assert admin.get(third, headers=auth).status_code == 401
    record = state(admin)["devices"][0]
    assert (record["active"], record["target_version"], record["status"]) == (False, None, "disabled")

    replacement, replacement_auth = enroll(admin)
    assert assign(admin, replacement).status_code == 204
    url = check(admin, replacement_auth).json()["platforms"][MAC]["url"]
    assert admin.delete(f"{ADMIN}/devices/{replacement}").status_code == 204
    assert admin.get(url, headers=replacement_auth).status_code == 401
    assert report(admin, replacement_auth).status_code == 409


@pytest.mark.parametrize("stale", ["missing_candidate", "platform_removed", "legacy_target"])
def test_check_unavailable_preserves_assignment_and_issues_no_ticket(sandbox, admin, stale):
    _, root, _ = sandbox
    stage(root, platforms=(MAC, WINDOWS))
    stage(root, "0.3.7")
    device, auth = enroll(admin)
    assert assign(admin, device).status_code == 204

    database = root / "rollout.sqlite3"
    manifest = root / "releases" / "0.3.6" / "release.json"
    target = "0.3.6"
    if stale == "missing_candidate":
        manifest.unlink()
    elif stale == "platform_removed":
        doc = json.loads(manifest.read_bytes())
        del doc["platforms"][MAC]
        manifest.write_text(json.dumps(doc))
    else:
        target = "legacy-version"
        with sqlite3.connect(database) as conn:
            conn.execute("UPDATE devices SET target_version = ? WHERE id = ?", (target, device))
    no_offer(check(admin, auth), "unavailable")
    record = state(admin)["devices"][0]
    assert (record["target_version"], record["status"]) == (target, "assigned")
    with sqlite3.connect(database) as conn:
        assert conn.execute("SELECT COUNT(*) FROM tickets").fetchone()[0] == 0


def test_enrollment_expiration_ticket_expiration_and_restart(sandbox, admin, password_hash):
    _, root, clock = sandbox
    expired = invite(admin)
    clock[0] += rollout.ENROLLMENT_SECONDS
    response = admin.post(API + "/enroll", json={
        "code": expired, "name": "Fixture device", "platform": MAC, "version": "0.3.4",
    })
    assert response.status_code == 401
    stage(root)
    device, auth = enroll(admin, code=invite(admin).lower())
    assert assign(admin, device).status_code == 204
    url = check(admin, auth).json()["platforms"][MAC]["url"]
    clock[0] += rollout.TICKET_SECONDS
    assert admin.get(url, headers=auth).status_code == 404

    replacement = TestClient(rollout.create_app(root, password_hash, clock=lambda: clock[0]), base_url=ORIGIN)
    assert check(replacement, auth).status_code == 200
    assert replacement.get(ADMIN + "/state").status_code == 401
    raw_bearer = auth["Authorization"].split()[1]
    with sqlite3.connect(root / "rollout.sqlite3") as conn:
        token_hash = conn.execute("SELECT token_hash FROM devices").fetchone()[0]
    assert token_hash == hashlib.sha256(raw_bearer.encode()).digest()


def test_only_one_client_can_use_an_invitation_even_across_concurrent_requests(sandbox, admin):
    app, _, _ = sandbox
    code = invite(admin)

    def redeem(_index):
        browser = TestClient(app, base_url=ORIGIN)
        response = browser.post(API + "/enroll", json={
            "code": code, "name": "Test device", "platform": MAC, "version": "0.3.4",
        })
        return response.status_code

    with ThreadPoolExecutor(max_workers=2) as workers:
        assert sorted(workers.map(redeem, range(2))) == [200, 401]
    assert len(state(admin)["devices"]) == 1


def test_batch_assignments_validate_every_device_and_do_not_partially_commit(sandbox, admin):
    _, root, _ = sandbox
    stage(root)
    stage(root, "0.3.7", platforms=(MAC, WINDOWS))
    mac, _ = enroll(admin)
    windows, _ = enroll(admin, platform=WINDOWS)

    def bulk(ids, version):
        return admin.put(ADMIN + "/assignments", json={"device_ids": ids, "version": version})

    assert bulk([mac, windows], "0.3.6").status_code == 409
    assert bulk([mac, "0" * 32], "0.3.7").status_code == 404
    assert bulk([mac, mac], "0.3.7").status_code == 422
    assert {row["target_version"] for row in state(admin)["devices"]} == {None}

    assert bulk([mac, windows], "0.3.7").status_code == 204
    assert {row["target_version"] for row in state(admin)["devices"]} == {"0.3.7"}
    assert bulk([mac, windows], None).status_code == 204
    assert {row["target_version"] for row in state(admin)["devices"]} == {None}

    assert admin.delete(f"{ADMIN}/devices/{windows}").status_code == 204
    assert bulk([mac, windows], "0.3.7").status_code == 409
    assert {row["target_version"] for row in state(admin)["devices"]} == {None}


def test_admin_actions_require_own_origin_secure_session_and_json(sandbox, admin):
    app, _, clock = sandbox
    unauthenticated = TestClient(app, base_url=ORIGIN)
    login = ADMIN + "/login"
    assert unauthenticated.post(login, json={"password": PASSWORD}).status_code == 403
    assert unauthenticated.post(login, headers={"Origin": "https://other.example"}, json={"password": PASSWORD}).status_code == 403
    assert unauthenticated.post(login, headers=SAME_ORIGIN, content="password").status_code == 415
    assert unauthenticated.post(login, headers=SAME_ORIGIN, json={"password": "a" * 5000}).status_code == 413
    invalid = unauthenticated.post(login, headers=SAME_ORIGIN, json={"password": {"test": "do not reflect"}})
    assert invalid.status_code == 422 and invalid.json() == {"detail": "invalid_request"}
    assert unauthenticated.post(ADMIN + "/enrollments", headers=SAME_ORIGIN, json={}).status_code == 401
    assert admin.post(ADMIN + "/enrollments", headers={"Origin": "https://other.example"}, json={}).status_code == 403
    assert unauthenticated.get(API + "/latest.json").status_code == 404

    success = unauthenticated.post(login, headers=SAME_ORIGIN, json={"password": PASSWORD})
    cookie = success.headers["set-cookie"].lower()
    for property in ("secure", "httponly", "samesite=strict", f"path={ADMIN}"):
        assert property in cookie
    assert success.headers["Cache-Control"] == "no-store"
    assert success.headers["Referrer-Policy"] == "no-referrer"
    assert "frame-ancestors 'none'" in success.headers["Content-Security-Policy"]
    assert unauthenticated.get(ADMIN + "/state").status_code == 200
    assert unauthenticated.post(ADMIN + "/logout", headers=SAME_ORIGIN, json={}).status_code == 204
    assert unauthenticated.get(ADMIN + "/state").status_code == 401
    clock[0] += rollout.SESSION_SECONDS
    assert admin.get(ADMIN + "/state").status_code == 401


def test_management_page_and_assets_require_a_live_admin_session(sandbox, admin, password_hash):
    app, root, clock = sandbox
    anonymous = TestClient(app, base_url=ORIGIN)
    manager_paths = (MANAGER + "/", MANAGER + "/style.css", MANAGER + "/app.js")
    for path in (*manager_paths, MANAGER, MANAGER + "/index.html"):
        response = anonymous.get(path, follow_redirects=False)
        assert response.status_code == 404 and response.json() == {"detail": "Not Found"}
        assert response.headers["cache-control"] == "no-store"
        assert "location" not in response.headers
        assert anonymous.head(path).status_code == 404
        forged = anonymous.get(path, headers={"Cookie": f"{rollout.ADMIN_COOKIE}={'a' * 43}"})
        assert forged.status_code == 404

    sign_in = anonymous.get(SIGN_IN)
    assert sign_in.status_code == 200 and sign_in.headers["cache-control"] == "no-store"
    assert sign_in.headers["referrer-policy"] == "same-origin"
    assert '<title>登录</title>' in sign_in.text
    assert 'method="post"' in sign_in.text and 'type="password"' in sign_in.text
    assert all(word not in sign_in.text for word in ("灰度", "VortoOne", "app.js", API, "vortoone-update:"))
    style = anonymous.get(SIGN_IN + ".css")
    assert style.status_code == 200 and style.headers["cache-control"] == "no-store"
    assert "text/css" in style.headers["content-type"]

    login = anonymous.post(SIGN_IN, headers=SAME_ORIGIN, data={"password": PASSWORD}, follow_redirects=False)
    assert login.status_code == 303 and login.headers["location"] == MANAGER + "/"
    assert login.headers["cache-control"] == "no-store"
    cookies = login.headers.get_list("set-cookie")
    assert len(cookies) == 2
    for cookie in cookies:
        for attribute in ("Secure", "HttpOnly", "SameSite=strict"):
            assert attribute in cookie
        assert "Domain=" not in cookie
    assert {cookie.split("Path=", 1)[1].split(";", 1)[0] for cookie in cookies} == {MANAGER, ADMIN}

    # The page and the API can be reached through different proxies to the same state.
    second = TestClient(rollout.create_app(root, password_hash, clock=lambda: clock[0]), base_url=ORIGIN)
    second.cookies.update(anonymous.cookies)
    for path in manager_paths:
        response = second.get(path)
        assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
        assert second.head(path).status_code == 200
    assert "vortoone-update://enroll" in second.get(MANAGER + "/").text
    assert second.get(ADMIN + "/state").status_code == 200
    assert second.get(SIGN_IN, follow_redirects=False).headers["location"] == MANAGER + "/"
    assert "会话已过期，请重新登录。" in second.get(SIGN_IN + "?expired=1").text

    logout = second.post(ADMIN + "/logout", headers=SAME_ORIGIN, json={})
    assert logout.status_code == 204 and len(logout.headers.get_list("set-cookie")) == 2
    for path in manager_paths:
        assert anonymous.get(path).status_code == 404  # Original cookies still exist on this client.
    assert anonymous.get(ADMIN + "/state").status_code == 401

    clock[0] += rollout.SESSION_SECONDS
    for path in manager_paths:
        assert admin.get(path).status_code == 404
    assert admin.get(ADMIN + "/state").status_code == 401


def test_sign_in_form_uses_the_existing_origin_guard_and_rate_limit(sandbox):
    app, _, _ = sandbox
    client = TestClient(app, base_url=ORIGIN)
    credentials = {"password": PASSWORD}
    assert client.post(SIGN_IN, data=credentials).status_code == 403
    assert client.post(SIGN_IN, headers={"Origin": "https://other.example"}, data=credentials).status_code == 403
    assert client.post(SIGN_IN, headers=SAME_ORIGIN, json=credentials).status_code == 415
    assert client.post(SIGN_IN, headers=SAME_ORIGIN, data={"password": "x" * rollout.FORM_LIMIT}).status_code == 413
    invalid = client.post(SIGN_IN, headers=SAME_ORIGIN, data={"wrong": "unused"})
    assert invalid.status_code == 422 and "unused" not in invalid.text
    secret = "incorrect synthetic password"
    for _ in range(8):
        rejected = client.post(SIGN_IN, headers=SAME_ORIGIN, data={"password": secret})
        assert rejected.status_code == 401 and rejected.headers["cache-control"] == "no-store"
        assert secret not in rejected.text and "灰度" not in rejected.text
    assert client.post(ADMIN + "/login", headers=SAME_ORIGIN, json=credentials).status_code == 429


def test_rate_limiting_uses_the_central_clock_and_does_not_echo_credentials(sandbox):
    app, _, clock = sandbox
    client = TestClient(app, base_url=ORIGIN, headers=SAME_ORIGIN)
    for _ in range(8):
        invalid = client.post(ADMIN + "/login", json={"password": "synthetic incorrect password"})
        assert invalid.status_code == 401
        assert "synthetic incorrect password" not in invalid.text
    limited = client.post(ADMIN + "/login", json={"password": PASSWORD})
    assert limited.status_code == 429 and limited.headers["Retry-After"] == "60"
    clock[0] += 60
    assert client.post(ADMIN + "/login", json={"password": PASSWORD}).status_code == 204


def test_malformed_or_linked_artifacts_are_never_available(sandbox, admin):
    _, root, _ = sandbox
    stage(root, "0.3.5")
    stage(root, "0.3.6")
    stage(root, "0.3.7")
    stage(root, "0.3.8")
    stage(root, "0.3.9")
    good = root / "releases" / "0.3.5"
    linked = root / "releases" / "0.3.6" / "VortoOne_0.3.6_aarch64.app.tar.gz"
    linked.unlink()
    linked.symlink_to(good / "VortoOne_0.3.5_aarch64.app.tar.gz")
    wrong_platform = root / "releases" / "0.3.7" / "release.json"
    doc = json.loads(wrong_platform.read_bytes())
    doc["platforms"] = {"darwin-x86_64": doc["platforms"][MAC]}
    wrong_platform.write_text(json.dumps(doc))
    wrong_file = root / "releases" / "0.3.8" / "release.json"
    doc = json.loads(wrong_file.read_bytes())
    doc["platforms"][MAC]["filename"] = "unrecognized.tar.gz"
    wrong_file.write_text(json.dumps(doc))
    oversized = root / "releases" / "0.3.9" / "VortoOne_0.3.9_aarch64.app.tar.gz"
    with oversized.open("r+b") as artifact:
        artifact.truncate(rollout.ARTIFACT_LIMIT + 1)
    assert [release["version"] for release in state(admin)["releases"]] == ["0.3.5"]
    device, auth = enroll(admin)
    assert assign(admin, device, "0.3.6").status_code == 404
    assert assign(admin, device, "0.3.7").status_code == 404
    assert assign(admin, device, "0.3.8").status_code == 404
    assert check(admin, auth).status_code == 204
