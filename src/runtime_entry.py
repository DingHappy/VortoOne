"""Internal service and diagnostics entry. Public interactive commands live in src.cli during migration."""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path
from collections.abc import Sequence

from src.runtime_bootstrap import prepare_runtime
from src.runtime_jobs import JOB_COMMANDS, add_job_commands, validate_job_args, run_job_command
from src.utils.state_dir import state_dir


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="vortoone-runtime", description="VortoOne internal service runner"
    )
    commands = parser.add_subparsers(dest="command", required=True)
    server = commands.add_parser("server", help="start the existing Gateway service")
    server.add_argument("--host", default="127.0.0.1")
    server.add_argument("--port", type=int, default=8080)
    server.add_argument("--im", choices=["telegram", "dingtalk"], default="")
    im = commands.add_parser("im", help="start the existing standalone IM bridge")
    im.add_argument("channel", choices=["telegram", "dingtalk"])
    im.add_argument("--mode", choices=["plan", "build"], default="plan")
    commands.add_parser("doctor", help="run existing diagnostics (may contact configured services)")
    commands.add_parser("canary", help="run offline delivery checks")
    inventory = commands.add_parser("legacy-inventory", help="read explicitly selected legacy launch/job files; never migrate them")
    inventory.add_argument("--path", action="append", required=True, help="explicit file or bounded launch/job directory")
    inventory.add_argument("--output", help="create a new private report; never overwrite an existing file")
    verify = commands.add_parser("legacy-verify", help="re-read declared inventory sources; never authorize removal")
    verify.add_argument("report")
    export = commands.add_parser("home-export", help="把当前目录的家导出成搬家包（不带凭据与绑在这台电脑上的东西）")
    export.add_argument("output", help="新建的 .tar.gz 文件，不覆盖已有文件")
    export.add_argument("--move", action="store_true", help="搬走：导出后这里的家停用，服务不再启动")
    load = commands.add_parser("home-import", help="把搬家包导入到当前目录的家（先停掉服务）")
    load.add_argument("package")
    load.add_argument("--replace", action="store_true", help="这里已有数据时替换；原来的挪到旁边留着")
    add_job_commands(commands)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    validate_job_args(parser, args)
    if args.command in {"legacy-inventory", "legacy-verify"}:
        import json
        from src.cli_migration import inventory, verify_report, write_report

        try:
            if args.command == "legacy-verify":
                report = verify_report(args.report)
                code = 2 if not report["complete_for_declared_inputs"] else 0 if report["unchanged"] else 1
            else:
                report = inventory(args.path)
                code = 0 if report["complete_for_declared_inputs"] else 2
                if args.output:
                    write_report(args.output, report)
                    report = {key: report[key] for key in ("schema_version", "review_digest", "legacy_references", "manual_reviews", "complete_for_declared_inputs", "retirement_authorized", "runtime_migration_verified")}
            print(json.dumps(report, ensure_ascii=False, sort_keys=True))
            return code
        except (OSError, ValueError, TypeError, RecursionError):
            print(json.dumps({"schema_version": 1, "error": "inventory_or_report_unavailable", "retirement_authorized": False, "runtime_migration_verified": False}))
            return 2
    if args.command in {"home-export", "home-import"}:
        return run_home_move(args)
    prepare_runtime()
    if args.command in JOB_COMMANDS:
        return run_job_command(args)
    if args.command == "server":
        run_server(args.host, args.port, im=args.im)
        return 0
    if args.command == "im":
        asyncio.run(run_im(args.channel, mode=args.mode))
        return 0
    if args.command == "doctor":
        return asyncio.run(run_doctor())
    if args.command == "canary":
        return asyncio.run(run_canary_cli())
    raise AssertionError(f"unsupported internal command: {args.command}")


async def run_doctor() -> int:
    """一键自检：逐项查管道（git/凭证/中转站/serve/权限/gh/IM），返回退出码（硬伤=1）。"""
    from src.gateway.doctor import run_checks, summarize

    print("VortoOne doctor · 自检中…\n", file=sys.stderr)
    checks = await run_checks(str(Path.cwd()))
    text, code = summarize(checks)
    print(text)
    return code


async def run_canary_cli() -> int:
    """端到端交付验收：逐条报告哪条交付链路通、哪条断。有断的返回 1（部署脚本据此拦住上线）。"""
    from src.gateway.canary import run_canary, summarize

    print("VortoOne canary · 端到端交付验收（假通道 + 脚本模型，不触网）…\n", file=sys.stderr)
    lanes = await run_canary()
    for ln in lanes:
        print(f"{ln.glyph} {ln.name:<12} {ln.detail}")
    ok, text = summarize(lanes)
    print(f"\n{'✅' if ok else '⛔'} {text}")
    return 0 if ok else 1


async def run_im(channel: str, *, mode: str = "plan"):
    """IM 通道桥入口：常驻长轮询，把主 agent 搬上 IM。fail-closed：缺配对凭证直接拒启。"""
    cwd = str(Path.cwd())
    from src.gateway.im_service import (
        IMConfigError,
        build_adapter,
        load_allow_from,
        load_conversation,
        load_session_role,
    )

    try:
        adapter, owner = build_adapter(channel)  # 凭证解析/fail-closed 与 serve 内嵌同源，不抄两份
        conversation = load_conversation()
        session_role = load_session_role()
    except IMConfigError as e:
        print(f"✗ {e}", file=sys.stderr)
        sys.exit(2)
    from src.im.bridge import IMBridge

    bridge = IMBridge(
        cwd,
        adapter,
        owner,
        channel=channel,
        mode=mode,  # standalone：自己的 runner
        allow_from=load_allow_from(channel),
        conversation=conversation,
        session_role=session_role,
    )
    who = "、".join(sorted(bridge.allow_from)) if bridge.allow_from else "空 → 拒绝一切入站"
    print(
        f"🌉 {channel} 桥启动（仓库 {Path(cwd).name}，{mode} 模式）。入站白名单：{who}；"
        f"群聊须显式 @ 本机器人。Ctrl-C 退出。",
        file=sys.stderr,
    )
    warn = bridge.allow_from_warning()  # 白名单配错（空 / 漏了 owner）→ 开机就在终端讲清楚：
    if warn:  # 不是坏了，是配置把你自己也挡在门外了
        print(warn, file=sys.stderr)
    from src.gateway.process_lease import ProcessLease

    try:
        with ProcessLease(state_dir(cwd) / "runtime.lock"):
            try:
                await bridge.run()
            finally:
                if bridge._runner is not None:
                    await bridge._runner.shutdown()
    finally:
        await adapter.close()


def run_home_move(args) -> int:
    """搬家的导出与导入；结果以一行 JSON 打出（Desktop 读它），失败退出码 2。"""
    import json
    from src.workflow.home_move import MoveError, export_home, import_home
    try:
        if args.command == "home-export":
            result = export_home(str(Path.cwd()), args.output, moving=args.move)
        else:
            result = import_home(str(Path.cwd()), args.package, replace=args.replace)
    except (MoveError, OSError) as error:
        print(json.dumps({"ok": False, "error": str(error)}, ensure_ascii=False))
        return 2
    print(json.dumps({"ok": True, **result}, ensure_ascii=False))
    return 0


def refuse_if_moved(root: str) -> None:
    """搬走了的家不再启动服务：同一个空间不能在两个地方同时接活。"""
    from src.workflow.home_move import moved
    found = moved(root)
    if found is not None:
        print(f"这个家已经搬走了（{found.get('at') or '时间不详'}），这里不再启动服务。"
              f"要在这里继续用，把搬家包导入回来，或删除 .vortoone/moved.json。", file=sys.stderr)
        raise SystemExit(3)


def run_server(host: str, port: int, im: str = ""):
    """运行服务器（im 非空 = 内嵌 IM 桥，见 gateway/im_service）"""
    from src.web.server import start_server
    refuse_if_moved(str(Path.cwd()))

    start_server(host, port, im=im)


if __name__ == "__main__":
    raise SystemExit(main())
