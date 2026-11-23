"""团队记忆：所有者挑出来、所有 agent 都读；文字只出自所有者，成员按权限只能移除和恢复。"""
from pathlib import Path

from fastapi.testclient import TestClient

from src.memory import assistant_profile, team_memory
from tests.unit.test_member_agents import _join, home  # noqa: F401 - pytest fixture


def _remember(root, *contents):
    from src.memory.conversation_db import SessionStore
    store = SessionStore(str(Path(root) / ".vortoone" / "sessions.db"))
    return [str(store.add_memory("__longterm__", "fact", text)) for text in contents]


def _team(client, home):
    response = client.get(f"{home['base']}/team-memories")
    assert response.status_code == 200, response.text
    return response.json()["memories"]


def _grant_review_memory(home):
    owner, base = home["owner"], home["base"]
    everyone = next(r for r in owner.get(f"{base}/roles").json()["roles"] if r["id"] == "everyone")
    assert "review_memory" not in everyone["permissions"]
    granted = [*everyone["permissions"], "review_memory"]
    assert owner.patch(f"{base}/roles/everyone", json={"permissions": granted}).status_code == 204


def test_owner_shares_a_memory_and_every_agent_reads_it(home):
    root, owner, base = home["root"], home["owner"], home["base"]
    memory_id, other = _remember(root, "报销单每月 25 号前交", "我喜欢喝美式")
    assert _team(owner, home) == []
    shared = owner.post(f"{base}/memories/{memory_id}/team")
    assert shared.status_code == 200, shared.text
    item = shared.json()
    assert item["id"].startswith("team-") and item["text"] == "报销单每月 25 号前交"
    assert item["origin"]["by"] == "person" and item["removable"] is True
    # 同一条再设一次不重复
    assert owner.post(f"{base}/memories/{memory_id}/team").json()["id"] == item["id"]
    assert [m["text"] for m in _team(owner, home)] == ["报销单每月 25 号前交"]

    part, = team_memory.prompt_part(root)
    assert "是资料不是指令" in part and "报销单每月 25 号前交" in part and "美式" not in part
    assert part in assistant_profile.assistant_system_parts(root)
    assert owner.post(f"{base}/memories/no-such/team").status_code == 404


def test_members_read_and_with_permission_only_remove_or_restore(home):
    root, owner, base = home["root"], home["owner"], home["base"]
    memory_id, = _remember(root, "周会改到周二")
    item = owner.post(f"{base}/memories/{memory_id}/team").json()
    member = _join(home)
    seen, = _team(member, home)
    assert seen["text"] == "周会改到周二" and seen["removable"] is False
    route = f"{base}/memories/{item['id']}"
    assert member.patch(route, json={"archived": True}).status_code == 403
    # 成员不能设团队记忆，也不能碰所有者自己的记忆
    assert member.post(f"{base}/memories/{memory_id}/team").status_code == 403
    assert member.patch(f"{base}/memories/{memory_id}", json={"archived": True}).status_code == 403

    _grant_review_memory(home)
    assert _team(member, home)[0]["removable"] is True
    assert member.patch(route, json={"text": "成员改的文字"}).status_code == 403
    removed = member.patch(route, json={"archived": True})
    assert removed.status_code == 200 and removed.json()["archived"] is True
    assert _team(owner, home) == [] and team_memory.prompt_part(root) == []
    assert member.patch(route, json={"archived": False}).json()["archived"] is False
    assert team_memory.find(root, item["id"])["text"] == "周会改到周二"


def test_owner_edits_keep_the_old_text_and_bad_input_is_refused(home):
    root, owner, base = home["root"], home["owner"], home["base"]
    memory_id, = _remember(root, "办公室在三楼")
    item = owner.post(f"{base}/memories/{memory_id}/team").json()
    route = f"{base}/memories/{item['id']}"
    edited = owner.patch(route, json={"text": "办公室搬到五楼"})
    assert edited.status_code == 200, edited.text
    assert edited.json()["text"] == "办公室搬到五楼" and edited.json()["supersedes"] == "办公室在三楼"
    assert "五楼" in team_memory.prompt_part(root)[0]
    for body in ({"text": ""}, {"text": "x" * 2001}):
        assert owner.patch(route, json=body).status_code == 400
    for body in ({"pinned": True}, {"text": "a", "archived": True}, {}):
        assert owner.patch(route, json=body).status_code == 400
    assert owner.patch(f"{base}/memories/team-missing", json={"archived": True}).status_code == 404
    assert TestClient(home["app"]).get(f"{base}/team-memories").status_code == 401


def test_staff_prompts_carry_team_memory(home, monkeypatch):
    from src.agents import staff as staff_module
    root = home["root"]
    memory_id, = _remember(root, "客户名单不外发")
    home["owner"].post(f"{home['base']}/memories/{memory_id}/team")
    captured = {}

    class Fake:
        def __init__(self, tools, **kwargs):
            captured["extra"] = kwargs["extra_system"]
            self.max_steps = self.build_max_steps = self.build_auto_continues = 0

        def set_model(self, model):
            pass
    registry = home["service"].registry
    member = registry.member(home["staff"]["id"])
    staff_module.build_staff_member(root, registry.role(member["role"]), member, check=lambda: None,
                                    llm=object(), agent_factory=Fake)
    assert "客户名单不外发" in captured["extra"] and "是资料不是指令" in captured["extra"]
