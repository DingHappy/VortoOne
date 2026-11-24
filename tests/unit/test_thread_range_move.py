"""选段话题「拿出频道 / 放回频道」（接 Codex 做了一半的 threads.act，2026-11-24）。

拿出：选段里的消息改挂到话题根消息下，变成话题的回复，频道主时间线里看不到；放回：只把写在根消息之前的
那几条挂回去，真正的回复不动。拿出期间不能调整选段、不能撤销话题。
"""
from tests.unit.test_member_agents import _join, home  # noqa: F401 - pytest fixture
from tests.unit.test_team_channels import _create
from tests.unit.test_team_thread_ranges import _create_range, _selection, _send


def _top_level(client, channel):
    """频道主时间线上的消息 id（选段话题的根消息自己也在，它是标记）。"""
    return [row["id"] for row in client.get(f"{channel}/messages").json()["messages"]]


def test_moving_a_range_takes_it_out_of_the_channel_and_return_puts_it_back(home):
    owner, member = home["owner"], _join(home)
    channel = _create(home, "move-work", people=[member.member_id])
    first, skipped, last, later = (_send(owner, channel, text) for text in ("甲", "乙", "丙", "之后"))
    url, made = _create_range(member, home, _selection(channel, first, last, excludedMessageIds=[skipped["id"]]))
    assert made["context"]["moved"] is False
    moved = member.post(f"{url}/move", json={})
    assert moved.status_code == 200, moved.text
    assert moved.json()["context"]["moved"] is True and moved.json()["context"]["movedCount"] == 2
    assert moved.json()["replyCount"] == 2                                  # 拿出来的就是话题的回复
    top = _top_level(owner, channel)
    assert first["id"] not in top and last["id"] not in top                  # 拿走了
    assert skipped["id"] in top and later["id"] in top                       # 被排除的、选段外的留在频道
    inside = [row["id"] for row in owner.get(f"{channel}/messages", params={"threadId": made["id"]}).json()["messages"]]
    assert first["id"] in inside and last["id"] in inside
    marker = next(row for row in owner.get(f"{channel}/messages").json()["messages"] if row["id"] == made["id"])
    assert marker["meta"]["threadStart"]["moved"] is True and marker["meta"]["threadStart"]["movedCount"] == 2
    assert member.post(f"{url}/move", json={}).status_code == 200            # 再拿一次不报错

    reply = _send(owner, channel, "话题里的新回复", threadId=made["id"])
    adjust = member.patch(url, json={"lastMessageId": later["id"]})
    assert adjust.status_code == 409                                         # 拿出期间不能调整选段
    returned = member.post(f"{url}/return", json={})
    assert returned.status_code == 200 and returned.json()["context"]["moved"] is False
    top = _top_level(owner, channel)
    assert first["id"] in top and last["id"] in top
    inside = [row["id"] for row in owner.get(f"{channel}/messages", params={"threadId": made["id"]}).json()["messages"]]
    assert reply["id"] in inside                                             # 真正的回复不动
    assert member.post(f"{url}/return", json={}).status_code == 200


def test_only_range_threads_and_people_who_manage_them_can_move(home):
    owner, member = home["owner"], _join(home)
    channel = _create(home, "move-rights", people=[member.member_id])
    first, last = _send(owner, channel, "一"), _send(owner, channel, "二")
    url, made = _create_range(owner, home, _selection(channel, first, last))
    other = _join(home, "li@example.com", "小李")
    assert other.post(f"{url}/move", json={}).status_code in (403, 404)
    plain = _send(owner, channel, "普通话题")
    _send(owner, channel, "回复", threadId=plain["id"])
    refused = owner.post(f"{home['base']}/threads/{plain['id']}/move", json={})
    assert refused.status_code == 400                                         # 普通话题没有选段
    assert owner.post(f"{url}/archive", json={}).status_code == 200
    assert owner.post(f"{url}/move", json={}).status_code == 409              # 归档的话题先解除归档
    assert first["id"] in _top_level(owner, channel)
