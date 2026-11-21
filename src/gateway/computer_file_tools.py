"""Prepare one remote file operation in the existing ToolRuntime."""
from __future__ import annotations

import asyncio
from dataclasses import asdict
from functools import partial
import hashlib
import json
from pathlib import Path
import time
import uuid

from src.agents.tool import PreparedToolCall, Tool, ToolResult
from src.agents.tool_runtime import check_tool_execution, current_tool_call_id
from src.agents.trust import READ, WRITE
from src.gateway.computer_connections import FileConnectionUnavailable, connections
from src.gateway.computer_file_authority import current_owner_turn, file_confirmation
from src.gateway.computer_file_intents import FileIntentUnavailable, HomeFileIntents
from src.gateway.computer_file_paths import relative_parts, text_bytes
from src.gateway.computer_file_store import FileAction
from src.gateway.computer_home_delivery import OUTBOX, deliver_file, outgoing_source
from src.gateway.computer_file_wire import object_fields, policy_value, result_value, staged_transfer, transfer_manifest, version_value
from src.gateway.computer_protocol import DELIVERY_PROTOCOL, DOWNLOAD_PREFIX, MAX_TRANSFER_BYTES, TRANSFER_PROTOCOL, identifier
from src.gateway.computer_transfer_retry import reconnect_transfer
from src.gateway.computer_transfers import TransferDenied, TransferFile, TransferUnavailable, computer_transfers
from src.gateway.connected_computers import ComputerUnavailable, ConnectedComputers
from src.utils.home_maintenance import HomeMaintenanceError

_SOURCE = {"external_content": True, "untrusted_source": True, "serial": True}
_OFFLINE = "电脑离线、未启用文件协议，或配对已变化。"
_OWNER = "请由所有者在家的主要聊天发起这项操作。"
_STATUS = {"created": "已新建", "blocked": "电脑已拒绝操作", "unknown": "结果未知",
           "waiting": "电脑已确认尚未开始"}
FILE_ACTION_SECONDS = 90
FILE_QUEUE_POLL_SECONDS = 3
FILE_TRANSFER_SECONDS = 900


def _blocked(message: str) -> ToolResult:
    return ToolResult(message, "blocked")


async def _receive_file(connection, target, info, still_current):
    deadline = int(time.time()) + FILE_TRANSFER_SECONDS
    request = {**target, **info, "deadline": deadline}
    action_id = uuid.uuid4().hex
    file = None
    current = connection
    for attempt in range(2):
        issued = None
        try:
            hashed = transfer_manifest(await current.file_request(
                "transfer_hash", request, timeout=max(0.001, deadline - time.time())), hashed=True)
            if any(hashed[key] != value for key, value in info.items()) or (file and hashed["sha256"] != file.sha256):
                return _blocked("文件版本已变化，请重新发起。")
            if not await still_current(current):
                raise FileConnectionUnavailable("电脑连接或所有者授权已变化")
            check_tool_execution()
            file = TransferFile(action_id, target["folder_id"], target["policy_revision"],
                                info["version"], info["size"], hashed["sha256"])
            issued = await computer_transfers.issue(current, file, action_deadline=deadline)
            reply = await current.file_request("transfer_upload", {**request, **issued, "sha256": file.sha256},
                                               timeout=max(0.001, deadline - time.time()))
            transfer_id = issued["transfer_id"]
            staged_transfer(reply, transfer_id=transfer_id, size=file.size, sha256=file.sha256)
            check_tool_execution()
            if not await still_current(current):
                raise FileConnectionUnavailable("电脑连接或所有者授权已变化")
            await computer_transfers.publish(current, transfer_id, file.action_id, Path(target["path"]).name)
            return ToolResult(f"文件已收到，共 {file.size} 字节。请在 10 分钟内[下载这个文件]({DOWNLOAD_PREFIX}{transfer_id})。")
        except (FileConnectionUnavailable, TransferDenied):
            if attempt:
                break
        except (ComputerUnavailable, TransferUnavailable, OSError, ValueError):
            break
        finally:
            if issued is not None:
                computer_transfers.abort(current, issued["transfer_id"])

        replacement = await reconnect_transfer(current, deadline, TRANSFER_PROTOCOL, still_current)
        if replacement is None:
            break
        if issued is not None:
            await computer_transfers.wait_retired(current, file, deadline)
        current = replacement
    return _blocked("传输未完成，请核对电脑连接、授权目录或文件版本后重新发起。")


async def _perform(connection, action: FileAction, intents: HomeFileIntents, still_current, on_wait):
    payload = asdict(action)
    write = action.operation == "create"
    monotonic_deadline = time.monotonic() + FILE_ACTION_SECONDS
    waiting = False
    while True:
        result = result_value(await connection.file_request("perform", payload), action)
        if result["status"] != "busy":
            return result
        # Only an exact, authenticated busy reply can authorize retrying this dispatch.
        if write and not await asyncio.to_thread(intents.set_waiting, connection.binding, action, waiting=True):
            raise FileConnectionUnavailable("派发状态已变化")
        if not waiting and on_wait is not None:
            try:
                on_wait()
            except Exception:  # UI observers cannot change an approved action.
                pass
        waiting = True
        remaining = min(action.deadline - time.time(), monotonic_deadline - time.monotonic())
        if remaining <= FILE_QUEUE_POLL_SECONDS:
            return None
        await asyncio.sleep(FILE_QUEUE_POLL_SECONDS)
        if min(action.deadline - time.time(), monotonic_deadline - time.monotonic()) <= 0 or not await still_current():
            return None
        check_tool_execution()
        if write and not await asyncio.to_thread(intents.set_waiting, connection.binding, action, waiting=False):
            raise FileConnectionUnavailable("原动作已过期或已有最终回执")


def computer_file_tools(root: str, *, on_wait=None) -> list[Tool]:
    root = str(Path(root).resolve())
    store = ConnectedComputers(root)
    intents = HomeFileIntents(root)

    async def identity():
        owner = current_owner_turn.get()
        if owner is None or owner.root != root or owner.file_identity is None:
            raise FileConnectionUnavailable(_OWNER)
        account = await asyncio.to_thread(store.identity)
        if not owner.matches(account):
            raise FileConnectionUnavailable("家的所有者或信任已变化。")
        return owner, account

    async def list_folders(args):
        try:
            _, account = await identity()
            if set(args) - {"computer_id"}:
                return _blocked("目录清单只接受电脑编号。")
            rows = await asyncio.to_thread(store.list)
            if "computer_id" in args:
                computer_id = identifier(args["computer_id"])
                connection = connections.file_connection(root, computer_id, account)
                if connection is None:
                    return _blocked(_OFFLINE)
                policy = policy_value(await connection.file_request("policy", {}))
                return json.dumps(policy["folders"], ensure_ascii=False)
            return json.dumps([{"computer_id": row["connector_id"], "name": row["name"]}
                               for row in rows if row["active"]
                               and connections.file_connection(root, row["connector_id"], account)],
                              ensure_ascii=False)
        except (ComputerUnavailable, FileConnectionUnavailable, OSError, ValueError):
            return _blocked("无法读取电脑或授权目录。")

    async def action_status(args):
        try:
            _, account = await identity()
            object_fields(args, ("action_id",))
            action_id = identifier(args.get("action_id"))
            status = await asyncio.to_thread(intents.status, action_id, account)
            return _STATUS.get(status, "当前家没有这个动作的回执。")
        except (ComputerUnavailable, FileConnectionUnavailable, FileIntentUnavailable, OSError, ValueError):
            return _blocked("无法核对文件动作回执。")

    async def prepare(operation, name, args):
        try:
            owner, account = await identity()
            call_id = current_tool_call_id()
            if call_id is None:
                return _blocked("需要从家中的正式工具调用发起文件操作。")
            fields = {"computer_id", "folder_id", "path"}
            with_text = operation in ("create", "patch")
            delivering = operation == "deliver"
            if with_text:
                fields.add("text")
            if delivering:
                fields.add("source_path")
            object_fields(args, fields)
            computer_id = identifier(args.get("computer_id"))
            folder_id = identifier(args.get("folder_id"))
            path = args.get("path")
            body = args.get("text", "") if with_text else ""
            relative_parts(path)
            data = text_bytes(body)
            connection = connections.file_connection(root, computer_id, account)
            if connection is None:
                return _blocked(_OFFLINE)
            if operation == "transfer" and connection.protocol < TRANSFER_PROTOCOL:
                return _blocked("这台电脑需要支持大文件传输的连接器版本。")
            if delivering and connection.protocol < DELIVERY_PROTOCOL:
                return _blocked("这台电脑需要支持从家接收文件的连接器版本。")
            rows = await asyncio.to_thread(store.list)
            machine = next((row for row in rows if row["active"] and row["connector_id"] == computer_id), None)
            if machine is None:
                return _blocked(_OFFLINE)
            policy = policy_value(await connection.file_request("policy", {}))
            folder = next((row for row in policy["folders"] if row["folder_id"] == folder_id), None)
            if (folder is None or (operation not in ("create", "deliver") and not folder["read"])
                    or ((with_text or delivering) and not folder["write"])):
                return _blocked("本机没有授予该文件夹所需的权限。")
            target = {"folder_id": folder_id, "policy_revision": policy["policy_revision"], "path": path}
            if operation == "transfer":
                info = transfer_manifest(await connection.file_request("transfer_preview", target))
                version = info["version"]
            else:
                preview_operation = "create" if delivering else operation
                preview = await connection.file_request("preview", {**target, "operation": preview_operation})
                version = version_value(preview, preview_operation)
                if delivering:
                    try:
                        source = await asyncio.to_thread(outgoing_source, root, args["source_path"])
                        info = await asyncio.to_thread(source.transfer_info, MAX_TRANSFER_BYTES)
                    except (OSError, ValueError):
                        return _blocked("无法核对家的发件目录或源文件版本。")
        except (ComputerUnavailable, FileConnectionUnavailable, OSError, ValueError):
            return _blocked("无法核对电脑、目标文件或本机授权。")

        verb = {"read": "读取文件", "create": "新建文件", "patch": "预览文件差异",
                "transfer": "发送文件到家", "deliver": "接收文件并新建"}[operation]
        message = (f"允许仅此一次在电脑「{machine['name']}」（{computer_id}）{verb}？\n"
                   f"已授权目录「{folder['alias']}」（{folder_id}）\n相对路径 {path}\n"
                   f"预览版本 {version}")
        if with_text:
            excerpt = json.dumps(body[:320], ensure_ascii=False)
            message += (f"\n文本 {len(data)} 字节，SHA-256 {hashlib.sha256(data).hexdigest()}"
                        f"\n{'文本前 320 字' if len(body) > 320 else '文本'} {excerpt}")
        elif operation == "transfer":
            message += f"\n文件 {info['size']} 字节。传输成功后，家会提供仅所有者可取的临时下载。"
        elif delivering:
            message += (f"\n来自家的发件目录 {OUTBOX}/{args['source_path']}，文件 {info['size']} 字节"
                        f"\n源版本 {info['version']}\n目标只允许新建。")

        async def still_current(candidate=connection):
            try:
                active_owner, active_account = await identity()
                return (active_owner == owner and candidate.binding == connection.binding
                        and connections.file_connection(root, computer_id, active_account) is candidate
                        and await candidate.valid())
            except (ComputerUnavailable, FileConnectionUnavailable, OSError):
                return False

        async def invoke():
            try:
                if not await still_current():
                    return _blocked(_OFFLINE)
                if operation == "transfer":
                    return await _receive_file(connection, target, info, still_current)
                if delivering:
                    action_id, status = await deliver_file(
                        connection, source, info, target=target, destination_version=version, owner=owner,
                        call_id=call_id, intents=intents, deadline=int(time.time()) + FILE_TRANSFER_SECONDS,
                        still_current=still_current)
                    return ToolResult(f"{_STATUS[status]}。动作编号 {action_id}。",
                                      "succeeded" if status == "created" else "blocked")
                action = FileAction(uuid.uuid4().hex, folder_id, target["policy_revision"], operation, path,
                                    version, int(time.time()) + FILE_ACTION_SECONDS, owner.session, owner.rid, call_id, body)
            except (ComputerUnavailable, FileConnectionUnavailable, FileIntentUnavailable, TransferUnavailable,
                    HomeMaintenanceError, OSError, ValueError):
                return _blocked("无法完成文件操作，请核对家的授权、源文件版本和可用空间。")
            if operation == "create":
                try:
                    await asyncio.to_thread(intents.record, connection.binding, action)
                except FileIntentUnavailable:
                    return _blocked("家的派发记录不可用，未向电脑发送操作。")
            try:
                notify = partial(on_wait, call_id, name) if on_wait is not None else None
                result = await _perform(connection, action, intents, still_current, notify)
                if result is None:
                    return _blocked(f"本次等待已结束，电脑尚未开始。动作编号 {action.action_id}。")
                status = result["status"]
                if "text" in result:
                    return result["text"]
                receipt = {key: result[key] for key in ("action_id", "digest", "status")}
                if operation == "create" and not await connection.receipt(receipt):
                    status = "unknown"
            except (ComputerUnavailable, FileConnectionUnavailable, FileIntentUnavailable, OSError, ValueError):
                status = "unknown"
            if operation != "create" and status == "unknown":
                return _blocked("电脑没有返回可核对的文件内容。")
            return ToolResult(f"{_STATUS[status]}。动作编号 {action.action_id}。",
                              "succeeded" if status == "created" else "blocked")

        return PreparedToolCall(invoke, message, approval_context=lambda: file_confirmation(owner))

    target_args = {"computer_id": "电脑编号", "folder_id": "本机授权目录编号", "path": "目录内相对路径"}

    def file_tool(operation, name, description):
        args = dict(target_args)
        if operation in ("create", "patch"):
            args["text"] = "不超过 32 KiB 的 UTF-8 文本"
        if operation == "deliver":
            args["source_path"] = "家的发件目录内相对路径"
        writes = operation in ("create", "deliver")
        return Tool.prepared(name, description, args, partial(prepare, operation, name),
                             operation_kind=WRITE if writes else READ,
                             read_only=not writes, always_confirm=True, required_args=tuple(args), **_SOURCE)

    return [
        Tool("connected_computer_folders", "在家的主要聊天中列出支持文件操作的已连接电脑；传电脑编号后列出本机已授权的目录。",
             {"computer_id": "可选，已连接电脑编号"}, list_folders, operation_kind=READ, **_SOURCE),
        file_tool("read", "connected_computer_read", "经所有者逐次确认，读取连接电脑已授权目录的小文本文件。"),
        file_tool("create", "connected_computer_create", "经所有者逐次确认，在连接电脑选定目录中新建小文本文件。已有文件保持原样。结果未知时用回执查询。"),
        file_tool("patch", "connected_computer_patch_preview", "经所有者逐次确认，比较连接电脑的原文本和提议文本，只产生差异。"),
        file_tool("transfer", "connected_computer_transfer", "经所有者逐次确认，从连接电脑的授权目录将文件发送到家，返回临时下载链接。文件正文不进入对话。"),
        file_tool("deliver", "connected_computer_deliver", "经所有者逐次确认，从家的发件目录发送文件，在连接电脑已授权的目录中新建。已有文件保持原样。"),
        Tool("connected_computer_file_status", "按动作编号查已派发新建或发件操作的持久回执。",
             {"action_id": "新建或发件操作的动作编号"}, action_status, operation_kind=READ, required_args=("action_id",)),
    ]
