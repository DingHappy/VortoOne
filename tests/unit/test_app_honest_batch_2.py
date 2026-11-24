"""第二批如实回答（2026-11-24）：这里还没有对应能力的接口回 4xx 或空，不存一份不生效的数据；
早晚报设置照实读取；不开免审批合并、不开 Mission 命令的自动放行。"""
import pytest

from tests.unit.test_member_agents import _join, home  # noqa: F401 - pytest fixture


@pytest.mark.parametrize(("method", "path", "body", "status", "code"), [
    ("post", "/team-week/m1", {"act": "apply"}, 404, "team_week.not_found"),
    ("put", "/agents/{agent}/merge-repositories", {"repositories": ["me/repo"]}, 409, "repositories.merge_needs_approval"),
    ("put", "/agents/{agent}/mission-command-approval", {"commandApproval": "low_risk_auto"}, 409, "mission.approval_fixed"),
    ("delete", "/agents/{agent}/holds/h1", None, 404, "hold.not_found"),
    ("post", "/imports", {"dryRun": True}, 409, "import.unavailable"),
    ("post", "/imports/files", {}, 409, "import.unavailable"),
    ("get", "/blocks/b1/state", None, 404, "block.not_found"),
    ("put", "/blocks/b1/state", {"version": 1, "values": {}}, 404, "block.not_found"),
    ("post", "/blocks/b1/draw", {"version": 1}, 404, "block.not_found"),
    ("post", "/actions/a1/undo", {}, 404, "action.not_found"),
    ("post", "/actions/a1/redo", {}, 404, "action.not_found"),
    ("get", "/action-cards/m1", None, 404, "action_card.not_found"),
    ("post", "/action-cards/m1/approve", {}, 404, "action_card.not_found"),
    ("post", "/memories/x1/revert", {}, 409, "memory.no_history"),
    ("post", "/task-manager/continue", {"runIds": ["task-1"]}, 409, "task.continue_unavailable"),
    ("post", "/team/channel-move", {"channelId": "c"}, 409, "channel_move.unavailable"),
])
def test_missing_features_say_so_and_members_cannot_reach_them(home, method, path, body, status, code):
    url = home["base"] + path.replace("{agent}", home["agent"]["id"])
    response = getattr(home["owner"], method)(url, **({"json": body} if body is not None else {}))
    assert (response.status_code, response.json()["error"]["code"]) == (status, code)
    member = _join(home)
    assert getattr(member, method)(url, **({"json": body} if body is not None else {})).status_code == 403


def test_reads_are_honest(home, monkeypatch):
    owner, base = home["owner"], home["base"]
    agent = home["agent"]["id"]
    assert owner.get(f"{base}/agents/{agent}/merge-repositories").json() == {"repositories": []}
    assert owner.put(f"{base}/agents/{agent}/merge-repositories", json={"repositories": []}).json() == {"repositories": []}
    assert owner.put(f"{base}/agents/{agent}/mission-command-approval", json={"commandApproval": "always"}).json() == {
        "commandApproval": "always"}
    assert owner.get(f"{base}/agents/{agent}/for-you").json() == {"items": [], "monthCount": 0}
    assert owner.get(f"{base}/imports").json() == {"sources": []}
    monkeypatch.setenv("VORTOONE_ASSISTANT_REPORT_TIMES", "08:00,22:00")
    monkeypatch.delenv("VORTOONE_IM", raising=False)
    monkeypatch.delenv("VORTOONE_NOTIFY_URL", raising=False)
    week = owner.get(f"{base}/team-week").json()
    assert (week["dayHour"], week["weekHour"], week["day"]) == (8, 22, 0)
    assert owner.put(f"{base}/team-week", json={"dayHour": 8}).json() == week                 # 和现在一样：照原样回
    moved = owner.put(f"{base}/team-week", json={"dayHour": 7})
    assert moved.status_code == 409 and moved.json()["error"]["code"] == "team_week.fixed"


def test_single_home_and_text_only_feedback(home):
    owner = home["owner"]
    assert owner.post("/api/app/organizations", json={"name": "另一个"}).json()["error"]["code"] == "organization.single_home"
    assert owner.post("/api/app/feedback/files").json()["error"]["code"] == "feedback.files_unavailable"
    assert owner.delete("/api/app/feedback/files/f1").status_code == 404
    assert owner.post("/api/app/feedback/offers/m1", json={"send": True}).status_code == 404
