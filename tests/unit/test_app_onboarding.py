"""新建空间的引导（2026-11-24）：这个家只有一个、早已设好，照实回「已经完成」，不存不生效的进度。"""
from tests.unit.test_member_agents import _join, home  # noqa: F401 - pytest fixture


def test_onboarding_is_already_finished_here(home):
    owner, base = home["owner"], home["base"]
    for body in ({"step": "agents"}, {"finished": True}):
        answer = owner.patch(f"{base}/onboarding", json=body)
        assert answer.status_code == 200, answer.text
        shown = answer.json()
        assert (shown["step"], shown["finished"], shown["rightHandId"]) == ("done", True, home["main"]["id"])
        assert shown["me"]["user"]["id"] == owner.get("/api/app/me").json()["user"]["id"]
    assert "onboarding" not in owner.get("/api/app/me").json()["organizations"][0]       # 没有变出一个引导
    assert owner.patch(f"{base}/onboarding", json={}).status_code == 400
    assert owner.patch(f"{base}/onboarding", json={"step": "elsewhere"}).status_code == 422
    assert _join(home).patch(f"{base}/onboarding", json={"finished": True}).status_code == 403
