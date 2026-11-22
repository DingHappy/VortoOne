"""Drive trash keeps original versions and cannot expose the owner's files to members."""
from datetime import datetime, timedelta, timezone
from pathlib import Path
import shutil

from fastapi.testclient import TestClient

from src.agents.tools import artifact_trash
from src.agents.tools.artifact_trash import ArtifactTrash
from src.agents.tools.artifacts import ArtifactStore
from src.utils.home_maintenance import home_maintenance
from src.web.auth import SESSION_COOKIE
from tests.unit.test_member_login import ORIGIN, TOKEN, _group, _join, home  # noqa: F401


TRASHED_AT = datetime(2026, 1, 7, 17, 34, tzinfo=timezone.utc)


def test_owner_moves_and_restores_an_existing_artifact_with_its_versions(home, monkeypatch):
    monkeypatch.setenv("VORTOONE_WORKSPACE_SCOPE", "general")
    monkeypatch.setattr(artifact_trash, "_now", lambda: TRASHED_AT)
    owner = TestClient(home["app"], headers=ORIGIN)
    owner.cookies.set(SESSION_COOKIE, TOKEN)
    base = home["base"]
    store = ArtifactStore(home["root"])
    store.publish("Weekly report", "<p>v1</p>", "weekly")
    store.publish("Weekly report", "<p>v2</p>", "weekly")
    pinned = store.pin("weekly", 1)
    assert pinned["version"] == 2 and pinned["pinned"] == 1

    assert owner.get(f"{base}/artifacts-trash").json() == {"files": []}
    assert owner.delete(f"{base}/artifacts/weekly").status_code == 204
    assert owner.get(f"{base}/artifacts").json()["artifacts"] == []
    assert owner.get(f"{base}/artifacts/weekly").status_code == 404
    assert ArtifactStore(home["root"]).html("weekly", 1) is None
    response = owner.get(f"{base}/artifacts-trash")
    assert response.status_code == 200 and response.headers["Cache-Control"] == "no-store"
    [file] = response.json()["files"]
    assert (file["id"], file["kind"], file["title"], file["version"]) == ("weekly", "html", "Weekly report", 1)
    assert datetime.fromisoformat(file["deletedAt"]) == TRASHED_AT
    assert datetime.fromisoformat(file["purgeAt"]) == TRASHED_AT + timedelta(days=30)
    assert "content" not in file and file["untrusted"] is True

    restored = owner.post(f"{base}/artifacts/weekly/restore", json={})
    assert restored.status_code == 204 and not restored.content
    assert owner.get(f"{base}/artifacts-trash").json() == {"files": []}
    assert owner.get(f"{base}/artifacts/weekly").json()["content"]["html"] == "<p>v1</p>"
    assert owner.get(f"{base}/artifacts/weekly", params={"version": 2}).json()["content"]["html"] == "<p>v2</p>"
    assert ArtifactStore(home["root"]).meta("weekly") == pinned
    assert owner.post(f"{base}/artifacts/weekly/restore", json={}).status_code == 404


def test_owner_purges_only_trashed_artifacts_and_cannot_undo_afterward(home):
    owner, base = home["owner"], home["base"]
    store = ArtifactStore(home["root"])
    store.publish("Report", "<p>v1</p>", "report")
    store.publish("Report", "<p>v2</p>", "report")
    purge = f"{base}/artifacts-trash/report"
    restore = f"{base}/artifacts/report/restore"
    assert owner.delete(purge).status_code == 404
    assert owner.post(restore, json={}).status_code == 404
    assert store.html("report") == "<p>v2</p>"
    assert owner.delete(f"{base}/artifacts/report").status_code == 204
    assert owner.delete(purge).status_code == 204
    assert owner.get(f"{base}/artifacts-trash").json() == {"files": []}
    assert not list((store.root.parent / "artifact-trash").rglob("*.html"))
    assert owner.delete(purge).status_code == 404
    rejected = owner.post(restore, json={})
    assert rejected.status_code == 410 and rejected.json()["error"]["code"] == "artifact.permanently_deleted"


def test_expiry_is_exact_and_reclaims_both_versions_on_next_trash_visit(home, monkeypatch):
    owner, base = home["owner"], home["base"]
    store = ArtifactStore(home["root"])
    store.publish("Report", "<p>v1</p>", "report")
    store.publish("Report", "<p>v2</p>", "report")
    monkeypatch.setattr(artifact_trash, "_now", lambda: TRASHED_AT)
    assert owner.delete(f"{base}/artifacts/report").status_code == 204
    expiry = TRASHED_AT + timedelta(days=30)
    monkeypatch.setattr(artifact_trash, "_now", lambda: expiry - timedelta(seconds=1))
    assert owner.get(f"{base}/artifacts-trash").json()["files"][0]["id"] == "report"
    monkeypatch.setattr(artifact_trash, "_now", lambda: expiry)
    assert owner.get(f"{base}/artifacts-trash").json() == {"files": []}
    assert not list((store.root.parent / "artifact-trash").rglob("*.html"))
    response = owner.post(f"{base}/artifacts/report/restore", json={})
    assert response.status_code == 409 and response.json()["error"]["code"] == "artifact.purged"


def test_member_always_sees_an_empty_trash_and_cannot_mutate_even_with_workspace_role(home):
    owner, base = home["owner"], home["base"]
    member = _join(home, roleIds=[_group(home, "Desktop admins", ["configure", "upload_files"])])
    ArtifactStore(home["root"]).publish("Owner material", "<p>private</p>", "owner-note")
    assert owner.delete(f"{base}/artifacts/owner-note").status_code == 204
    assert member.get(f"{base}/artifacts-trash").json() == {"files": []}
    assert member.post(f"{base}/artifacts/owner-note/restore", json={}).status_code == 403
    assert member.delete(f"{base}/artifacts-trash/owner-note").status_code == 403
    assert member.delete(f"{base}/artifacts/owner-note").status_code == 403
    guest = TestClient(home["app"])
    assert guest.get(f"{base}/artifacts-trash").status_code == 401
    foreign = "/api/app/organizations/other"
    assert owner.get(f"{foreign}/artifacts-trash").status_code == 404
    assert owner.post(f"{foreign}/artifacts/owner-note/restore", json={}).status_code == 404
    assert owner.delete(f"{foreign}/artifacts-trash/owner-note").status_code == 404
    assert owner.get(f"{base}/artifacts-trash").json()["files"][0]["id"] == "owner-note"


def test_off_origin_owner_cookie_cannot_change_trash(home):
    owner, base = home["owner"], home["base"]
    ArtifactStore(home["root"]).publish("Draft", "<p>v1</p>", "draft")
    assert owner.delete(f"{base}/artifacts/draft").status_code == 204
    browser = TestClient(home["app"], headers={"Origin": "http://invalid.test"})
    browser.cookies.set(SESSION_COOKIE, TOKEN)
    assert browser.get(f"{base}/artifacts-trash").status_code == 403
    assert browser.post(f"{base}/artifacts/draft/restore", json={}).status_code == 403
    assert browser.delete(f"{base}/artifacts-trash/draft").status_code == 403
    assert owner.get(f"{base}/artifacts-trash").json()["files"][0]["id"] == "draft"


def test_matching_live_id_never_overwrites_a_different_generation_in_trash(home):
    owner, base = home["owner"], home["base"]
    store = ArtifactStore(home["root"])
    store.publish("Old version", "<p>old</p>", "same-id")
    assert owner.delete(f"{base}/artifacts/same-id").status_code == 204
    store.publish("New version", "<p>new</p>", "same-id")
    refused = owner.post(f"{base}/artifacts/same-id/restore", json={})
    assert refused.status_code == 422 and refused.json()["error"]["code"] == "artifact.restore_id_taken"
    assert owner.delete(f"{base}/artifacts/same-id").status_code == 409
    assert store.html("same-id") == "<p>new</p>"
    assert owner.get(f"{base}/artifacts-trash").json()["files"][0]["title"] == "Old version"


def test_failed_move_leaves_active_file_and_can_be_retried(home, monkeypatch):
    owner, base = home["owner"], home["base"]
    store = ArtifactStore(home["root"])
    store.publish("Report", "<p>v1</p>", "report")

    def fail(*args):
        raise OSError("synthetic rename failure")

    with monkeypatch.context() as failure:
        failure.setattr(Path, "rename", fail)
        assert owner.delete(f"{base}/artifacts/report").status_code == 503
    assert store.html("report") == "<p>v1</p>"
    assert owner.get(f"{base}/artifacts-trash").json() == {"files": []}
    assert owner.delete(f"{base}/artifacts/report").status_code == 204
    assert ArtifactTrash(home["root"]).trashed()[0][0]["id"] == "report"


def test_interrupted_permanent_delete_cannot_be_undone_and_can_finish_on_retry(home, monkeypatch):
    owner, base = home["owner"], home["base"]
    store = ArtifactStore(home["root"])
    store.publish("Report", "<p>v1</p>", "report")
    assert owner.delete(f"{base}/artifacts/report").status_code == 204

    def fail(*args):
        raise OSError("synthetic cleanup failure")

    with monkeypatch.context() as failure:
        failure.setattr(shutil, "rmtree", fail)
        assert owner.delete(f"{base}/artifacts-trash/report").status_code == 503
        rejected = owner.post(f"{base}/artifacts/report/restore", json={})
        assert rejected.status_code == 410 and rejected.json()["error"]["code"] == "artifact.permanently_deleted"
    assert owner.delete(f"{base}/artifacts-trash/report").status_code == 204
    assert owner.get(f"{base}/artifacts-trash").json() == {"files": []}
    assert not list((store.root.parent / "artifact-trash").rglob("*.html"))


def test_interruption_before_the_atomic_move_does_not_mark_the_active_file_as_purged(home, monkeypatch):
    owner, base = home["owner"], home["base"]
    store = ArtifactStore(home["root"])
    store.publish("Report", "<p>v1</p>", "report")
    monkeypatch.setattr(artifact_trash, "_now", lambda: TRASHED_AT)
    (store.root.parent / "artifact-trash" / "report" / str(int(TRASHED_AT.timestamp()))).mkdir(parents=True)
    assert owner.post(f"{base}/artifacts/report/restore", json={}).status_code == 404
    assert store.html("report") == "<p>v1</p>"
    assert owner.delete(f"{base}/artifacts/report").status_code == 204
    assert owner.get(f"{base}/artifacts-trash").json()["files"][0]["id"] == "report"


def test_workspace_maintenance_blocks_trash_moves_and_expired_byte_cleanup(home, monkeypatch):
    owner, base = home["owner"], home["base"]
    store = ArtifactStore(home["root"])
    monkeypatch.setattr(artifact_trash, "_now", lambda: TRASHED_AT)
    store.publish("Expiring report", "<p>expired payload</p>", "expired")
    assert owner.delete(f"{base}/artifacts/expired").status_code == 204
    monkeypatch.setattr(artifact_trash, "_now", lambda: TRASHED_AT + timedelta(days=30))
    store.publish("Recent report", "<p>recent payload</p>", "recent")
    assert owner.delete(f"{base}/artifacts/recent").status_code == 204
    store.publish("Active report", "<p>active payload</p>", "active")
    trash_root = store.root.parent / "artifact-trash"
    before = {path.relative_to(trash_root): path.read_bytes() for path in trash_root.rglob("*.html")}
    assert len(before) == 4

    with home_maintenance(home["root"]):
        responses = [
            owner.delete(f"{base}/artifacts/active"),
            owner.post(f"{base}/artifacts/recent/restore", json={}),
            owner.delete(f"{base}/artifacts-trash/recent"),
            owner.get(f"{base}/artifacts-trash"),
        ]
        assert [(response.status_code, response.json()["error"]["code"]) for response in responses] == [
            (423, "unavailable"), (423, "unavailable"), (423, "unavailable"), (423, "unavailable"),
        ]
        assert {path.relative_to(trash_root): path.read_bytes() for path in trash_root.rglob("*.html")} == before
        assert store.html("active") == "<p>active payload</p>"
        assert store.html("recent") is None

    listing = owner.get(f"{base}/artifacts-trash")
    assert listing.status_code == 200
    assert [row["id"] for row in listing.json()["files"]] == ["recent"]
    assert not list((trash_root / "expired").rglob("*.html"))
    assert owner.post(f"{base}/artifacts/recent/restore", json={}).status_code == 204
    assert owner.delete(f"{base}/artifacts/active").status_code == 204
    assert owner.delete(f"{base}/artifacts-trash/active").status_code == 204


def test_failed_empty_directory_cleanup_does_not_fail_committed_restore(home, monkeypatch):
    owner, base = home["owner"], home["base"]
    store = ArtifactStore(home["root"])
    store.publish("Report", "<p>v1</p>", "report")
    monkeypatch.setattr(artifact_trash, "_now", lambda: TRASHED_AT)
    assert owner.delete(f"{base}/artifacts/report").status_code == 204
    marker = store.root.parent / "artifact-trash" / "report" / str(int(TRASHED_AT.timestamp()))
    original_rmdir = Path.rmdir

    def fail_to_remove_marker(path):
        if path == marker:
            raise OSError("synthetic cleanup failure")
        return original_rmdir(path)

    with monkeypatch.context() as failure:
        failure.setattr(Path, "rmdir", fail_to_remove_marker)
        assert owner.post(f"{base}/artifacts/report/restore", json={}).status_code == 204
    assert marker.is_dir()
    assert store.html("report") == "<p>v1</p>"
    assert owner.get(f"{base}/artifacts-trash").json() == {"files": []}
    assert owner.post(f"{base}/artifacts/report/restore", json={}).status_code == 404
    assert owner.delete(f"{base}/artifacts/report").status_code == 204
