"""Private native settings pipe. This entrypoint cannot run a file action."""
from __future__ import annotations

import json
from pathlib import Path
import sys

from src.gateway.computer_file_paths import FileDenied
from src.gateway.computer_file_store import ComputerFiles, FileStateUnavailable
from src.gateway.computer_protocol import BINDING_FIELDS, ComputerProtocolError, decode_frame


def main() -> int:
    try:
        # The caller selects cwd from the app-private data directory, never the webview.
        raw = sys.stdin.buffer.readline(8193)
        if len(raw) > 8192 or not raw.endswith(b"\n"):
            raise FileDenied("本机设置请求无效")
        command = decode_frame(raw)
        operation, binding = command.get("operation"), command.get("binding")
        if not isinstance(binding, dict) or set(binding) != set(BINDING_FIELDS):
            raise FileDenied("电脑配对已变化")
        store = ComputerFiles(Path.cwd() / "file-access", binding)
        keys = {"operation", "binding"}
        if operation == "grant" and set(command) == keys | {"directory", "alias", "read", "write", "directory_identity"}:
            identity = command["directory_identity"]
            if (not isinstance(identity, list) or len(identity) != 2
                    or not all(isinstance(value, str) and value.isascii() and value.isdecimal()
                               and len(value) <= 24 for value in identity)):
                raise FileDenied("所选文件夹身份无效")
            store.grant(command["directory"], command["alias"], read=command["read"], write=command["write"],
                        expected_directory=(identity[0], identity[1]))
        elif operation == "revoke" and set(command) == keys | {"folder_id"}:
            store.revoke(command["folder_id"])
        elif operation != "list" or set(command) != keys:
            raise FileDenied("本机设置请求无效")
        result = {"ok": True, **store.policy(local_paths=True)}
    except FileDenied as error:
        result = {"ok": False, "error": str(error)}
    except (FileStateUnavailable, ComputerProtocolError, OSError, TypeError, ValueError, RecursionError):
        result = {"ok": False, "error": "本机文件夹设置暂时不可用"}
    print(json.dumps(result, ensure_ascii=False), flush=True)
    return 0 if result["ok"] else 2
