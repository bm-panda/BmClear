from __future__ import annotations

import unicodedata
from datetime import datetime
from typing import Iterable, Optional

from .fsutil import OptionOutcome
from .options import OPTION_MAP, OPTIONS


_UNITS = ("B", "KB", "MB", "GB", "TB")


def format_bytes(value: int) -> str:
    amount = float(value or 0)
    sign = "-" if amount < 0 else ""
    amount = abs(amount)
    for unit in _UNITS:
        if amount < 1024 or unit == _UNITS[-1]:
            if unit == "B":
                return f"{sign}{int(amount)} B"
            return f"{sign}{amount:.1f} {unit}"
        amount /= 1024
    return f"{sign}{amount:.1f} TB"


def char_width(char: str) -> int:
    if unicodedata.combining(char):
        return 0
    return 2 if unicodedata.east_asian_width(char) in ("W", "F") else 1


def display_width(text: str) -> int:
    return sum(char_width(char) for char in text)


def pad(text: str, width: int, align: str = "left") -> str:
    gap = width - display_width(text)
    if gap <= 0:
        return text
    if align == "right":
        return " " * gap + text
    if align == "center":
        left = gap // 2
        return " " * left + text + " " * (gap - left)
    return text + " " * gap


def render_table(headers, rows, aligns=None) -> str:
    columns = len(headers)
    aligns = aligns or ["left"] * columns
    widths = [display_width(str(header)) for header in headers]
    for row in rows:
        for index, cell in enumerate(row):
            widths[index] = max(widths[index], display_width(str(cell)))
    lines = []
    lines.append(
        "  ".join(pad(str(header), widths[i], aligns[i]) for i, header in enumerate(headers))
    )
    lines.append("  ".join("-" * widths[i] for i in range(columns)))
    for row in rows:
        lines.append(
            "  ".join(pad(str(cell), widths[i], aligns[i]) for i, cell in enumerate(row))
        )
    return "\n".join(lines)


def option_name(option_id: str) -> str:
    option = OPTION_MAP.get(option_id)
    return option.name if option else option_id


def option_status(outcome: OptionOutcome, dry_run: bool) -> str:
    result = outcome.result
    has_output = bool(result.bytes or result.files or result.actions)
    if result.errors and has_output:
        return "部分失败"
    if result.errors:
        return "失败"
    if has_output:
        return "预览" if dry_run else "完成"
    return "无匹配"


def render_option_list() -> str:
    rows = []
    for option in OPTIONS:
        rows.append(
            [
                option.id,
                option.name,
                "是" if option.default else "否",
                "是" if option.risky else "",
                "是" if option.admin else "",
            ]
        )
    return render_table(
        ["ID", "名称", "默认", "风险", "管理员"],
        rows,
        ["left", "left", "center", "center", "center"],
    )


def render_matched(entries) -> str:
    rows = [[entry.name, entry.file_keys, entry.reg_keys] for entry in entries]
    return render_table(["软件条目", "FileKey", "RegKey"], rows, ["left", "right", "right"])


def matched_report(entries, glob_enabled: bool) -> dict:
    return {
        "matchedEntries": len(entries),
        "globEnabled": glob_enabled,
        "entries": [
            {"name": entry.name, "fileKeys": entry.file_keys, "regKeys": entry.reg_keys}
            for entry in entries
        ],
    }


def build_report(
    mode: str,
    outcomes: Iterable[OptionOutcome],
    disk_before: Optional[int],
    disk_after: Optional[int],
    elapsed: float,
) -> dict:
    options = []
    total_bytes = 0
    total_files = 0
    total_errors = 0
    winapp2_stats = None
    for outcome in outcomes:
        status = option_status(outcome, mode == "scan")
        options.append(
            {
                "id": outcome.id,
                "name": option_name(outcome.id),
                "status": status,
                "matchBytes": outcome.result.bytes,
                "files": outcome.result.files,
                "errors": list(outcome.result.errors),
                "skipped": list(outcome.result.skipped),
                "actions": list(outcome.result.actions),
            }
        )
        total_bytes += outcome.result.bytes
        total_files += outcome.result.files
        total_errors += len(outcome.result.errors)
        if outcome.winapp2 is not None:
            stats = outcome.winapp2
            winapp2_stats = {
                "totalEntries": stats.total_entries,
                "matchedEntries": stats.matched_entries,
                "registryEnabled": stats.registry_enabled,
                "globEnabled": stats.glob_enabled,
            }
    delta = None
    if disk_before is not None and disk_after is not None:
        delta = disk_after - disk_before
    report = {
        "mode": mode,
        "dryRun": mode == "scan",
        "generatedAt": datetime.now().astimezone().isoformat(timespec="seconds"),
        "drive": "C:",
        "disk": {"before": disk_before, "after": disk_after, "delta": delta},
        "options": options,
        "total": {
            "matchBytes": total_bytes,
            "files": total_files,
            "errors": total_errors,
        },
        "elapsedMs": int(elapsed * 1000),
    }
    if winapp2_stats is not None:
        report["winapp2"] = winapp2_stats
    return report


def render_report(report: dict, verbose: bool = False) -> str:
    dry_run = report.get("dryRun", False)
    rows = []
    for option in report["options"]:
        rows.append(
            [
                option["name"],
                option["status"],
                format_bytes(option["matchBytes"]),
                option["files"],
                len(option["errors"]),
            ]
        )
    lines = [
        render_table(
            ["清理项", "状态", "匹配大小", "文件数", "错误"],
            rows,
            ["left", "left", "right", "right", "right"],
        )
    ]
    total = report["total"]
    lines.append("")
    lines.append(
        f"合计: {format_bytes(total['matchBytes'])} / {total['files']} 个文件 / {total['errors']} 个错误"
    )
    disk = report["disk"]
    before = disk.get("before")
    after = disk.get("after")
    delta = disk.get("delta")
    if after is not None:
        lines.append(
            f"C 盘可用: {format_bytes(before) if before is not None else '未知'} -> "
            f"{format_bytes(after)}（释放 {format_bytes(delta) if delta is not None else '未知'}）"
        )
    else:
        lines.append(f"C 盘可用: {format_bytes(before) if before is not None else '未知'}")
    if "winapp2" in report:
        stats = report["winapp2"]
        lines.append(
            f"Winapp2: 命中 {stats['matchedEntries']}/{stats['totalEntries']} 条"
            f"，注册表{'启用' if stats['registryEnabled'] else '禁用'}"
            f"，通配符{'启用' if stats['globEnabled'] else '禁用'}"
        )
    lines.append(f"耗时: {report['elapsedMs'] / 1000:.1f}s")
    errors = []
    for option in report["options"]:
        for message in option["errors"]:
            errors.append(f"{option['name']}: {message}")
    if errors:
        lines.append("")
        limit = len(errors) if verbose else 20
        lines.append(f"错误明细（共 {len(errors)} 条，显示 {min(limit, len(errors))} 条）:")
        for message in errors[:limit]:
            lines.append(f"  - {message}")
    if dry_run:
        lines.append("")
        lines.append("这是预览模式，未删除任何文件。使用 clean 子命令执行清理。")
    return "\n".join(lines)
