"""Closed protocol for folder metadata and approved small-file actions."""
from __future__ import annotations

from dataclasses import fields
import json
import re

from src.gateway.computer_file_paths import FileDenied, relative_parts, text_bytes
from src.gateway.computer_file_store import ComputerFiles, FileAction, FileDelivery, FileStateUnavailable, MAX_GRANTS
from src.gateway.computer_protocol import MAX_FRAME_BYTES, MAX_TRANSFER_BYTES, binding_fields, identifier

_DIGEST = re.compile(r"[a-f0-9]{64}\Z")
_VERSION = re.compile(r"(?:absent:)?[a-f0-9]{64}\Z")
TERMINAL_RECEIPTS = frozenset({"created", "blocked", "unknown"})


def object_fields(value, keys):
    if not isinstance(value, dict) or set(value) != set(keys):
        raise ValueError("invalid computer file fields")
    return value


def digest_value(value):
    if not isinstance(value, str) or not _DIGEST.fullmatch(value):
        raise ValueError("invalid file action digest")
    return value


def bounded_frame(value: dict) -> dict:
    # Validate the byte budget in the same encoding used by both websocket writers.
    if len(json.dumps(value, ensure_ascii=True)) > MAX_FRAME_BYTES:
        raise ValueError("oversized computer file frame")
    return value


def policy_value(value: dict) -> dict:
    object_fields(value, ("policy_revision", "folders"))
    identifier(value["policy_revision"])
    folders = value["folders"]
    if not isinstance(folders, list) or len(folders) > MAX_GRANTS:
        raise ValueError("invalid computer folder list")
    seen = set()
    for entry in folders:
        object_fields(entry, ("folder_id", "alias", "read", "write"))
        folder_id = identifier(entry["folder_id"])
        alias = entry["alias"]
        if (folder_id in seen or not isinstance(alias, str) or not 1 <= len(alias) <= 80
                or not alias.isprintable() or type(entry["read"]) is not bool
                or type(entry["write"]) is not bool or not (entry["read"] or entry["write"])):
            raise ValueError("invalid computer folder")
        seen.add(folder_id)
    return value


def preview_value(body: dict) -> dict:
    object_fields(body, ("operation", "folder_id", "policy_revision", "path"))
    identifier(body["folder_id"])
    identifier(body["policy_revision"])
    relative_parts(body["path"])
    if body["operation"] not in ("read", "create", "patch"):
        raise ValueError("unsupported file operation")
    return body


def version_value(value: dict, operation: str) -> str:
    object_fields(value, ("version",))
    version = value["version"]
    if (not isinstance(version, str) or not _VERSION.fullmatch(version)
            or version.startswith("absent:") != (operation == "create")):
        raise ValueError("invalid preview version")
    return version


def receipt_value(value: dict) -> dict:
    object_fields(value, ("action_id", "digest", "status"))
    identifier(value["action_id"])
    digest_value(value["digest"])
    if not isinstance(value["status"], str) or value["status"] not in TERMINAL_RECEIPTS:
        raise ValueError("invalid file receipt")
    return value


def result_value(value: dict, action: FileAction) -> dict:
    if not isinstance(value, dict):
        raise ValueError("invalid file action result")
    status = value.get("status")
    keys = {"action_id", "digest", "status"}
    if (value.get("action_id"), value.get("digest")) != (action.action_id, action.digest):
        raise ValueError("file result does not match action")
    if status in ("read", "patch"):
        if action.operation != status:
            raise ValueError("invalid read/patch result")
        object_fields(value, keys | {"text"})
        text_bytes(value["text"])
    else:
        object_fields(value, keys)
        if status not in ("blocked", "busy", "unknown") and (action.operation, status) != ("create", "created"):
            raise ValueError("unsupported file result")
    return value


def action_value(value: dict, *, delivery: bool = False) -> FileAction:
    kind = FileDelivery if delivery else FileAction
    object_fields(value, (field.name for field in fields(kind)))
    return kind(**value)


def delivery_payload(body: dict) -> dict:
    object_fields(body, ("action", "ticket"))
    action_value(body["action"], delivery=True)
    transfer_ticket(body["ticket"])
    return body


def transfer_ticket(value):
    if not isinstance(value, str) or not re.fullmatch(r"vct_[A-Za-z0-9_-]{43}", value):
        raise ValueError("invalid transfer ticket")


def transfer_manifest(value: dict, *, hashed: bool = False) -> dict:
    keys = {"version", "size"}
    if hashed:
        keys.add("sha256")
    object_fields(value, keys)
    digest_value(value["version"])
    if type(value["size"]) is not int or not 0 <= value["size"] <= MAX_TRANSFER_BYTES:
        raise ValueError("invalid transfer size")
    if hashed:
        digest_value(value["sha256"])
    return value


def transfer_payload(body: dict, method: str) -> dict:
    keys = {"folder_id", "policy_revision", "path"}
    if method != "transfer_preview":
        keys.update(("version", "size", "deadline"))
    if method == "transfer_upload":
        keys.update(("sha256", "transfer_id", "ticket"))
    object_fields(body, keys)
    identifier(body["folder_id"])
    identifier(body["policy_revision"])
    relative_parts(body["path"])
    if method != "transfer_preview":
        transfer_manifest({"version": body["version"], "size": body["size"]})
        if type(body["deadline"]) is not int or not 0 < body["deadline"] < 2 ** 53:
            raise ValueError("invalid transfer deadline")
    if method == "transfer_upload":
        digest_value(body["sha256"])
        identifier(body["transfer_id"])
        transfer_ticket(body["ticket"])
    return body


def staged_transfer(value: dict, *, transfer_id: str, size: int, sha256: str) -> dict:
    object_fields(value, ("transfer_id", "size", "sha256", "status"))
    if (type(value["size"]) is not int or
            value != {"transfer_id": transfer_id, "size": size, "sha256": sha256, "status": "staged"}):
        raise ValueError("transfer receipt mismatch")
    return value


def native_file_request(frame: dict, generation: str, binding: dict, *,
                        transfers: bool = False, deliveries: bool = False) -> tuple[str, str, dict | FileAction]:
    object_fields(frame, ("type", "generation", "request_id", "binding", "method", "payload"))
    if frame["generation"] != generation or frame["binding"] != binding_fields(binding):
        raise ValueError("stale file request")
    request_id = identifier(frame["request_id"])
    method = frame["method"]
    body = frame["payload"]
    if method == "policy":
        object_fields(body, ())
    elif method == "preview":
        preview_value(body)
    elif method == "perform":
        body = action_value(body)
    elif transfers and method in ("transfer_preview", "transfer_hash", "transfer_upload"):
        transfer_payload(body, method)
    elif deliveries and method == "transfer_download":
        delivery_payload(body)
    else:
        raise ValueError("unsupported file request")
    return request_id, method, body


def file_reply(request_id: str, generation: str, result: dict | None) -> dict:
    return bounded_frame({"type": "file_reply", "generation": generation, "request_id": request_id,
                          "ok": result is not None, "result": result if result is not None else {}})


def native_file_preflight(store: ComputerFiles, request, generation: str) -> dict:
    request_id, method, body = request
    try:
        if method == "policy":
            data = policy_value(store.policy())
        elif method == "preview":
            data = {"version": store.preview(**body)}
        else:
            raise ValueError("invalid file preflight")
    except (FileDenied, FileStateUnavailable, OSError):
        data = None
    return file_reply(request_id, generation, data)
