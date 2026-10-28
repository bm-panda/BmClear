from __future__ import annotations

import fnmatch
import os
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable, Mapping, Optional

from .paths import is_excluded, is_protected, long_path


FILE_ATTRIBUTE_REPARSE_POINT = 0x400


@dataclass
class CleanResult:
    bytes: int = 0
    files: int = 0
    dirs: int = 0
    errors: list = field(default_factory=list)
    skipped: list = field(default_factory=list)
    actions: list = field(default_factory=list)

    def merge(self, other: "CleanResult") -> None:
        self.bytes += other.bytes
        self.files += other.files
        self.dirs += other.dirs
        self.errors.extend(other.errors)
        self.skipped.extend(other.skipped)
        self.actions.extend(other.actions)


@dataclass
class OptionOutcome:
    id: str
    result: CleanResult
    winapp2: Optional[object] = None


@dataclass
class CleanContext:
    dry_run: bool
    exclusions: tuple = ()
    verbose: bool = False
    progress: Optional[Callable[[int], None]] = None
    result: CleanResult = field(default_factory=CleanResult)
    registry_enabled: bool = True
    glob_enabled: bool = True

    def log(self, message: str) -> None:
        if self.verbose:
            print(message, file=sys.stderr)


def entry_stat(entry):
    try:
        return entry.stat(follow_symlinks=False)
    except OSError:
        return None


def entry_is_dir(entry) -> bool:
    try:
        return entry.is_dir(follow_symlinks=False)
    except OSError:
        return False


def is_reparse(stat_result) -> bool:
    return bool(getattr(stat_result, "st_file_attributes", 0) & FILE_ATTRIBUTE_REPARSE_POINT)


def path_is_dir(path) -> bool:
    try:
        return os.path.isdir(os.fspath(path))
    except (OSError, TypeError):
        return False


def path_exists(path) -> bool:
    try:
        return os.path.exists(os.fspath(path))
    except (OSError, TypeError):
        return False


def is_link(path) -> bool:
    try:
        stat_result = os.lstat(os.fspath(path))
    except OSError:
        return False
    if getattr(stat_result, "st_file_attributes", 0) & FILE_ATTRIBUTE_REPARSE_POINT:
        return True
    return os.path.islink(os.fspath(path))


def read_entries(folder, result: Optional[CleanResult]):
    try:
        return list(os.scandir(os.fspath(folder)))
    except OSError as exc:
        if result is not None:
            result.errors.append(f"无法读取 {folder}: {exc}")
        return None


def dir_size(path) -> tuple:
    total = 0
    files = 0
    stack = [os.fspath(path)]
    while stack:
        current = stack.pop()
        entries = read_entries(current, None)
        if entries is None:
            continue
        for entry in entries:
            stat_result = entry_stat(entry)
            if stat_result is None or is_reparse(stat_result):
                continue
            if entry_is_dir(entry):
                stack.append(entry.path)
            else:
                total += stat_result.st_size
                files += 1
    return total, files


def remove_file_into(path, size: int, ctx: CleanContext, result: CleanResult) -> bool:
    if ctx.dry_run:
        result.bytes += size
        result.files += 1
        return True
    text = os.fspath(path)
    target = long_path(text) if len(text) >= 248 else text
    try:
        os.remove(target)
    except OSError as exc:
        result.errors.append(f"跳过 {path}: {exc}")
        return False
    result.bytes += size
    result.files += 1
    return True


def remove_dir_into(path, ctx: CleanContext, result: CleanResult) -> bool:
    size, files = dir_size(path)
    if ctx.dry_run:
        result.bytes += size
        result.files += files
        result.dirs += 1
        return True
    failures = []

    def on_error(_func, failed_path, exc_info):
        failures.append(f"{failed_path}: {exc_info[1]}")

    text = os.fspath(path)
    target = long_path(text) if len(text) >= 248 else text
    try:
        shutil.rmtree(target, onerror=on_error)
    except OSError as exc:
        result.errors.append(f"无法删除 {path}: {exc}")
        return False
    result.dirs += 1
    if failures:
        result.errors.extend(failures)
        return False
    result.bytes += size
    result.files += files
    return True


def clean_dir_contents(folder, ctx: CleanContext) -> None:
    folder_path = Path(folder)
    if is_protected(folder_path):
        ctx.result.skipped.append(f"受保护目录，跳过: {folder_path}")
        return
    entries = read_entries(folder_path, ctx.result)
    if entries is None:
        return
    for entry in entries:
        stat_result = entry_stat(entry)
        if stat_result is None:
            ctx.result.errors.append(f"无法读取 {entry.path}")
            continue
        if is_reparse(stat_result):
            ctx.result.skipped.append(f"跳过链接: {entry.path}")
            continue
        if entry_is_dir(entry):
            remove_dir_into(entry.path, ctx, ctx.result)
        else:
            remove_file_into(entry.path, stat_result.st_size, ctx, ctx.result)


def clean_dir_files_only(folder, ctx: CleanContext) -> None:
    folder_path = Path(folder)
    if is_protected(folder_path):
        ctx.result.skipped.append(f"受保护目录，跳过: {folder_path}")
        return
    entries = read_entries(folder_path, ctx.result)
    if entries is None:
        return
    for entry in entries:
        stat_result = entry_stat(entry)
        if stat_result is None or is_reparse(stat_result):
            continue
        if not entry_is_dir(entry):
            remove_file_into(entry.path, stat_result.st_size, ctx, ctx.result)


def clean_matching_files(folder, pattern: str, recurse: bool, ctx: CleanContext) -> None:
    patterns = [part.strip() for part in pattern.split(";") if part.strip()]
    if not patterns:
        return
    stack = [os.fspath(folder)]
    visited = 0
    while stack:
        current = stack.pop()
        entries = read_entries(current, None)
        if entries is None:
            continue
        for entry in entries:
            stat_result = entry_stat(entry)
            if stat_result is None or is_reparse(stat_result):
                continue
            if entry_is_dir(entry):
                if recurse and not is_excluded(entry.path, ctx.exclusions):
                    stack.append(entry.path)
                continue
            if any(fnmatch.fnmatch(entry.name, part) for part in patterns):
                remove_file_into(entry.path, stat_result.st_size, ctx, ctx.result)
            visited += 1
            if ctx.progress and visited % 2000 == 0:
                ctx.progress(visited)


def walk_extensions(root, extensions: Iterable[str], ctx: CleanContext) -> CleanResult:
    groups = {"default": set(extensions)}
    return walk_extensions_multi(root, groups, ctx)["default"]


def walk_extensions_multi(root, groups: Mapping[str, Iterable[str]], ctx: CleanContext) -> dict:
    results = {name: CleanResult() for name in groups}
    extension_map = {}
    for name, extensions in groups.items():
        for extension in extensions:
            extension_map[extension.casefold().lstrip(".")] = name
    if not extension_map:
        return results

    stack = [os.fspath(root)]
    visited = 0
    while stack:
        current = stack.pop()
        entries = read_entries(current, None)
        if entries is None:
            continue
        for entry in entries:
            stat_result = entry_stat(entry)
            if stat_result is None or is_reparse(stat_result):
                continue
            if entry_is_dir(entry):
                if not is_excluded(entry.path, ctx.exclusions):
                    stack.append(entry.path)
                continue
            name = entry.name
            if "." not in name:
                continue
            extension = name.rsplit(".", 1)[1].casefold()
            group = extension_map.get(extension)
            if group is not None:
                remove_file_into(entry.path, stat_result.st_size, ctx, results[group])
            visited += 1
            if ctx.progress and visited % 2000 == 0:
                ctx.progress(visited)
    return results


def walk_named(root, target_name: str, ctx: CleanContext) -> CleanResult:
    result = CleanResult()
    target = target_name.casefold()
    stack = [os.fspath(root)]
    visited = 0
    while stack:
        current = stack.pop()
        entries = read_entries(current, None)
        if entries is None:
            continue
        for entry in entries:
            stat_result = entry_stat(entry)
            if stat_result is None or is_reparse(stat_result):
                continue
            if entry_is_dir(entry):
                stack.append(entry.path)
                continue
            if entry.name.casefold() == target:
                remove_file_into(entry.path, stat_result.st_size, ctx, result)
            visited += 1
            if ctx.progress and visited % 2000 == 0:
                ctx.progress(visited)
    return result


def remove_empty_dir(path, ctx: CleanContext) -> None:
    if not os.path.isdir(os.fspath(path)):
        return
    try:
        if os.listdir(os.fspath(path)):
            return
    except OSError:
        return
    if ctx.dry_run:
        ctx.result.actions.append(f"将删除空目录: {path}")
        return
    try:
        os.rmdir(long_path(path) if len(os.fspath(path)) >= 248 else os.fspath(path))
        ctx.result.actions.append(f"已删除空目录: {path}")
    except OSError:
        pass


def remove_file(path, ctx: CleanContext, result: CleanResult) -> bool:
    try:
        stat_result = os.stat(os.fspath(path), follow_symlinks=False)
    except OSError:
        return False
    return remove_file_into(path, stat_result.st_size, ctx, result)
