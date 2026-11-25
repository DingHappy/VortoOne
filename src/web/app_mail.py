"""界面「邮箱」：所有者接进来的团队 IMAP 邮箱（见 workflow/members/mailboxes.py，主人 2026-11-24 定）。

只有所有者能接、改、断开邮箱和给 agent 授权；成员看到的是空列表。授权码从不回给界面。
Gmail / Outlook（Composio）这里没有，如实拒绝。"""
from __future__ import annotations

from typing import Any, Dict, List

from src.web import app_views as views
from src.workflow.members.mailboxes import MailboxError, Mailboxes, default_policy


def _login(account: Dict[str, Any]):
    from src.plugins.mail import Account
    return Account(account["username"], account["password"], account["imapHost"], account["imapPort"])


def view(root: str, account: Dict[str, Any], grants: Dict[str, Any]) -> Dict[str, Any]:
    return {"id": account["id"], "provider": "imap", "via": "imap", "address": account["address"],
            "label": account["label"], "status": account["status"], "lastError": account["lastError"],
            "preset": account["preset"], "createdAt": account["createdAt"], "offActions": account["offActions"],
            "askActions": account["askActions"], "aliases": account["aliases"], "defaultFrom": account["defaultFrom"],
            "ownerUserId": account["owner"],
            "agents": [{"agentId": views.member_agent_id(root, staff_id), "level": grant["level"], "canDelete": False}
                       for staff_id, grant in sorted(grants.items())]}


def accounts(root: str) -> List[Dict[str, Any]]:
    store = Mailboxes(root)
    return [view(root, account, store.agents_on(account["id"])) for account in store.accounts()]


def connect(root: str, data: Dict[str, Any]) -> Dict[str, Any]:
    """先登录一次（打开收件箱）再存：连不上不留记录。"""
    from src.plugins.mail import verify
    store = Mailboxes(root)
    connection = store.connection(data)
    verify(_login(connection))
    account = store.add(connection, views.owner_id(root))
    return view(root, account, {})


def update(root: str, account_id: str, change: Dict[str, Any]) -> Dict[str, Any]:
    from src.plugins.mail import verify
    store = Mailboxes(root)
    account = store.get(account_id)
    password = change.get("password")
    if password is not None:
        if not isinstance(password, str) or not password.strip() or len(password) > 256:
            raise MailboxError(400, "invalid", "新的授权码无效")
        verify(_login({**account, "password": password.strip()}))      # 先测，通过了才换
        password = password.strip()
    saved = store.update(account_id, {k: v for k, v in change.items() if k != "password"}, password=password)
    return view(root, saved, store.agents_on(account_id))


def folders(root: str, account_id: str) -> List[Dict[str, str]]:
    from src.plugins.mail import MailError, list_folders
    store = Mailboxes(root)
    account = store.get(account_id)
    try:
        return list_folders(_login(account))
    except MailError as error:
        if error.code == "auth":
            store.mark(account_id, error="登录失败：授权码可能失效了")
        raise


def suggested_addresses(root: str, account_id: str) -> List[str]:
    from src.plugins.mail import sent_addresses
    account = Mailboxes(root).get(account_id)
    return [address for address in sent_addresses(_login(account)) if address not in account["aliases"]]


def agent_mailboxes(root: str, staff_id: str) -> List[Dict[str, Any]]:
    store = Mailboxes(root)
    grants = store.grants_of(staff_id)
    return [{"accountId": account["id"], "level": grants.get(account["id"], {}).get("level", "none"), "canDelete": False,
             "policy": grants.get(account["id"], {}).get("policy", default_policy())} for account in store.accounts()]


def set_agent(root: str, staff_id: str, account_id: str, body: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(body, dict) or set(body) != {"level", "canDelete", "policy"}:
        raise MailboxError(400, "invalid", "要给 level、canDelete 和 policy")
    saved = Mailboxes(root).grant(account_id, staff_id, body["level"], body["canDelete"], body["policy"])
    return {"accountId": account_id, "level": saved["level"], "canDelete": False, "policy": saved["policy"]}
