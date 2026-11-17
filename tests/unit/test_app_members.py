"""界面的 roles / members 接口：把写法对到成员登记处，规矩仍在登记处与内核。"""

import pytest
from fastapi.testclient import TestClient

from src.agents.principal import OWNER, Principal
from src.workflow.members.registry import MemberRegistry

TOKEN = "owner-token-for-test"


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    for name in ("VORTOCODE_API_TOKEN", "AUTODEV_API_TOKEN", "OPENAI_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("VORTOONE_API_TOKEN", TOKEN)
    from src.web.server import app
    client = TestClient(app)
    client.headers["Authorization"] = f"Bearer {TOKEN}"
    org = client.get("/api/app/me").json()["organizations"][0]["id"]
    return {"root": str(tmp_path), "client": client, "base": f"/api/app/organizations/{org}"}


def _join(root, name="小王", groups=()):
    registry = MemberRegistry(root)
    _, token = registry.invite(Principal(OWNER, "owner"), groups=groups)
    return registry.accept_invitation(token, name)


def _roles(home):
    return {role["id"]: role for role in home["client"].get(f"{home['base']}/roles").json()["roles"]}


def test_a_new_space_lists_the_built_in_groups_in_order(home):
    body = home["client"].get(f"{home['base']}/roles").json()
    assert [(role["id"], role["kind"], role["position"]) for role in body["roles"]] == [
        ("admins", "custom", 0), ("everyone", "everyone", 1)]
    admins, everyone = body["roles"]
    assert admins["permissions"] == ["administrator", "control_computers"]
    assert "talk_to_agents" in everyone["permissions"] and "own_browsers" in everyone["permissions"]
    assert everyone["people"] == 1 and not everyone["givable"]
    assert {area["id"] for area in body["areas"]} == {"chat", "agents", "tools", "knowledge", "org"}


def test_every_key_the_screen_offers_is_understood(home):
    from src.web import app_members
    offered = {key for area in app_members.AREAS for key in area["keys"]} | {"administrator"}
    created = home["client"].post(f"{home['base']}/roles", json={"name": "全开", "permissions": sorted(offered)})
    assert created.status_code == 200
    got = set(_roles(home)[created.json()["id"]]["permissions"])
    # 四个电脑键是一条阶梯，只留最高的一级
    assert got == offered - {"own_browsers", "own_computers", "control_browsers"}


def test_create_change_reorder_delete(home):
    client, base = home["client"], home["base"]
    made = client.post(f"{base}/roles", json={"name": "财务", "icon": "wallet", "color": "amber",
                                              "permissions": ["view_usage", "own_computers"]}).json()["id"]
    role = _roles(home)[made]
    assert (role["name"], role["icon"], role["color"], role["position"]) == ("财务", "wallet", "amber", 1)
    assert role["permissions"] == ["view_usage", "own_computers"]

    assert client.patch(f"{base}/roles/{made}", json={"name": "财务组", "permissions": ["view_audit"],
                                                      "hoist": True}).status_code == 204
    role = _roles(home)[made]
    assert (role["name"], role["permissions"], role["hoist"], role["icon"]) == ("财务组", ["view_audit"], True, "wallet")

    assert client.put(f"{base}/roles/order", json={"ids": [made, "admins", "everyone"]}).status_code == 204
    assert [role["id"] for role in client.get(f"{base}/roles").json()["roles"]] == [made, "admins", "everyone"]
    assert client.put(f"{base}/roles/order", json={"ids": ["everyone", made, "admins"]}).status_code == 403

    assert client.delete(f"{base}/roles/{made}").status_code == 204
    assert made not in _roles(home)


def test_a_copy_takes_what_the_request_leaves_out(home):
    client, base = home["client"], home["base"]
    source = client.post(f"{base}/roles", json={"name": "财务", "color": "teal",
                                                "permissions": ["view_usage"]}).json()["id"]
    copy = client.post(f"{base}/roles", json={"name": "财务二组", "copyFrom": source}).json()["id"]
    role = _roles(home)[copy]
    assert (role["permissions"], role["color"]) == (["view_usage"], "teal")
    assert client.post(f"{base}/roles", json={"name": "x", "copyFrom": "g_missing"}).status_code == 404


def test_bad_requests_are_refused_not_half_applied(home):
    client, base = home["client"], home["base"]
    before = client.get(f"{base}/roles").json()
    for body in ({"name": ""}, {"name": "x", "permissions": ["fly"]}, {"name": "x", "permissions": "post"},
                 {"name": "x", "icon": "skull"}, {"name": "x", "mentionable": "yes"}):
        assert client.post(f"{base}/roles", json=body).status_code == 400
    assert client.patch(f"{base}/roles/everyone", json={"permissions": ["administrator"]}).status_code == 400
    assert client.patch(f"{base}/roles/admins", json={"permissions": []}).status_code == 400
    assert client.patch(f"{base}/roles/everyone", json={}).status_code == 400
    assert client.patch(f"{base}/roles/g_missing", json={"name": "x"}).status_code == 404
    assert client.delete(f"{base}/roles/everyone").status_code == 400
    assert client.put(f"{base}/roles/order", json={"ids": "admins"}).status_code == 400
    assert client.get(f"{base}/roles").json() == before


def test_members_list_and_their_groups(home):
    client, base = home["client"], home["base"]
    group = client.post(f"{base}/roles", json={"name": "财务"}).json()["id"]
    who = _join(home["root"])
    rows = client.get(f"{base}/members").json()["members"]
    assert [(row["role"], row["name"]) for row in rows] == [("owner", "我"), ("member", "小王")]
    assert rows[1]["userId"] == who and rows[1]["roleIds"] == []

    assert client.put(f"{base}/members/{who}/roles", json={"roleIds": [group]}).json() == {"roleIds": [group]}
    assert client.get(f"{base}/members").json()["members"][1]["roleIds"] == [group]
    assert _roles(home)[group]["people"] == 1 and _roles(home)["everyone"]["people"] == 2
    assert client.put(f"{base}/members/{who}/roles", json={"roleIds": ["g_missing"]}).status_code == 403
    assert client.put(f"{base}/members/m_missing/roles", json={"roleIds": []}).status_code == 404
    assert client.put(f"{base}/members/{who}/roles", json={}).status_code == 400

    assert client.delete(f"{base}/members/{who}").status_code == 204
    assert len(client.get(f"{base}/members").json()["members"]) == 1
    assert client.delete(f"{base}/members/{who}").status_code == 404


def test_writes_need_an_owner_token_on_the_service(home, monkeypatch):
    monkeypatch.delenv("VORTOONE_API_TOKEN")
    client, base = home["client"], home["base"]
    assert client.get(f"{base}/roles").status_code == 200
    assert client.post(f"{base}/roles", json={"name": "财务"}).status_code == 403
    assert client.put(f"{base}/roles/order", json={"ids": ["admins", "everyone"]}).status_code == 403
    assert client.delete(f"{base}/members/m_x").status_code == 403
    assert not (MemberRegistry(home["root"]).directory / "space.json").exists()


def test_a_damaged_registry_is_reported_not_papered_over(home):
    registry = MemberRegistry(home["root"])
    _join(home["root"])
    (registry.directory / "space.json").write_text("{", encoding="utf-8")
    client, base = home["client"], home["base"]
    assert client.get(f"{base}/roles").status_code == 409
    assert client.get(f"{base}/members").status_code == 409
    assert client.post(f"{base}/roles", json={"name": "财务"}).status_code == 409
