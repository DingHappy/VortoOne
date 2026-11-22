"""任务看板：卡片的增删改、看板列与项目、可见范围、成员权限、交给 agent 走现有派活入口。"""

import pytest

from tests.unit.test_member_agents import _join, home  # noqa: F401 - pytest fixture


def _new(client, base, **body):
    answer = client.post(f"{base}/issues", json={"title": "整理发票", **body})
    assert answer.status_code == 200, answer.text
    return answer.json()


def test_cards_get_numbers_and_move_through_columns(home):
    owner, base = home["owner"], home["base"]
    columns = owner.get(f"{base}/issue-columns").json()["columns"]
    assert [c["stage"] for c in columns] == ["todo", "in_progress", "in_review", "done", "canceled"]
    first = _new(owner, base, description="十月的", criteria=["核对金额", "归档"], priority="high",
                 labels=["财务"], dueDate="2026-12-01")
    second = _new(owner, base, title="第二张")
    assert (first["number"], second["number"]) == (1, 2)
    assert first["status"] == "todo" and first["columnId"] == "col-todo" and first["priority"] == "high"
    assert [c["text"] for c in first["criteria"]] == ["核对金额", "归档"] and not first["criteria"][0]["done"]
    moved = owner.patch(f"{base}/issues/{first['id']}", json={"columnId": "col-in_review"}).json()
    assert moved["status"] == "in_review"
    done = owner.patch(f"{base}/issues/{first['id']}", json={"status": "done"}).json()
    assert done["columnId"] == "col-done"
    checked = owner.put(f"{base}/issues/{first['id']}/criteria/{first['criteria'][0]['id']}", json={"done": True}).json()
    assert checked["criteria"][0]["done"] and checked["criteria"][0]["doneBy"]["type"] == "person"
    # 编辑验收项：带 id 的保留勾选，没带的是新的，没列的删掉
    edited = owner.patch(f"{base}/issues/{first['id']}", json={"criteria": [
        {"id": first["criteria"][0]["id"], "text": "核对金额（含税）"}, {"text": "发邮件"}]}).json()
    assert [(c["text"], c["done"]) for c in edited["criteria"]] == [("核对金额（含税）", True), ("发邮件", False)]
    assert owner.get(f"{base}/issues/by-number/1").json()["title"] == "整理发票"
    events = [a.get("event") for a in owner.get(f"{base}/issues/{first['id']}/activity").json()["activity"]]
    assert events[0] == "created" and events.count("status_changed") == 2
    comment = owner.post(f"{base}/issues/{first['id']}/comments", json={"text": "已经开始"}).json()
    assert comment["kind"] == "comment" and comment["text"] == "已经开始"
    assert [i["number"] for i in owner.get(f"{base}/issues", params={"q": "第二"}).json()["issues"]] == [2]
    assert owner.delete(f"{base}/issues/{second['id']}").status_code == 204
    assert owner.get(f"{base}/issues/by-number/2").status_code == 404


def test_columns_and_projects(home):
    owner, base = home["owner"], home["base"]
    columns = owner.post(f"{base}/issue-columns", json={"stage": "in_progress", "name": "测试中"}).json()["columns"]
    extra = next(c for c in columns if c["name"] == "测试中")
    card = _new(owner, base)
    owner.patch(f"{base}/issues/{card['id']}", json={"columnId": extra["id"]})
    renamed = owner.patch(f"{base}/issue-columns/{extra['id']}", json={"name": "联调", "position": 0}).json()["columns"]
    assert renamed[0]["id"] == extra["id"] and renamed[0]["name"] == "联调"
    left = owner.delete(f"{base}/issue-columns/{extra['id']}").json()["columns"]
    assert extra["id"] not in {c["id"] for c in left}
    assert owner.get(f"{base}/issues/by-number/1").json()["columnId"] == "col-in_progress"
    assert owner.delete(f"{base}/issue-columns/col-todo").status_code == 400            # 每个阶段至少一列

    project = owner.post(f"{base}/issue-projects", json={"name": "年终", "color": "amber"}).json()
    owner.patch(f"{base}/issues/{card['id']}", json={"projectId": project["id"]})
    listed = owner.get(f"{base}/issue-projects").json()
    assert listed["projects"][0]["open"] == 1 and listed["unfiled"] == 0
    assert owner.get(f"{base}/issues", params={"projectId": project["id"]}).json()["issues"][0]["project"]["name"] == "年终"
    assert owner.patch(f"{base}/issue-projects/{project['id']}", json={"archived": True}).json()["archived"] is True
    assert owner.delete(f"{base}/issue-projects/{project['id']}").status_code == 204
    assert owner.get(f"{base}/issues/by-number/1").json()["project"] is None


@pytest.mark.parametrize("body", [
    {"title": ""}, {"title": "x" * 201}, {"title": "a", "priority": "urgent"}, {"title": "a", "dueDate": "明天"},
    {"title": "a", "assignee": {"type": "agent", "id": "nobody"}}, {"title": "a", "githubUrl": "https://evil.example"},
    {"title": "a", "criteria": ["x"] * 31}, {"title": "a", "projectId": "proj-none"}, {"title": "a", "secret": 1},
    {"title": "a", "attachmentIds": ["f1"]},
])
def test_bad_cards_are_refused(home, body):
    answer = home["owner"].post(f"{home['base']}/issues", json=body)
    assert answer.status_code in (400, 404), answer.text
    assert home["owner"].get(f"{home['base']}/issues").json() == {"issues": []}


def test_members_see_only_their_audience_and_need_edit_rights(home):
    owner, base = home["owner"], home["base"]
    member = _join(home)
    member_id = member.member_id
    public = _new(owner, base, title="大家都看")
    private = _new(owner, base, title="只给我", audience={"mode": "people", "userIds": [home["owner"].get("/api/app/me").json()["user"]["id"]]})
    for_member = _new(owner, base, title="给小王", audience={"mode": "people", "userIds": [member_id]})
    seen = {i["title"] for i in member.get(f"{base}/issues").json()["issues"]}
    assert seen == {"大家都看", "给小王"}
    assert member.get(f"{base}/issues/by-number/{private['number']}").status_code == 404
    assert member.patch(f"{base}/issues/{private['id']}", json={"title": "改"}).status_code == 404
    # 成员默认有「编辑资料」：能建、能改看得到的
    mine = _new(member, base, title="我建的")
    assert member.patch(f"{base}/issues/{public['id']}", json={"title": "大家都看（改）"}).status_code == 200
    # 只能删自己建的
    assert member.delete(f"{base}/issues/{public['id']}").status_code == 403
    assert member.delete(f"{base}/issues/{mine['id']}").status_code == 204
    assert for_member["audience"]["userIds"]


def _drop_from_everyone(home, key):
    owner, base = home["owner"], home["base"]
    everyone = next(r for r in owner.get(f"{base}/roles").json()["roles"] if r["id"] == "everyone")
    assert key in everyone["permissions"]
    kept = [item for item in everyone["permissions"] if item != key]
    assert owner.patch(f"{base}/roles/everyone", json={"permissions": kept}).status_code == 204


def test_a_member_without_edit_rights_only_reads(home):
    owner, base = home["owner"], home["base"]
    member = _join(home)
    card = _new(owner, base)
    _drop_from_everyone(home, "edit_wiki")
    assert [i["id"] for i in member.get(f"{base}/issues").json()["issues"]] == [card["id"]]
    assert member.post(f"{base}/issues", json={"title": "x"}).status_code == 403
    assert member.patch(f"{base}/issues/{card['id']}", json={"title": "x"}).status_code == 403
    assert member.post(f"{base}/issues/{card['id']}/comments", json={"text": "x"}).status_code == 403


def test_asking_an_agent_dispatches_through_the_existing_entry(home):
    owner, base = home["owner"], home["base"]
    card = _new(owner, base, description="把十月发票整理成表", criteria=["按日期排序"])
    asked = owner.post(f"{base}/issues/{card['id']}/ask-agent", json={"agentId": home["agent"]["id"]})
    assert asked.status_code == 200, asked.text
    issue = asked.json()
    assert issue["assignee"] == {"type": "agent", "id": home["agent"]["id"]} and issue["status"] == "in_progress"
    [job] = home["pool"].jobs
    events = owner.get(f"{base}/issues/{card['id']}/activity").json()["activity"]
    asked_event = next(e for e in events if e.get("event") == "agent_asked")
    assert asked_event["textParams"]["agentId"] == home["agent"]["id"]
    from src.workflow.tasks.tasks import TaskLedger
    task = TaskLedger(home["root"]).load(asked_event["textParams"]["taskId"])
    assert task is not None and "看板任务 #1：整理发票" in task.prompt and "按日期排序" in task.prompt
    assert job


def test_the_main_assistant_is_not_a_board_worker(home):
    owner, base = home["owner"], home["base"]
    card = _new(owner, base)
    answer = owner.post(f"{base}/issues/{card['id']}/ask-agent", json={"agentId": home["main"]["id"]})
    assert answer.status_code == 400 and not home["pool"].jobs


def test_a_member_without_dispatch_rights_cannot_ask_an_agent(home):
    owner, base = home["owner"], home["base"]
    member = _join(home)
    card = _new(owner, base)
    _drop_from_everyone(home, "assign_work")
    answer = member.post(f"{base}/issues/{card['id']}/ask-agent", json={"agentId": home["agent"]["id"]})
    assert answer.status_code == 403 and not home["pool"].jobs
    assert owner.get(f"{base}/issues/by-number/1").json()["assignee"] is None


def test_each_person_keeps_their_board_view(home):
    owner, base = home["owner"], home["base"]
    member = _join(home)
    view = owner.get(f"{base}/me/issues-view").json()["view"]
    assert view["layout"] == "board" and view["fields"]["priority"] is True
    saved = owner.put(f"{base}/me/issues-view", json={"view": {**view, "layout": "list",
                                                               "fields": {**view["fields"], "label": False}}})
    assert saved.json()["view"]["layout"] == "list" and saved.json()["view"]["fields"]["label"] is False
    assert member.get(f"{base}/me/issues-view").json()["view"]["layout"] == "board"
    assert owner.put(f"{base}/me/issues-view", json={"view": {"layout": "timeline"}}).status_code == 400
