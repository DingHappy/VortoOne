"""agent 详情页的几项如实给现在的状态：分组读部门、改要经提议；没有常设审批规则；agent 不自己写技能、不进团队频道。"""
from src.web import app_views as views
from src.workflow.staff.departments import Departments
from src.workflow.staff.registry import StaffRegistry
from tests.unit.test_member_agents import _join, home  # noqa: F401 - pytest fixture


def test_groups_are_the_departments_and_change_only_by_proposal(home):
    owner, base, root = home["owner"], home["base"], home["root"]
    assert owner.get(f"{base}/agent-groups").json() == {"groups": []}
    Departments(StaffRegistry(root)).register({"id": "dept-reading", "name": "资料组", "duty": "整理资料",
                                               "members": [home["staff"]["id"]]})
    group, = owner.get(f"{base}/agent-groups").json()["groups"]
    assert group["id"] == "dept-reading" and group["name"] == "资料组"
    assert group["agentIds"] == [views.member_agent_id(root, home["staff"]["id"])] == [home["agent"]["id"]]
    assert _join(home).get(f"{base}/agent-groups").json()["groups"] == [group]
    for answer in (owner.post(f"{base}/agent-groups", json={"name": "新组"}),
                   owner.patch(f"{base}/agent-groups/dept-reading", json={"name": "改名"}),
                   owner.delete(f"{base}/agent-groups/dept-reading"),
                   owner.put(f"{base}/agents/{home['agent']['id']}/group", json={"groupId": None})):
        assert answer.status_code == 409 and "提议" in answer.json()["error"]["message"]
    assert owner.put(f"{base}/agents/agent-missing/group", json={"groupId": None}).status_code == 404
    assert owner.get(f"{base}/agent-groups").json()["groups"] == [group]


def test_agent_pages_say_what_is_true(home):
    owner, base, agent = home["owner"], home["base"], home["agent"]["id"]
    assert owner.get(f"{base}/agents/{agent}/approval-rules").json() == {"rules": []}
    assert owner.delete(f"{base}/agents/{agent}/approval-rules/rule-x").status_code == 404
    assert owner.get(f"{base}/agents/{agent}/learned-skills").json() == {"skills": []}
    assert owner.get(f"{base}/agents/{agent}/channels").json() == {"channels": []}
    assert owner.put(f"{base}/agents/{agent}/channels", json={"channels": []}).json() == {"channels": []}
    refused = owner.put(f"{base}/agents/{agent}/channels", json={"channels": [{"id": "c", "autoReply": True}]})
    assert refused.status_code == 409 and "团队频道" in refused.json()["error"]["message"]
    assert owner.put(f"{base}/agents/{agent}/channels", json={}).status_code == 400
    for path in ("approval-rules", "learned-skills", "channels"):
        assert owner.get(f"{base}/agents/agent-missing/{path}").status_code == 404
