from __future__ import annotations

import ctypes
import os
import re
import shutil
from pathlib import Path
from typing import Iterable, Optional


_ALIASES = {
    "localappdata": "LOCALAPPDATA",
    "appdata": "APPDATA",
    "windir": "SystemRoot",
    "systemroot": "SystemRoot",
    "systemdrive": "SystemDrive",
    "userprofile": "USERPROFILE",
    "programfiles": "ProgramFiles",
    "programfiles(x86)": "ProgramFiles(x86)",
    "commonappdata": "ProgramData",
    "localappdatalow": "LOCALAPPDATA",
    "documents": "USERPROFILE",
    "public": "PUBLIC",
    "allusersprofile": "ProgramData",
    "temp": "TEMP",
    "tmp": "TEMP",
}

_ENV_PATTERN = re.compile(r"%([^%]+)%")


def expand_env(value: str) -> str:
    if not value or "%" not in value:
        return value
    env = {key.casefold(): item for key, item in os.environ.items()}

    def replace(match: re.Match) -> str:
        name = match.group(1)
        key = name.casefold()
        alias = _ALIASES.get(key)
        if alias is not None:
            return env.get(alias.casefold(), "")
        if key in env:
            return env[key]
        return match.group(0)

    return _ENV_PATTERN.sub(replace, value)


def env_path(name: str, default: Optional[str] = None) -> Optional[Path]:
    raw = os.environ.get(name) or default
    return Path(raw) if raw else None


def windows_dir() -> Path:
    return env_path("SystemRoot", "C:\\Windows") or Path("C:\\Windows")


def local_appdata() -> Optional[Path]:
    return env_path("LOCALAPPDATA")


def roaming_appdata() -> Optional[Path]:
    return env_path("APPDATA")


def program_data() -> Optional[Path]:
    return env_path("ProgramData", "C:\\ProgramData")


def temp_dir() -> Optional[Path]:
    return env_path("TEMP") or env_path("TMP")


def user_profile() -> Optional[Path]:
    return env_path("USERPROFILE")


def is_admin() -> bool:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def disk_usage(drive: str = "C:\\"):
    try:
        return shutil.disk_usage(drive)
    except OSError:
        return None


def c_free_bytes() -> Optional[int]:
    usage = disk_usage()
    return usage.free if usage else None


def norm_path(path) -> str:
    try:
        value = os.path.abspath(os.fspath(path))
    except (OSError, TypeError):
        value = os.fspath(path)
    value = value.rstrip("\\/")
    return os.path.normcase(value or os.fspath(path))


def long_path(path) -> str:
    value = os.path.abspath(os.fspath(path))
    if value.startswith("\\\\?\\"):
        return value
    if value.startswith("\\\\"):
        return "\\\\?\\UNC\\" + value[2:]
    return "\\\\?\\" + value


def protected_paths() -> tuple[str, ...]:
    windows = windows_dir()
    candidates = [
        Path("C:\\"),
        windows,
        windows / "System32",
        windows / "SysWOW64",
        windows / "WinSxS",
        windows / "servicing",
        Path("C:\\Program Files"),
        Path("C:\\Program Files (x86)"),
        Path("C:\\ProgramData"),
        Path("C:\\System Volume Information"),
        Path("C:\\Recovery"),
    ]
    profile = user_profile()
    if profile is not None:
        candidates.append(profile)
    return tuple(norm_path(item) for item in candidates)


def is_protected(path) -> bool:
    target = norm_path(path)
    return any(target == item for item in protected_paths())


def is_excluded(path, exclusions: Iterable[str]) -> bool:
    target = norm_path(path)
    for exclusion in exclusions:
        base = norm_path(exclusion)
        if target == base or target.startswith(base + os.sep):
            return True
    return False


def default_exclusions() -> tuple[str, ...]:
    windows = windows_dir()
    candidates = [
        windows / "System32",
        windows / "SysWOW64",
        windows / "WinSxS",
        windows / "servicing",
        Path("C:\\Program Files"),
        Path("C:\\Program Files (x86)"),
        Path("C:\\System Volume Information"),
        Path("C:\\$Recycle.Bin"),
        Path("C:\\Recovery"),
    ]
    return tuple(os.fspath(item) for item in candidates)
