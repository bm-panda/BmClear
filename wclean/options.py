from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Optional


@dataclass(frozen=True)
class Option:
    id: str
    name: str
    description: str
    default: bool = True
    risky: bool = False
    admin: bool = False


OPTIONS = (
    Option("prefetch", "预读取缓存", "清理 C:\\Windows\\Prefetch 中的 .pf 文件。", True, False, True),
    Option("user_temp", "用户临时文件", "清理当前用户 TEMP 目录内容。", True, False, False),
    Option("windows_temp", "系统临时文件", "清理 C:\\Windows\\Temp 中的内容。", True, False, True),
    Option("system_logs", "系统日志", "清理 C:\\Windows\\Logs 中的内容。", True, False, True),
    Option("software_distribution", "更新下载缓存", "清理 Windows Update 下载缓存。", True, False, True),
    Option("browser_cache", "浏览器缓存", "清理 Chrome 和 Edge 的默认缓存目录。", True, False, False),
    Option("temp_files_c", "C 盘临时文件", "递归清理 C 盘上的 .tmp 和 .cache 文件。", True, False, True),
    Option("log_files_c", "C 盘日志文件", "递归清理 C 盘上的 .log 文件。", False, False, True),
    Option("restore_points", "系统还原点", "删除 C 盘系统还原点。", True, True, True),
    Option("hibernation", "休眠文件", "关闭系统休眠并释放 hiberfil.sys。", True, True, True),
    Option("delivery_optimization", "分发优化缓存", "清理 Delivery Optimization 下载缓存。", True, False, True),
    Option("thumbnails", "缩略图缓存", "清理 Explorer 缩略图数据库。", True, False, False),
    Option("defender", "Defender 扫描历史", "清理 Microsoft Defender 扫描历史和缓存。", True, False, True),
    Option("inet_cache", "INetCache", "清理 Internet 临时文件缓存。", True, False, False),
    Option("wer_reports", "错误报告", "清理 Windows Error Reporting 报告。", True, False, True),
    Option("shader_cache", "着色器缓存", "清理 DirectX 和显卡驱动着色器缓存。", True, False, False),
    Option("nvidia_debug_logs", "NVIDIA 调试日志", "清理 NVIDIA debug.log 文件。", True, False, True),
    Option("wps_backups", "WPS 备份", "清理 WPS 备份目录。", True, False, False),
    Option("winapp2", "软件缓存", "按 Winapp2.ini 规则清理常用软件缓存。", True, False, False),
    Option("recycle_bin", "回收站", "清空 C 盘回收站中的所有文件。", False, True, True),
    Option("msp_files", "清理 .msp 文件", "递归清理磁盘上的 Windows Installer patch 文件。", False, True, True),
)

OPTION_MAP = {option.id: option for option in OPTIONS}
ALL_OPTION_IDS = tuple(option.id for option in OPTIONS)

QUICK_OPTION_IDS = (
    "prefetch",
    "user_temp",
    "windows_temp",
    "system_logs",
    "software_distribution",
    "browser_cache",
    "temp_files_c",
    "restore_points",
    "hibernation",
    "delivery_optimization",
    "thumbnails",
    "defender",
    "inet_cache",
    "wer_reports",
    "shader_cache",
    "nvidia_debug_logs",
    "wps_backups",
    "winapp2",
)

QUICK_SAFE_OPTION_IDS = tuple(
    option_id for option_id in QUICK_OPTION_IDS if not OPTION_MAP[option_id].risky
)


@dataclass
class Selection:
    ids: tuple[str, ...]
    gated: tuple[str, ...]
    excluded: tuple[str, ...]


def parse_option_ids(values: Iterable[str]) -> tuple[str, ...]:
    result = []
    seen = set()
    for value in values:
        for token in value.replace(",", " ").split():
            option_id = token.strip().lower()
            if not option_id or option_id in seen:
                continue
            seen.add(option_id)
            result.append(option_id)
    return tuple(result)


def resolve_selection(
    explicit: Iterable[str] = (),
    all_options: bool = False,
    quick: bool = False,
    include_risk: bool = False,
    exclude: Iterable[str] = (),
) -> Selection:
    explicit_ids = tuple(explicit)
    for option_id in explicit_ids:
        if option_id not in OPTION_MAP:
            raise ValueError(f"未知清理项: {option_id}")
    excluded_ids = tuple(exclude)
    for option_id in excluded_ids:
        if option_id not in OPTION_MAP:
            raise ValueError(f"未知排除项: {option_id}")

    exclude_set = set(excluded_ids)

    if explicit_ids:
        base = explicit_ids
    elif all_options:
        base = ALL_OPTION_IDS
    elif quick:
        base = QUICK_OPTION_IDS
    else:
        base = QUICK_SAFE_OPTION_IDS

    base = tuple(option_id for option_id in base if option_id not in exclude_set)
    if not include_risk:
        gated = tuple(option_id for option_id in base if OPTION_MAP[option_id].risky)
        selected = tuple(option_id for option_id in base if option_id not in set(gated))
    else:
        gated = ()
        selected = base

    return Selection(ids=selected, gated=gated, excluded=excluded_ids)


def format_ids(ids: Iterable[str]) -> str:
    return ", ".join(ids) if ids else "无"
