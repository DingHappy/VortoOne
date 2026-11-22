"""成员邀请：全部列表（含过期）、批量邀请、改邀请给的身份组、成员「换角色」。规矩在成员登记处。"""

import pytest

from tests.unit.test_member_login import ORIGIN, _group, _invite, _join
from tests.unit.test_member_login import home  # noqa: F401 —— 成员登录的整套夹具


def test_a_batch_invites_each_address_once(home):
    owner, base = home["owner"], home["base"]
    _join(home, email="old@example.com")
    group = _group(home, "财务", ["view_usage"])
    answer = owner.post(f"{base}/invitations/batch", json={
        "emails": ["A@example.com", "old@example.com", "not-an-email", "a@example.com"],
        "invites": [{"email": "b@example.com", "roleIds": [group]}]}, headers=ORIGIN)
    assert answer.status_code == 200, answer.text
    results = [(row["email"], row["status"]) for row in answer.json()["results"]]
    assert results == [("a@example.com", "invited"), ("old@example.com", "already_member"),
                       ("not-an-email", "invalid_email"), ("a@example.com", "already_invited"),
                       ("b@example.com", "invited")]
    rows = {row["email"]: row for row in owner.get(f"{base}/invitations").json()["invitations"]}
    assert set(rows) == {"a@example.com", "b@example.com"} and rows["b@example.com"]["roleIds"] == [group]
    links = [row["url"] for row in answer.json()["results"] if row["status"] == "invited"]
    assert all(link.startswith("http://testserver/invite/") for link in links)


@pytest.mark.parametrize("body", [
    {},
    {"emails": ["a@example.com"], "channelIds": ["c1"]},
    {"emails": ["a@example.com"], "agentIds": ["x"]},
    {"emails": [f"p{i}@example.com" for i in range(51)]},
    {"emails": ["a@example.com"], "sendMail": True},
])
def test_bad_batches_are_refused(home, body):
    answer = home["owner"].post(f"{home['base']}/invitations/batch", json=body, headers=ORIGIN)
    assert answer.status_code == 400
    assert home["owner"].get(f"{home['base']}/invitations").json()["invitations"] == []


def test_a_member_without_invite_rights_cannot_batch(home):
    member = _join(home)
    answer = member.post(f"{home['base']}/invitations/batch", json={"emails": ["x@example.com"]})
    assert answer.status_code == 403


def test_a_member_with_invite_rights_can_batch_but_not_grant_more_than_they_hold(home):
    inviter = _group(home, "招募", ["invite_people"])
    admin = _group(home, "管理", ["administrator"])
    member = _join(home, roleIds=[inviter])
    ok = member.post(f"{home['base']}/invitations/batch", json={"emails": ["x@example.com"]})
    assert ok.status_code == 200 and ok.json()["results"][0]["status"] == "invited"
    over = member.post(f"{home['base']}/invitations/batch", json={"emails": ["y@example.com"], "roleIds": [admin]})
    assert over.status_code == 403


def test_the_full_list_includes_expired_invitations(home, monkeypatch):
    from src.workflow.members.registry import MemberRegistry
    owner, base = home["owner"], home["base"]
    _invite(home, "live@example.com")
    _invite(home, "late@example.com")
    registry = MemberRegistry(home["root"])
    with registry._change() as state:
        late = next(key for key, item in state["invitations"].items() if item["email"] == "late@example.com")
        state["invitations"][late]["expires"] = "2000-01-01T00:00:00+00:00"
    rows = {row["email"]: row["status"] for row in owner.get(f"{base}/invitations/all").json()["invitations"]}
    assert rows == {"live@example.com": "pending", "late@example.com": "expired"}
    assert [row["email"] for row in owner.get(f"{base}/invitations").json()["invitations"]] == ["live@example.com"]
    assert all("url" not in row for row in owner.get(f"{base}/invitations/all").json()["invitations"])
    plain = _join(home, email="plain@example.com")
    assert plain.get(f"{base}/invitations/all").status_code == 403


def test_changing_what_an_invitation_gives(home):
    owner, base = home["owner"], home["base"]
    group = _group(home, "财务", ["view_usage"])
    token = _invite(home, "a@example.com")
    [row] = owner.get(f"{base}/invitations").json()["invitations"]
    changed = owner.patch(f"{base}/invitations/{row['id']}", json={"roleIds": [group]}, headers=ORIGIN)
    assert changed.status_code == 200 and changed.json() == {"roleIds": [group], "channelIds": [], "agentIds": []}
    assert owner.get(f"{base}/invitations").json()["invitations"][0]["roleIds"] == [group]
    # 同一个链接照样能用，加入后带着新的身份组
    from tests.unit.test_member_login import PASSWORD
    from fastapi.testclient import TestClient
    browser = TestClient(home["app"], headers=ORIGIN)
    browser.post("/api/auth/sign-up/email", json={"name": "小王", "email": "a@example.com", "password": PASSWORD},
                 headers={"x-invitation-token": token})
    assert browser.post(f"/api/app/invitations/{token}/accept", json={}).status_code == 200
    me = browser.get("/api/app/me").json()
    assert "view_usage" in me["organizations"][0]["permissions"]
    assert owner.patch(f"{base}/invitations/{row['id']}", json={"roleIds": []}, headers=ORIGIN).status_code == 404


def test_invitation_edits_are_checked(home):
    owner, base = home["owner"], home["base"]
    _invite(home, "a@example.com")
    [row] = owner.get(f"{base}/invitations").json()["invitations"]
    url = f"{base}/invitations/{row['id']}"
    assert owner.patch(url, json={"channelIds": ["c1"]}, headers=ORIGIN).status_code == 400
    assert owner.patch(url, json={"roleIds": "all"}, headers=ORIGIN).status_code == 400
    assert owner.patch(url, json={"roleIds": ["no-such-group"]}, headers=ORIGIN).status_code in (400, 403)
    assert owner.patch(f"{base}/invitations/i_nope", json={"roleIds": []}, headers=ORIGIN).status_code == 404
    member = _join(home, email="m@example.com")
    assert member.patch(url, json={"roleIds": []}).status_code == 403        # 不是他发的、他也不管成员


def test_member_roles_are_identity_groups_not_two_tiers(home):
    owner, base = home["owner"], home["base"]
    member = _join(home)
    member_id = member.get("/api/app/me").json()["user"]["id"]
    assert owner.patch(f"{base}/members/{member_id}", json={"role": "member"}).status_code == 204
    answer = owner.patch(f"{base}/members/{member_id}", json={"role": "admin"})
    assert answer.status_code == 400 and "身份组" in answer.json()["error"]["message"]
    assert owner.patch(f"{base}/members/nobody", json={"role": "member"}).status_code == 404
    assert member.patch(f"{base}/members/{member_id}", json={"role": "member"}).status_code == 403
