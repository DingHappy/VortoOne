"""新建 agent 页面要的几项如实回答：没有存下的设置、换模型要经提议、服务有没有模型、agent 不接邮箱。"""
from tests.unit.test_member_agents import home  # noqa: F401 - pytest fixture


def test_new_agent_page_answers(home, monkeypatch):
    owner, base, agent = home["owner"], home["base"], home["agent"]["id"]
    assert owner.get(f"{base}/agent-setup").json() == {"setup": None}
    refused = owner.post(f"{base}/agent-setup", json={"setup": {"brain": "model"}, "agentIds": [agent]})
    assert refused.status_code == 409 and "提议" in refused.json()["error"]["message"]
    monkeypatch.setattr("src.web.routers.app_api.views.model_connected", lambda: True)
    assert owner.get(f"{base}/credentials/platform").json() == {"available": True}
    monkeypatch.setattr("src.web.routers.app_api.views.model_connected", lambda: False)
    assert owner.get(f"{base}/credentials/platform").json() == {"available": False}
    assert owner.post(f"{base}/agents/{agent}/audience/mail-loss",
                      json={"audience": {"mode": "everyone"}}).json() == {"mailboxes": []}
    assert owner.post(f"{base}/agents/agent-missing/audience/mail-loss", json={}).status_code == 404
