from __future__ import annotations

import glob
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from . import registry
from .fsutil import (
    CleanContext,
    clean_dir_contents,
    clean_dir_files_only,
    clean_matching_files,
    is_link,
    path_exists,
    path_is_dir,
    remove_empty_dir,
)
from .paths import expand_env, is_protected


WINAPP2_PATH = Path(__file__).resolve().parent.parent / "resources" / "Winapp2.ini"


def resolve_ini(path: Optional[Path] = None) -> Path:
    if path is not None:
        return Path(path)
    candidates = []
    override = os.environ.get("WCLEAN_WINAPP2")
    if override:
        candidates.append(Path(override))
    candidates.append(WINAPP2_PATH)
    candidates.append(Path(sys.prefix) / "resources" / "Winapp2.ini")
    for candidate in candidates:
        try:
            if candidate.is_file():
                return candidate
        except OSError:
            continue
    return candidates[0]


@dataclass
class Entry:
    name: str
    detects: list = field(default_factory=list)
    detect_files: list = field(default_factory=list)
    file_keys: list = field(default_factory=list)
    reg_keys: list = field(default_factory=list)


@dataclass
class Winapp2Stats:
    total_entries: int
    matched_entries: int
    registry_enabled: bool
    glob_enabled: bool


@dataclass
class MatchedEntry:
    name: str
    file_keys: int
    reg_keys: int


def parse_ini(content: str) -> list:
    entries = []
    current = None
    for line in content.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith(";"):
            continue
        if stripped.startswith("[") and stripped.endswith("]"):
            if current is not None:
                entries.append(current)
            current = Entry(name=stripped[1:-1].strip())
            continue
        if current is None or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        key = key.strip().casefold()
        value = value.strip()
        if key.startswith("detectfile"):
            current.detect_files.append(value)
        elif key.startswith("detect") and not key.startswith("detectos"):
            current.detects.append(value)
        elif key.startswith("filekey"):
            current.file_keys.append(value)
        elif key.startswith("regkey"):
            if "|" in value:
                reg_key, reg_value = value.split("|", 1)
            else:
                reg_key, reg_value = value, ""
            current.reg_keys.append((reg_key.strip(), reg_value.strip()))
    if current is not None:
        entries.append(current)
    return entries


def load_entries(path: Optional[Path] = None) -> list:
    target = resolve_ini(path)
    with open(target, "r", encoding="utf-8-sig", errors="replace") as handle:
        return parse_ini(handle.read())


def expand_glob(raw: str, enabled: bool) -> list:
    expanded = expand_env(raw)
    if not expanded:
        return []
    if not enabled or not any(char in expanded for char in "*?["):
        return [Path(expanded)]
    try:
        return [Path(match) for match in glob.glob(expanded)]
    except (OSError, ValueError):
        return []


def detect(entry: Entry, glob_enabled: bool, cache: dict) -> bool:
    if not entry.detects and not entry.detect_files:
        return False
    for raw in entry.detects:
        if not registry.key_exists(raw, cache):
            return False
    for raw in entry.detect_files:
        if not any(path_exists(path) for path in expand_glob(raw, glob_enabled)):
            return False
    return True


def exec_file_key(raw: str, ctx: CleanContext, glob_enabled: bool) -> None:
    parts = raw.split("|", 2)
    if len(parts) < 2:
        return
    base_raw = parts[0]
    pattern = parts[1]
    option_text = parts[2].upper() if len(parts) > 2 else ""
    recurse = "RECURSE" in option_text
    remove_self = "REMOVESELF" in option_text

    for base in expand_glob(base_raw, glob_enabled):
        if not path_is_dir(base):
            continue
        if is_link(base):
            ctx.result.skipped.append(f"跳过链接: {base}")
            continue
        if is_protected(base):
            ctx.result.skipped.append(f"受保护路径，跳过: {base}")
            continue
        ctx.log(f"  {base}")
        if pattern == "*":
            if recurse:
                clean_dir_contents(base, ctx)
            else:
                clean_dir_files_only(base, ctx)
        else:
            clean_matching_files(base, pattern, recurse, ctx)
        if remove_self:
            remove_empty_dir(base, ctx)


def exec_reg_key(key: str, value: str, ctx: CleanContext) -> None:
    label = f"{key}|{value}" if value else key
    if ctx.dry_run:
        ctx.result.actions.append(f"将删除注册表: {label}")
        return
    try:
        registry.delete_reg_key(key, value)
    except OSError as exc:
        ctx.result.errors.append(f"注册表删除失败 {label}: {exc}")
        return
    ctx.result.actions.append(f"已删除注册表: {label}")


def run(
    dry_run: bool,
    use_registry: bool = True,
    glob_enabled: bool = True,
    verbose: bool = False,
    progress=None,
    path: Optional[Path] = None,
) -> tuple:
    entries = load_entries(path)
    cache = {}
    ctx = CleanContext(dry_run=dry_run, verbose=verbose, progress=progress)
    matched = 0
    for entry in entries:
        if not detect(entry, glob_enabled, cache):
            continue
        matched += 1
        ctx.log(f"Winapp2: {entry.name}")
        for raw in entry.file_keys:
            exec_file_key(raw, ctx, glob_enabled)
        if use_registry:
            for key, value in entry.reg_keys:
                exec_reg_key(key, value, ctx)
    stats = Winapp2Stats(
        total_entries=len(entries),
        matched_entries=matched,
        registry_enabled=use_registry,
        glob_enabled=glob_enabled,
    )
    return ctx.result, stats


def list_matched(glob_enabled: bool = True, path: Optional[Path] = None) -> list:
    entries = load_entries(path)
    cache = {}
    matched = []
    for entry in entries:
        if detect(entry, glob_enabled, cache):
            matched.append(
                MatchedEntry(
                    name=entry.name,
                    file_keys=len(entry.file_keys),
                    reg_keys=len(entry.reg_keys),
                )
            )
    return matched
