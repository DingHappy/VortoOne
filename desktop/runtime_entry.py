#!/usr/bin/env python3
"""Minimal entry point for the VortoOne Desktop bundled runtime.

The sidecar exposes the local Gateway and native-managed computer/restore
commands. The internal ``src.runtime_entry`` also handles service diagnostics.
Public ``vc`` wrappers are retained temporarily while their callers migrate.
"""

from __future__ import annotations

import argparse
import os
from collections.abc import Sequence


def local_port(value: str) -> int:
    try:
        port = int(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("port must be an integer") from error
    if not 1024 <= port <= 65535:
        raise argparse.ArgumentTypeError("port must be between 1024 and 65535")
    return port


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="vortocode-runtime",
        description="VortoOne Desktop bundled local Gateway runtime",
    )
    parser.add_argument("--version", action="version", version="%(prog)s 0.1.0")
    subcommands = parser.add_subparsers(dest="command", required=True)
    subcommands.add_parser("computer-connector", help="native-managed outbound computer control worker")
    desktop_restore = subcommands.add_parser("desktop-home-restore", help="restore native Desktop home data without reviving computer trust")
    desktop_restore.add_argument("--data-dir", required=True)
    desktop_restore.add_argument("--archive", required=True)
    subcommands.add_parser(
        "model-capabilities", help="check bundled model client without inference or credentials"
    )
    subcommands.add_parser("resident-capabilities", help="report the local resident launch protocol")
    server = subcommands.add_parser("server", help="start the local Gateway server")
    server.add_argument("--host", choices=("127.0.0.1", "localhost"), default="127.0.0.1")
    server.add_argument("--port", type=local_port, default=8080)
    server.add_argument("--native-computer-control", action="store_true", help="read a native launch ticket from private stdin")
    resident = subcommands.add_parser(
        "resident-server", help="start the native-managed local resident"
    )
    resident.add_argument("--config", required=True)
    return parser


async def model_capabilities() -> dict:
    from importlib.metadata import version
    from src.llm.client import LLMClient, LLMConfig

    client = LLMClient(LLMConfig(base_url="http://127.0.0.1:1/v1", api_key="", model="diagnostic"))
    try:
        if await client._get_client() is None:
            raise RuntimeError(
                "Desktop native tool client unavailable: OpenAI-compatible SDK missing"
            )
        return {
            "version": 1,
            "native_tools_client": True,
            "openai": version("openai"),
            "httpx": version("httpx"),
            "inference_sent": False,
        }
    finally:
        await client.close()


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "computer-connector":
        from src.gateway.computer_connector import main as connector_main
        return connector_main()
    if args.command == "desktop-home-restore":
        from src.runtime_entry import run_desktop_restore
        return run_desktop_restore(args.data_dir, args.archive)
    if args.command == "resident-capabilities":
        import json

        print(json.dumps({"schema_version": 1, "configuration_schema": 1}))
        return 0
    if args.command == "model-capabilities":
        import asyncio
        import json

        print(json.dumps(asyncio.run(model_capabilities())))
        return 0
    if args.command == "server":
        from src.runtime_bootstrap import prepare_server_ownership
        prepare_server_ownership(os.getcwd())
        if args.native_computer_control:
            from src.gateway.computer_native import ComputerNativeDenied, ComputerNativeUnavailable, read_bootstrap
            try:
                read_bootstrap(os.getcwd())
            except (ComputerNativeDenied, ComputerNativeUnavailable, OSError, ValueError):
                raise SystemExit("本机启动票据不可用，服务未启动") from None
        os.environ["VORTOONE_DESKTOP_SIDECAR"] = "1"
        from src.web.server import start_server

        start_server(args.host, args.port)
        return 0
    if args.command == "resident-server":
        from desktop.local_resident_config import prepare_resident, prepare_resident_socket

        root, port = prepare_resident(args.config)
        endpoint = prepare_resident_socket(root)
        os.chdir(root)
        from src.web.server import start_server

        start_server("127.0.0.1", port, uds=str(endpoint))
        return 0
    raise AssertionError(f"unsupported runtime command: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())
