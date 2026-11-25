"""Mission 的决定和交付（主人 2026-11-23 定）：agent 在一步里提出，卡片跟着回答发在话题里；
阻塞的决定等人拍板再接着做，交付等所有者验收：完成就收尾，提出修改就带着意见接着做。

真实派发、执行池和频道记录，只有模型是假的；假模型真的调用 mission_decision / mission_deliver。
"""
import asyncio

from tests.unit.test_channel_agents_answer import _agent_replies, _client, _until, team  # noqa: F401
from tests.unit.test_member_agents import _join, home  # noqa: F401 - pytest fixture
from tests.unit.test_mission_runs import PLAN_A, PLAN_DONE, _get, _note, _settled, _steps, mission  # noqa: F401

ASK = ("mission_decision", {"question": "先做哪个渠道", "options": ["客服", "问卷"], "recommendation": "客服",
                            "why": "量大", "blocking": True})
SOFT = ("mission_decision", {"question": "报告用中文还是英文", "options": ["中文", "英文"], "recommendation": "中文",
                             "blocking": False})
DELIVER = ("mission_deliver", {"summary": "反馈整理好了，放在话题里", "checklist": ["看一下分类", "确认数字"]})


def _card_messages(case, key):
    rows = case.owner.get(case.channel + "/messages", params={"threadId": case.thread}).json()["messages"]
    return [row for row in rows if (row.get("meta") or {}).get(key)]


async def test_a_blocking_decision_waits_until_the_owner_chooses(mission):
    mission.script.extend([
        ([("mission_plan", {"items": PLAN_A}), ASK], "要先问一下"),
        ([("mission_plan", {"items": PLAN_DONE}), DELIVER], "做完了"),
    ])
    async with _client(mission) as owner:
        assert (await owner.post(mission.route + "/start", json={})).status_code == 200
        await _note(mission, "拍板")
        view = _get(mission)
        assert view["status"] == "active" and view["runningTasks"] == 0 and view["pending"]["decisions"] == 1
        await asyncio.sleep(0.2)
        assert len(_steps(mission)) == 1                                   # 等人拍板：不派下一步
        [card] = _card_messages(mission, "missionDecision")
        assert card["author"]["type"] == "agent" and card["body"] == "先做哪个渠道"
        [decision] = mission.owner.get(mission.route + "/decisions").json()["decisions"]
        assert card["meta"]["missionDecision"] == {"missionId": mission.mission["id"], "id": decision["id"]}
        assert (decision["status"], decision["options"], decision["recommendation"], decision["messageId"]) == (
            "pending", ["客服", "问卷"], "客服", card["id"])
        bad = await owner.post(f"{mission.route}/decisions/{decision['id']}/choose", json={"option": "电话"})
        assert bad.status_code == 400 and bad.json()["error"]["code"] == "mission.invalid_option"
        opinion = await owner.post(f"{mission.route}/decisions/{decision['id']}/opinion", json={"option": "问卷"})
        assert opinion.status_code == 200 and opinion.json()["opinions"][0]["option"] == "问卷"
        chosen = await owner.post(f"{mission.route}/decisions/{decision['id']}/choose", json={"option": "问卷"})
        assert chosen.status_code == 200, chosen.text
        assert chosen.json()["status"] == "taken" and chosen.json()["chosen"] == "问卷"
        view = await _settled(mission, "awaiting_close")
    assert len(_steps(mission)) == 2 and view["pending"]["deliveries"] == 1 and view["pending"]["decisions"] == 0
    second = mission.seen.prompts[1]
    assert "先做哪个渠道 → 已拍板：问卷" in second and "有人拍板了" in second
    assert second.index("已拍板：问卷") < second.rindex("</vortocode_untrusted_memory>")      # 选项文字是 agent 写的：资料
    again = mission.owner.post(f"{mission.route}/decisions/{decision['id']}/choose", json={"option": "问卷"})
    assert again.status_code == 200                                         # 同一选项重复点不报错
    other = mission.owner.post(f"{mission.route}/decisions/{decision['id']}/choose", json={"option": "客服"})
    assert other.status_code == 409


async def test_delivery_accept_finishes_and_changes_send_the_agent_back(mission):
    mission.script.extend([
        ([("mission_plan", {"items": PLAN_DONE}), DELIVER], "交付了"),
        ([("mission_plan", {"items": PLAN_DONE}), DELIVER], "改好了"),
    ])
    async with _client(mission) as owner:
        assert (await owner.post(mission.route + "/start", json={})).status_code == 200
        await _settled(mission, "awaiting_close")
        [first] = (await owner.get(mission.route + "/deliveries")).json()
        assert first["summary"] == "反馈整理好了，放在话题里" and [c["text"] for c in first["checklist"]] == ["看一下分类", "确认数字"]
        assert first["messageId"] == _card_messages(mission, "missionDelivery")[0]["id"]
        checked = await owner.put(f"{mission.route}/deliveries/{first['id']}/checks/c1", json={"checked": True})
        assert checked.status_code == 200 and checked.json()["checklist"][0]["checkedBy"] == [_get(mission)["ownerId"]]
        unchecked = await owner.put(f"{mission.route}/deliveries/{first['id']}/checks/c1", json={"checked": False})
        assert unchecked.json()["checklist"][0]["checkedBy"] == []
        assert (await owner.put(f"{mission.route}/deliveries/{first['id']}/checks/nope", json={"checked": True})).status_code == 404
        empty = await owner.post(f"{mission.route}/deliveries/{first['id']}/changes", json={"note": "  "})
        assert empty.status_code == 400
        changed = await owner.post(f"{mission.route}/deliveries/{first['id']}/changes", json={"note": "数字要按周汇总"})
        assert changed.status_code == 200, changed.text
        await _until(lambda: len(_steps(mission)) == 2)
        await _settled(mission, "awaiting_close")
        assert "数字要按周汇总" in mission.seen.prompts[1] and "修改意见" in mission.seen.prompts[1]
        rows = (await owner.get(mission.route + "/deliveries")).json()
        assert [row["status"] for row in rows] == ["changes_requested", "pending"]
        # 2026-11-24 起「存到 Wiki」接上了（主人定）：私有频道里的 Mission，这页只给频道里的人看
        private = await owner.patch(mission.channel, json={"visibility": "private"})
        assert private.status_code == 200, private.text
        accepted = await owner.post(f"{mission.route}/deliveries/{rows[1]['id']}/accept",
                                    json={"saveToWiki": True, "note": "很好"})
        assert accepted.status_code == 200, accepted.text
        assert accepted.json()["status"] == "done" and accepted.json()["wikiPageId"]
    rows = mission.owner.get(mission.route + "/deliveries").json()
    assert rows[1]["status"] == "accepted" and rows[1]["decidedBy"] == _get(mission)["ownerId"]
    assert rows[1]["saveToWiki"] is True and rows[0]["saveToWiki"] is False
    base = mission.route.split("/missions/")[0]
    [page] = [row for row in mission.owner.get(base + "/wiki/pages").json()["pages"]
              if row["id"] == accepted.json()["wikiPageId"]]
    detail = mission.owner.get(f"{base}/wiki/pages/{page['slug']}").json()
    assert detail["folder"] == "Mission" and detail["title"].startswith("Mission：")
    assert "反馈整理好了，放在话题里" in detail["body"] and "- [ ] 看一下分类" in detail["body"]
    assert "**验收意见**：很好" in detail["body"]
    assert detail["audience"] is not None and _get(mission)["ownerId"] in detail["audience"]["userIds"]
    late = mission.owner.post(f"{mission.route}/deliveries/{rows[0]['id']}/accept", json={})
    assert late.status_code == 409


async def test_provisional_decisions_keep_going_and_can_be_approved_together(mission):
    mission.script.extend([
        ([("mission_plan", {"items": PLAN_A}), SOFT], "先按中文写"),
        ([("mission_plan", {"items": PLAN_DONE})], "写完了"),
    ])
    async with _client(mission) as owner:
        assert (await owner.post(mission.route + "/start", json={})).status_code == 200
        view = await _settled(mission, "awaiting_close")              # 不阻塞：没停下等人
        assert len(_steps(mission)) == 2 and view["pending"]["provisional"] == 1
        approved = await owner.post(mission.route + "/decisions/approve-provisional", json={})
        assert approved.status_code == 200
        [decision] = approved.json()["decisions"]
        assert decision["status"] == "taken" and decision["chosen"] == "中文"
    assert _get(mission)["pending"]["provisional"] == 0
    assert "先按建议：中文" in mission.seen.prompts[1]


async def test_cards_from_an_unfinished_step_never_show_up(mission):
    mission.script.append(([("mission_plan", {"items": PLAN_A}), ASK, DELIVER], RuntimeError("模型挂了")))
    async with _client(mission) as owner:
        assert (await owner.post(mission.route + "/start", json={})).status_code == 200
        await _settled(mission, "paused")
    assert mission.owner.get(mission.route + "/decisions").json() == {"decisions": []}
    assert mission.owner.get(mission.route + "/deliveries").json() == []
    assert not _card_messages(mission, "missionDecision") and not _card_messages(mission, "missionDelivery")


async def test_bad_cards_are_refused_and_limited_per_step(mission):
    mission.script.append(([
        ("mission_decision", {"question": "q", "options": ["a", "a"]}),
        ("mission_decision", {"question": "q", "options": ["a", "b"], "recommendation": "c"}),
        ("mission_plan", {"items": PLAN_A}), SOFT, SOFT, SOFT, SOFT,
    ], "提了好几张"))
    async with _client(mission) as owner:
        assert (await owner.post(mission.route + "/start", json={})).status_code == 200
        await _until(lambda: len(_steps(mission)) >= 2 or _get(mission)["status"] != "active")
    results = [str(r) for r in mission.seen.results[:7]]
    assert "选项不能重复" in results[0] and "建议要是选项之一" in results[1]
    assert "最多" in results[6]                                                # 一步最多 3 张
    await _until(lambda: len(mission.owner.get(mission.route + "/decisions").json()["decisions"]) == 3)


async def test_members_and_strangers_cannot_decide_or_see(mission):
    mission.script.append(([("mission_plan", {"items": PLAN_A}), ASK], "问一下"))
    async with _client(mission) as owner:
        assert (await owner.post(mission.route + "/start", json={})).status_code == 200
        await _note(mission, "拍板")
    [decision] = mission.owner.get(mission.route + "/decisions").json()["decisions"]
    member = mission.member
    # 频道里的成员（还没加入 Mission）看得到决定，但不能拍板、表态
    assert member.get(mission.route + "/decisions").status_code == 200
    assert member.get(mission.route + "/deliveries").status_code == 200
    for method, path, body in ((
                               "post", f"/decisions/{decision['id']}/choose", {"option": "客服"}),
                               ("post", f"/decisions/{decision['id']}/opinion", {"option": "客服"}),
                               ("post", "/decisions/approve-provisional", {})):
        response = getattr(member, method)(mission.route + path, **({"json": body} if body is not None else {}))
        assert response.status_code == 403, path
    assert mission.owner.get(mission.route + "/decisions").json()["decisions"][0]["status"] == "pending"
    # 话题里的人看得到卡片消息本身（它就是 agent 在话题里发的一条）
    rows = member.get(mission.channel + "/messages", params={"threadId": mission.thread}).json()["messages"]
    assert any((row.get("meta") or {}).get("missionDecision") for row in rows)


async def test_resume_after_a_delivery_supersedes_it(mission):
    mission.script.extend([([("mission_plan", {"items": PLAN_A}), DELIVER], "交付了"),
                           ([("mission_plan", {"items": PLAN_DONE})], "又做了一点")])
    async with _client(mission) as owner:
        assert (await owner.post(mission.route + "/start", json={})).status_code == 200
        await _settled(mission, "awaiting_close")
        assert (await owner.post(mission.route + "/resume", json={})).status_code == 200
        await _until(lambda: len(_steps(mission)) == 2)
        await _settled(mission, "awaiting_close")
    assert [row["status"] for row in mission.owner.get(mission.route + "/deliveries").json()] == ["superseded"]


async def test_approving_provisional_leaves_questions_that_wait_for_a_person(mission):
    mission.script.append(([("mission_plan", {"items": PLAN_A}), SOFT, ASK], "两个问题"))
    async with _client(mission) as owner:
        assert (await owner.post(mission.route + "/start", json={})).status_code == 200
        await _note(mission, "拍板")
        approved = (await owner.post(mission.route + "/decisions/approve-provisional", json={})).json()["decisions"]
    assert [d["question"] for d in approved] == ["报告用中文还是英文"]
    statuses = {d["question"]: d["status"] for d in mission.owner.get(mission.route + "/decisions").json()["decisions"]}
    assert statuses == {"报告用中文还是英文": "taken", "先做哪个渠道": "pending"}
    assert len(_steps(mission)) == 1                                       # 阻塞的那个还没人选：不接着做
