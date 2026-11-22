"""新版界面的任务看板：谁能看、谁能改，卡片的界面形状，以及「交给 agent」。

- 所有者看、改所有卡片。成员：看可见范围包含自己的（所有人可见、名单里有他、他建的、他负责的）；
  新建和编辑要「编辑资料」（material.edit）；删除只限建卡的人和所有者。
- 交给 agent：经现有的派活入口生成执行任务（所有者走 app_staff.send_task，成员走
  app_members.member_send_task，后者按他的级别收窄并要所有者批准读以外的每一步）。卡片只记一条动态，
  不另起执行器，也不因为卡片在看板上就多给 agent 任何权限。
- 卡片的标题和说明是人写的内容，交给 agent 时照原文附上；agent 的回复照旧进它的私聊和任务台账。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from src.agents.principal import Principal
from src.web import app_views as views
from src.workflow.board.store import Board, BoardError, NotFound

MAX_HANDOFF = 4000


def _actor_of(who: Principal) -> Dict[str, str]:
    return {"type": "person", "id": who.id}


def _keys(root: str, who: Principal) -> set:
    from src.web.app_members import permission_keys
    return set(permission_keys(root, who))


def can_edit(root: str, who: Principal) -> bool:
    """「编辑资料」（界面键 edit_wiki，内核 material.edit，成员默认有）。"""
    return who.is_owner or "edit_wiki" in _keys(root, who)


def can_see(issue: Dict[str, Any], who: Principal) -> bool:
    if who.is_owner:
        return True
    audience = issue.get("audience") or {}
    people = [(issue.get("createdBy") or {}).get("id"), (issue.get("assignee") or {}).get("id")]
    return audience.get("mode") == "everyone" or who.id in (audience.get("userIds") or []) or who.id in people


def _known_people(root: str) -> set:
    from src.web.app_members import members
    return {row["userId"] for row in members(root)}


def _known_agents(root: str) -> set:
    return {agent["id"] for agent in views.agents(root)}


def _check_actor(root: str, actor: Any) -> None:
    if not actor:
        return
    if not isinstance(actor, dict) or actor.get("type") not in ("person", "agent"):
        raise BoardError("负责人要写成 {type: person|agent, id}")
    pool = _known_people(root) if actor["type"] == "person" else _known_agents(root)
    if actor.get("id") not in pool:
        raise NotFound("没有这个负责人")


def audience_of(choice: Any, who: Principal, root: str) -> Dict[str, Any]:
    if choice is None:
        return {"mode": "everyone", "userIds": [], "channelId": None}
    if not isinstance(choice, dict) or choice.get("mode") not in ("everyone", "people"):
        raise BoardError("可见范围只能是所有人或指定的人")
    if choice["mode"] == "everyone":
        return {"mode": "everyone", "userIds": [], "channelId": None}
    ids = choice.get("userIds")
    if not isinstance(ids, list) or not ids or any(not isinstance(item, str) for item in ids) or len(ids) > 100:
        raise BoardError("要列出能看到的人")
    unknown = set(ids) - _known_people(root)
    if unknown:
        raise NotFound("名单里有不认识的人")
    return {"mode": "people", "userIds": list(dict.fromkeys(ids + [who.id])), "channelId": None}


def view(root: str, board: Board, issue: Dict[str, Any], *, projects: Optional[Dict[str, Any]] = None,
         detail: bool = False) -> Dict[str, Any]:
    projects = projects if projects is not None else {item["id"]: item for item in board.projects()}
    project = projects.get(issue.pop("projectId", None) or "")
    row = {**issue, "project": project, "pullRequests": [], "artifacts": [], "work": None, "blocked": None,
           "backgroundWork": None, "conversation": None}
    if detail:
        row["subIssues"] = [{"id": child["id"], "number": child["number"], "title": child["title"],
                             "status": child["status"], "assignee": child["assignee"]}
                            for child in board.children(issue["id"])]
    return row


def listing(root: str, who: Principal, *, q: str = "", project_id: str = "") -> List[Dict[str, Any]]:
    board = Board(root)
    projects = {item["id"]: item for item in board.projects()}
    needle = q.strip().lower()
    rows = []
    for issue in board.issues():
        if not can_see(issue, who):
            continue
        if project_id and issue.get("projectId") != project_id:
            continue
        if needle and needle not in issue["title"].lower() and needle not in issue["description"].lower() \
                and needle != f"#{issue['number']}":
            continue
        rows.append(view(root, board, issue, projects=projects))
    return rows


def _visible(root: str, who: Principal, issue: Dict[str, Any]) -> Dict[str, Any]:
    if not can_see(issue, who):
        raise NotFound("没有这张卡片")
    return issue


def get(root: str, who: Principal, number: int) -> Dict[str, Any]:
    board = Board(root)
    return view(root, board, _visible(root, who, board.by_number(number)), detail=True)


def create(root: str, who: Principal, body: Dict[str, Any]) -> Dict[str, Any]:
    if set(body) - {"title", "description", "criteria", "assignee", "priority", "dueDate", "labels", "projectId",
                    "githubUrl", "attachmentIds", "audience", "parentIssueId"}:
        raise BoardError("不认识的字段")
    if body.get("attachmentIds"):
        raise BoardError("建卡时附文件这里还不支持")
    _check_actor(root, body.get("assignee"))
    board = Board(root)
    if body.get("parentIssueId"):
        _visible(root, who, board.issue(body["parentIssueId"]))
    issue = board.create(_actor_of(who), body, audience=audience_of(body.get("audience"), who, root))
    return view(root, board, issue, detail=True)


def update(root: str, who: Principal, issue_id: str, patch: Dict[str, Any]) -> Dict[str, Any]:
    board = Board(root)
    _visible(root, who, board.issue(issue_id))
    if "assignee" in patch:
        _check_actor(root, patch["assignee"])
    return view(root, board, board.update(issue_id, _actor_of(who), patch), detail=True)


def delete(root: str, who: Principal, issue_id: str) -> None:
    from src.workflow.members.registry import Refused
    board = Board(root)
    issue = _visible(root, who, board.issue(issue_id))
    if not who.is_owner and (issue.get("createdBy") or {}).get("id") != who.id:
        raise Refused("只有建卡的人和所有者能删这张卡")
    board.delete(issue_id)


def set_audience(root: str, who: Principal, issue_id: str, choice: Any) -> Dict[str, Any]:
    board = Board(root)
    _visible(root, who, board.issue(issue_id))
    return view(root, board, board.set_audience(issue_id, audience_of(choice, who, root)), detail=True)


def set_criterion(root: str, who: Principal, issue_id: str, criterion_id: str, done: Any) -> Dict[str, Any]:
    board = Board(root)
    _visible(root, who, board.issue(issue_id))
    return view(root, board, board.set_criterion(issue_id, criterion_id, done, _actor_of(who)), detail=True)


def activity(root: str, who: Principal, issue_id: str) -> List[Dict[str, Any]]:
    board = Board(root)
    _visible(root, who, board.issue(issue_id))
    return board.activity(issue_id)


def comment(root: str, who: Principal, issue_id: str, body: Dict[str, Any]) -> Dict[str, Any]:
    if set(body) - {"text", "attachmentIds"} or body.get("attachmentIds"):
        raise BoardError("评论里附文件这里还不支持")
    board = Board(root)
    _visible(root, who, board.issue(issue_id))
    return board.comment(issue_id, _actor_of(who), body.get("text"))


def handoff_text(issue: Dict[str, Any]) -> str:
    lines = [f"看板任务 #{issue['number']}：{issue['title']}"]
    if issue["description"]:
        lines += ["", issue["description"]]
    if issue["criteria"]:
        lines += ["", "验收项："] + [f"- {item['text']}" for item in issue["criteria"]]
    if issue["dueDate"]:
        lines += ["", f"截止：{issue['dueDate']}"]
    return "\n".join(lines)[:MAX_HANDOFF]


def ask_agent(root: str, who: Principal, issue_id: str, agent_id: Any, *, owner_proof: bool,
              staff_of) -> Dict[str, Any]:
    """把卡片交给一位专员：经现有派活入口生成一件事；卡片记一条动态、负责人设成那位 agent。"""
    from src.web import app_members, app_staff
    from src.workflow.members.registry import Refused
    board = Board(root)
    issue = _visible(root, who, board.issue(issue_id))
    if not isinstance(agent_id, str):
        raise BoardError("要选一位 agent")
    staff_id = staff_of(root, agent_id)
    if staff_id is None:
        if agent_id == views.main_agent_id(root):
            raise BoardError("看板任务目前交给专员执行；主助理可以在对话里替你派")
        raise NotFound("没有这个 agent")
    text = handoff_text(issue)
    if who.is_owner:
        if not owner_proof:
            raise Refused("交给专员需要已认证的所有者")
        task = app_staff.send_task(root, staff_id, text)
    else:
        if "assign_work" not in _keys(root, who):
            raise Refused("你没有派活的权限")
        task = app_members.member_send_task(root, who, staff_id, text)   # 再按他对这个 agent 的级别收窄
    author = _actor_of(who)
    patch: Dict[str, Any] = {"assignee": {"type": "agent", "id": agent_id}}
    if issue["status"] == "todo":
        patch["status"] = "in_progress"
    board.update(issue_id, author, patch)
    board.record(issue_id, author, "agent_asked", "asked an agent to work on it",
                 {"agentId": agent_id, "taskId": str(task.get("id") or "")})
    return view(root, board, board.issue(issue_id), detail=True)
