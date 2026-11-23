"""Create a private Drive HTML artifact using the client's JSON contract."""

from fastapi.testclient import TestClient
import pytest

from src.agents.tools.artifacts import ArtifactStore
from src.utils.home_maintenance import home_maintenance
from tests.unit.test_member_login import ORIGIN, TOKEN, _join
from tests.unit.test_member_login import home  # noqa: F401


HTML = "<!doctype html>\n<h1>桌面制品</h1>"


def _payload(**fields):
    return {"title": " 私有页面 ", "content": {"kind": "html", "html": HTML}, **fields}


def test_owner_creates_existing_drive_format_from_json(home):
    owner, root = home["owner"], home["root"]
    path = f"{home['base']}/artifacts"

    created = owner.post(path, json=_payload(channelId=None))

    assert created.status_code == 201
    assert created.headers["cache-control"] == "no-store"
    detail = created.json()
    artifact_id = detail["id"]
    assert detail["title"] == "私有页面"
    assert detail["kind"] == "html"
    assert detail["version"] == detail["shownVersion"] == 1
    assert detail["content"] == {"kind": "html", "html": HTML}
    assert detail["createdBy"] == detail["updatedBy"] == {
        "type": "person",
        "id": owner.get("/api/app/me").json()["user"]["id"],
    }
    assert detail["channelId"] is None and detail["threadId"] is None
    assert detail["untrusted"] is True and detail["canShare"] is False
    assert detail["canEdit"] is False and detail["versioned"] is False
    assert owner.get(f"{path}/{artifact_id}").json() == detail
    assert [row["id"] for row in owner.get(path).json()["artifacts"]] == [artifact_id]
    assert ArtifactStore(root, desktop_uploads=True).html(artifact_id) == HTML
    assert ArtifactStore(root).html(artifact_id) is None
    assert owner.get(f"/artifact/{artifact_id}/raw").status_code == 404
    member = _join(home)
    assert member.get(path).json()["artifacts"] == []
    assert member.get(f"{path}/{artifact_id}").status_code == 403


def test_creation_requires_authenticated_owner_with_write_proof(home, monkeypatch):
    path = f"{home['base']}/artifacts"
    owner = home["owner"]
    member = _join(home)
    browser = TestClient(home["app"])
    assert member.post(path, json=_payload()).status_code == 403
    assert browser.post(path, json=_payload()).status_code == 401
    assert browser.post("/api/auth/login", json={"token": TOKEN}).status_code == 200
    assert browser.post(path, json=_payload(), headers={"Origin": "https://other.example"}).status_code == 403
    assert browser.post(path, json=_payload()).status_code == 403
    assert owner.post("/api/app/organizations/other/artifacts", json=_payload()).status_code == 404
    assert owner.get(path).json()["artifacts"] == []
    assert browser.post(path, json=_payload(), headers=ORIGIN).status_code == 201
    monkeypatch.delenv("VORTOONE_API_TOKEN")
    assert TestClient(home["app"]).post(path, json=_payload()).status_code == 403


@pytest.mark.parametrize(
    ("payload", "status", "code"),
    [
        ({"title": "No content"}, 400, "invalid"),
        (_payload(title=" "), 400, "invalid"),
        (_payload(title="a" * 201), 400, "invalid"),
        (_payload(title="a\nb"), 400, "invalid"),
        (_payload(content={"kind": "html", "html": ""}), 400, "invalid"),
        (_payload(content={"kind": "html", "html": "invalid\x00"}), 400, "invalid"),
        (_payload(content={"kind": "html", "html": 101}), 400, "invalid"),
        (_payload(content={"kind": "html", "html": HTML, "extra": True}), 400, "invalid"),
        (_payload(content={"kind": "document", "markdown": "Some content"}), 415, "artifact.upload_format"),
        (_payload(channelId="any-channel"), 422, "artifact.upload_location"),
        (_payload(sharing={"mode": "workspace"}), 400, "invalid"),
    ],
)
def test_unsupported_creation_does_not_publish(home, payload, status, code):
    path = f"{home['base']}/artifacts"
    denied = home["owner"].post(path, json=payload)

    assert denied.status_code == status
    assert denied.json()["error"]["code"] == code
    assert home["owner"].get(path).json()["artifacts"] == []


def test_invalid_json_non_utf8_surrogates_and_size_limits_fail_before_publish(home, monkeypatch):
    owner, root = home["owner"], home["root"]
    path = f"{home['base']}/artifacts"
    headers = {"Content-Type": "application/json"}
    assert owner.post(path, content=b"<p>not JSON</p>").status_code == 415
    assert owner.post(path, content=b'{"title":"\xff"}', headers=headers).status_code == 400
    assert owner.post(path, content=b'{not JSON}', headers=headers).status_code == 400
    invalid_scalar = (
        rb'{"title":"invalid\ud800","content":{"kind":"html","html":"<p>example</p>"}}'
    )
    assert owner.post(path, content=invalid_scalar, headers=headers).status_code == 400
    monkeypatch.setenv("VORTOONE_ARTIFACT_MAX_BYTES", "32")
    too_large = _payload(content={"kind": "html", "html": "大" * 11})
    assert owner.post(path, json=too_large).status_code == 413
    assert owner.post(path, content=iter([b" " * 9_000, b" " * 9_000]), headers=headers).status_code == 413
    assert not ArtifactStore(root, desktop_uploads=True).root.exists()


def test_failed_or_busy_creation_leaves_drive_empty(home, monkeypatch):
    def fail(directory, *_args):
        directory.mkdir()
        (directory / "index.html").write_text("partially written", encoding="utf-8")
        raise OSError("private fixture path")

    owner, root = home["owner"], home["root"]
    path = f"{home['base']}/artifacts"
    with home_maintenance(root):
        assert owner.post(path, json=_payload()).status_code == 409

    monkeypatch.setattr(ArtifactStore, "_publish_at", staticmethod(fail))
    rejected = owner.post(path, json=_payload())
    assert rejected.status_code == 503
    assert "private fixture path" not in rejected.text
    assert list(ArtifactStore(root, desktop_uploads=True).root.iterdir()) == []
    assert owner.get(path).json()["artifacts"] == []
