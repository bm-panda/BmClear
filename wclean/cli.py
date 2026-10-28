from __future__ import annotations

import argparse
import json
import sys
import time

from . import winapp2
from .cleaners import run_selection
from .options import (
    OPTION_MAP,
    format_ids,
    parse_option_ids,
    resolve_selection,
)
from .paths import c_free_bytes, default_exclusions, disk_usage, is_admin
from .report import (
    build_report,
    matched_report,
    render_matched,
    render_option_list,
    render_report,
)


def _configure_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except Exception:
            pass


def _add_run_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--options", action="append", default=[], metavar="IDS")
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--quick", action="store_true")
    parser.add_argument("--include-risk", action="store_true")
    parser.add_argument("--exclude", action="append", default=[], metavar="IDS")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--verbose", "-v", action="store_true")
    parser.add_argument("--quiet", "-q", action="store_true")
    parser.add_argument("--no-exclusions", action="store_true")
    parser.add_argument("--no-registry", action="store_true")
    parser.add_argument("--no-winapp2-glob", action="store_true")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="wclean", description="Windows C 盘清理命令行工具"
    )
    subparsers = parser.add_subparsers(dest="command")

    subparsers.add_parser("list", help="列出全部清理项")

    info = subparsers.add_parser("info", help="显示 C 盘容量")
    info.add_argument("--json", action="store_true")

    for name, help_text in (
        ("scan", "预览可清理内容（只读）"),
        ("clean", "执行清理"),
    ):
        sub = subparsers.add_parser(name, help=help_text)
        _add_run_args(sub)
        if name == "clean":
            sub.add_argument("--yes", action="store_true", help="跳过确认")
            sub.add_argument(
                "--require-admin", action="store_true", help="非管理员时直接退出"
            )

    win = subparsers.add_parser("winapp2", help="Winapp2.ini 相关操作")
    win_sub = win.add_subparsers(dest="winapp2_command")
    win_list = win_sub.add_parser("list", help="列出 Winapp2 条目")
    win_list.add_argument("--matched", action="store_true")
    win_list.add_argument("--json", action="store_true")
    win_list.add_argument("--no-winapp2-glob", action="store_true")
    return parser


def _make_progress(quiet: bool):
    if quiet or not sys.stderr.isatty():
        return None
    state = {"last": 0.0}

    def callback(count: int) -> None:
        now = time.monotonic()
        if now - state["last"] < 1.0:
            return
        state["last"] = now
        print(f"\r已扫描 {count} 个文件...", end="", file=sys.stderr, flush=True)

    return callback


def _cmd_list() -> int:
    print(render_option_list())
    return 0


def _cmd_info(args) -> int:
    usage = disk_usage()
    if usage is None:
        print("无法读取 C 盘容量", file=sys.stderr)
        return 1
    data = {
        "drive": "C:",
        "total": usage.total,
        "used": usage.used,
        "free": usage.free,
    }
    if args.json:
        print(json.dumps(data, ensure_ascii=False, indent=2))
        return 0
    from .report import format_bytes, render_table

    rows = [
        ["总容量", format_bytes(usage.total)],
        ["已使用", format_bytes(usage.used)],
        ["可用", format_bytes(usage.free)],
    ]
    print(render_table(["项目", "大小"], rows, ["left", "right"]))
    return 0


def _cmd_winapp2(args, parser: argparse.ArgumentParser) -> int:
    if not getattr(args, "winapp2_command", None):
        print("用法: wclean winapp2 list [--matched] [--json] [--no-winapp2-glob]", file=sys.stderr)
        return 2
    glob_enabled = not args.no_winapp2_glob
    if args.matched:
        entries = winapp2.list_matched(glob_enabled=glob_enabled)
    else:
        entries = [
            winapp2.MatchedEntry(
                name=entry.name,
                file_keys=len(entry.file_keys),
                reg_keys=len(entry.reg_keys),
            )
            for entry in winapp2.load_entries()
        ]
    if args.json:
        print(
            json.dumps(
                matched_report(entries, glob_enabled), ensure_ascii=False, indent=2
            )
        )
        return 0
    print(f"条目数: {len(entries)}（通配符{'启用' if glob_enabled else '禁用'}）")
    print(render_matched(entries))
    return 0


def _cmd_run(args) -> int:
    explicit = parse_option_ids(args.options)
    exclude = parse_option_ids(args.exclude)
    try:
        selection = resolve_selection(
            explicit=explicit,
            all_options=args.all,
            quick=args.quick,
            include_risk=args.include_risk,
            exclude=exclude,
        )
    except ValueError as exc:
        print(f"错误: {exc}", file=sys.stderr)
        return 2

    if not selection.ids:
        print("没有可执行的清理项。", file=sys.stderr)
        return 2

    if selection.gated and not args.quiet:
        print(
            f"已跳过高风险项（加 --include-risk 启用）: {format_ids(selection.gated)}",
            file=sys.stderr,
        )

    if not is_admin():
        admin_ids = [option_id for option_id in selection.ids if OPTION_MAP[option_id].admin]
        if admin_ids and not args.quiet:
            print(
                f"警告: 当前非管理员，以下项可能失败: {format_ids(admin_ids)}",
                file=sys.stderr,
            )
        if getattr(args, "require_admin", False):
            print("错误: 需要管理员权限。", file=sys.stderr)
            return 2

    if args.command == "clean" and not args.yes:
        if not sys.stdin.isatty():
            print("错误: 非交互环境请加 --yes。", file=sys.stderr)
            return 2
        print(f"将执行 {len(selection.ids)} 个清理项: {format_ids(selection.ids)}")
        try:
            answer = input("确认清理? [y/N] ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print("\n已取消")
            return 130
        if answer not in ("y", "yes"):
            print("已取消")
            return 130

    exclusions = () if args.no_exclusions else default_exclusions()
    dry_run = args.command == "scan"
    before = c_free_bytes()
    progress = _make_progress(args.quiet)
    start = time.monotonic()
    try:
        outcomes = run_selection(
            selection.ids,
            dry_run=dry_run,
            exclusions=exclusions,
            verbose=args.verbose,
            progress=progress,
            use_registry=not args.no_registry,
            glob_enabled=not args.no_winapp2_glob,
        )
    except KeyboardInterrupt:
        print("\n已中断", file=sys.stderr)
        return 130
    finally:
        if progress is not None:
            print("", file=sys.stderr)
    after = None if dry_run else c_free_bytes()
    elapsed = time.monotonic() - start
    report = build_report(args.command, outcomes, before, after, elapsed)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(render_report(report, verbose=args.verbose))
    return 0 if report["total"]["errors"] == 0 else 1


def main(argv=None) -> int:
    _configure_stdio()
    parser = _build_parser()
    args = parser.parse_args(argv)
    if args.command is None:
        parser.print_help()
        return 2
    if sys.platform != "win32":
        print("错误: 该工具仅支持 Windows。", file=sys.stderr)
        return 2
    if args.command == "list":
        return _cmd_list()
    if args.command == "info":
        return _cmd_info(args)
    if args.command == "winapp2":
        return _cmd_winapp2(args, parser)
    if args.command in ("scan", "clean"):
        return _cmd_run(args)
    parser.print_help()
    return 2
