from __future__ import annotations

import os
from pathlib import Path
from typing import Iterable, Optional

from . import shell
from .fsutil import (
    CleanContext,
    OptionOutcome,
    clean_dir_contents,
    entry_is_dir,
    entry_stat,
    is_reparse,
    path_is_dir,
    read_entries,
    remove_file_into,
    walk_extensions,
    walk_extensions_multi,
    walk_named,
)
from .paths import (
    local_appdata,
    roaming_appdata,
    temp_dir,
    windows_dir,
)


def _clean_if_dir(folder: Optional[Path], ctx: CleanContext) -> None:
    if folder is None:
        return
    path = Path(folder)
    if path_is_dir(path):
        clean_dir_contents(path, ctx)


def _clean_prefetch(ctx: CleanContext) -> None:
    folder = windows_dir() / "Prefetch"
    if not path_is_dir(folder):
        return
    entries = read_entries(folder, ctx.result)
    if entries is None:
        return
    for entry in entries:
        stat_result = entry_stat(entry)
        if stat_result is None or is_reparse(stat_result):
            continue
        if entry_is_dir(entry):
            continue
        if entry.name.casefold().endswith(".pf"):
            remove_file_into(entry.path, stat_result.st_size, ctx, ctx.result)


def _clean_user_temp(ctx: CleanContext) -> None:
    _clean_if_dir(temp_dir(), ctx)


def _clean_windows_temp(ctx: CleanContext) -> None:
    _clean_if_dir(windows_dir() / "Temp", ctx)


def _clean_system_logs(ctx: CleanContext) -> None:
    _clean_if_dir(windows_dir() / "Logs", ctx)


def _clean_software_distribution(ctx: CleanContext) -> None:
    if shell.windows_update_running():
        ctx.result.skipped.append("Windows Update 正在运行，跳过 SoftwareDistribution 清理")
        return
    _clean_if_dir(windows_dir() / "SoftwareDistribution" / "Download", ctx)


def _clean_browser_cache(ctx: CleanContext) -> None:
    local = local_appdata()
    if local is None:
        return
    chrome = local / "Google" / "Chrome" / "User Data" / "Default" / "Cache"
    edge = local / "Microsoft" / "Edge" / "User Data" / "Default" / "Cache"
    _clean_if_dir(chrome, ctx)
    _clean_if_dir(edge, ctx)


def _clean_temp_files_c(ctx: CleanContext) -> None:
    ctx.result.merge(walk_extensions(Path("C:\\"), ("tmp", "cache"), ctx))


def _clean_log_files_c(ctx: CleanContext) -> None:
    ctx.result.merge(walk_extensions(Path("C:\\"), ("log",), ctx))


def _clean_restore_points(ctx: CleanContext) -> None:
    if ctx.dry_run:
        count = shell.shadow_count()
        if count is None:
            ctx.result.actions.append("将删除 C 盘所有系统还原点（无法预估大小）")
        else:
            ctx.result.actions.append(f"将删除 C 盘系统还原点，共 {count} 个（无法预估大小）")
        return
    ok, message = shell.delete_shadows()
    if ok:
        ctx.result.actions.append("已删除 C 盘系统还原点")
        if message:
            ctx.result.actions.append(message)
    else:
        ctx.result.errors.append(f"删除还原点失败: {message}")


def _clean_hibernation(ctx: CleanContext) -> None:
    size = shell.hiberfil_size()
    if ctx.dry_run:
        if size:
            ctx.result.bytes += size
        ctx.result.actions.append("将执行 powercfg /h off")
        return
    ok, message = shell.disable_hibernation()
    if ok:
        if size:
            ctx.result.bytes += size
        ctx.result.actions.append("已关闭休眠并释放 hiberfil.sys")
    else:
        ctx.result.errors.append(f"关闭休眠失败: {message}")


def _clean_delivery_optimization(ctx: CleanContext) -> None:
    _clean_if_dir(
        windows_dir() / "SoftwareDistribution" / "DeliveryOptimization", ctx
    )


def _clean_thumbnails(ctx: CleanContext) -> None:
    local = local_appdata()
    if local is None:
        return
    folder = local / "Microsoft" / "Windows" / "Explorer"
    if not path_is_dir(folder):
        return
    entries = read_entries(folder, ctx.result)
    if entries is None:
        return
    for entry in entries:
        stat_result = entry_stat(entry)
        if stat_result is None or is_reparse(stat_result):
            continue
        if entry_is_dir(entry):
            continue
        lowered = entry.name.casefold()
        if lowered.startswith("thumbcache_") and lowered.endswith(".db"):
            remove_file_into(entry.path, stat_result.st_size, ctx, ctx.result)


def _clean_defender(ctx: CleanContext) -> None:
    _clean_if_dir(
        Path("C:\\ProgramData\\Microsoft\\Windows Defender\\Scans\\History"), ctx
    )


def _clean_inet_cache(ctx: CleanContext) -> None:
    local = local_appdata()
    if local is None:
        return
    _clean_if_dir(local / "Microsoft" / "Windows" / "INetCache", ctx)


def _clean_wer_reports(ctx: CleanContext) -> None:
    _clean_if_dir(Path("C:\\ProgramData\\Microsoft\\Windows\\WER"), ctx)
    local = local_appdata()
    if local is not None:
        _clean_if_dir(local / "Microsoft" / "Windows" / "WER", ctx)


def _clean_shader_cache(ctx: CleanContext) -> None:
    local = local_appdata()
    if local is None:
        return
    _clean_if_dir(local / "Microsoft" / "DirectX Shader Cache", ctx)
    _clean_if_dir(local / "NVIDIA" / "DXCache", ctx)
    _clean_if_dir(local / "AMD" / "DxCache", ctx)
    _clean_if_dir(local / "Intel" / "ShaderCache", ctx)


def _clean_nvidia_debug_logs(ctx: CleanContext) -> None:
    roots = [
        Path("C:\\ProgramData\\NVIDIA Corporation"),
        Path(os.environ.get("ProgramFiles", "C:\\Program Files")) / "NVIDIA Corporation",
    ]
    local = local_appdata()
    if local is not None:
        roots.append(local / "NVIDIA Corporation")
    for root in roots:
        if path_is_dir(root):
            ctx.result.merge(walk_named(root, "debug.log", ctx))


def _collect_backups(folder: Path, ctx: CleanContext) -> None:
    if not path_is_dir(folder):
        return
    entries = read_entries(folder, None)
    if entries is None:
        return
    for entry in entries:
        stat_result = entry_stat(entry)
        if stat_result is None or is_reparse(stat_result):
            continue
        if entry_is_dir(entry):
            if entry.name.casefold() == "backup":
                clean_dir_contents(entry.path, ctx)
            else:
                _collect_backups(Path(entry.path), ctx)
            continue
        lowered = entry.name.casefold()
        if lowered.endswith(".bak") or lowered.endswith(".wbk"):
            remove_file_into(entry.path, stat_result.st_size, ctx, ctx.result)


def _clean_wps_backups(ctx: CleanContext) -> None:
    roaming = roaming_appdata()
    local = local_appdata()
    if roaming is not None:
        _clean_if_dir(roaming / "Kingsoft" / "office6" / "backup", ctx)
    if local is not None:
        _collect_backups(local / "Kingsoft", ctx)
    if roaming is not None:
        _collect_backups(roaming / "Kingsoft", ctx)


def _clean_winapp2(ctx: CleanContext) -> object:
    from . import winapp2 as engine

    result, stats = engine.run(
        dry_run=ctx.dry_run,
        use_registry=ctx.registry_enabled,
        glob_enabled=ctx.glob_enabled,
        verbose=ctx.verbose,
        progress=ctx.progress,
    )
    ctx.result.merge(result)
    return stats


def _clean_msp_files(ctx: CleanContext) -> None:
    ctx.result.merge(walk_extensions(Path("C:\\"), ("msp",), ctx))


def _clean_recycle_bin(ctx: CleanContext) -> None:
    _clean_if_dir(Path("C:\\$Recycle.Bin"), ctx)


_CLEANERS = {
    "prefetch": _clean_prefetch,
    "user_temp": _clean_user_temp,
    "windows_temp": _clean_windows_temp,
    "system_logs": _clean_system_logs,
    "software_distribution": _clean_software_distribution,
    "browser_cache": _clean_browser_cache,
    "temp_files_c": _clean_temp_files_c,
    "log_files_c": _clean_log_files_c,
    "restore_points": _clean_restore_points,
    "hibernation": _clean_hibernation,
    "delivery_optimization": _clean_delivery_optimization,
    "thumbnails": _clean_thumbnails,
    "defender": _clean_defender,
    "inet_cache": _clean_inet_cache,
    "wer_reports": _clean_wer_reports,
    "shader_cache": _clean_shader_cache,
    "nvidia_debug_logs": _clean_nvidia_debug_logs,
    "wps_backups": _clean_wps_backups,
    "winapp2": _clean_winapp2,
    "msp_files": _clean_msp_files,
    "recycle_bin": _clean_recycle_bin,
}


def run_option(
    option_id: str,
    *,
    dry_run: bool,
    exclusions: Iterable[str] = (),
    verbose: bool = False,
    progress=None,
    use_registry: bool = True,
    glob_enabled: bool = True,
) -> OptionOutcome:
    ctx = CleanContext(
        dry_run=dry_run,
        exclusions=tuple(exclusions),
        verbose=verbose,
        progress=progress,
    )
    ctx.registry_enabled = use_registry
    ctx.glob_enabled = glob_enabled
    stats = None
    cleaner = _CLEANERS.get(option_id)
    if cleaner is None:
        ctx.result.errors.append(f"未知清理项: {option_id}")
    else:
        output = cleaner(ctx)
        if option_id == "winapp2":
            stats = output
    return OptionOutcome(id=option_id, result=ctx.result, winapp2=stats)


def run_selection(
    option_ids: Iterable[str],
    *,
    dry_run: bool,
    exclusions: Iterable[str] = (),
    verbose: bool = False,
    progress=None,
    use_registry: bool = True,
    glob_enabled: bool = True,
) -> list:
    ids = list(option_ids)
    outcomes = {}
    if "temp_files_c" in ids and "log_files_c" in ids:
        ctx = CleanContext(
            dry_run=dry_run,
            exclusions=tuple(exclusions),
            verbose=verbose,
            progress=progress,
        )
        groups = {
            "temp_files_c": ("tmp", "cache"),
            "log_files_c": ("log",),
        }
        results = walk_extensions_multi(Path("C:\\"), groups, ctx)
        for option_id, result in results.items():
            outcomes[option_id] = OptionOutcome(id=option_id, result=result)
    ordered = []
    for option_id in ids:
        if option_id in outcomes:
            ordered.append(outcomes[option_id])
            continue
        ordered.append(
            run_option(
                option_id,
                dry_run=dry_run,
                exclusions=exclusions,
                verbose=verbose,
                progress=progress,
                use_registry=use_registry,
                glob_enabled=glob_enabled,
            )
        )
    return ordered
