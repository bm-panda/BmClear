from __future__ import annotations

import os
import subprocess
from typing import Optional

import winreg

from .shell import CREATE_NO_WINDOW


_HIVES = {
    "HKCU": winreg.HKEY_CURRENT_USER,
    "HKEY_CURRENT_USER": winreg.HKEY_CURRENT_USER,
    "HKLM": winreg.HKEY_LOCAL_MACHINE,
    "HKEY_LOCAL_MACHINE": winreg.HKEY_LOCAL_MACHINE,
    "HKU": winreg.HKEY_USERS,
    "HKEY_USERS": winreg.HKEY_USERS,
    "HKCR": winreg.HKEY_CLASSES_ROOT,
    "HKEY_CLASSES_ROOT": winreg.HKEY_CLASSES_ROOT,
}

_SHORT = {
    "HKEY_CURRENT_USER": "HKCU",
    "HKEY_LOCAL_MACHINE": "HKLM",
    "HKEY_USERS": "HKU",
    "HKEY_CLASSES_ROOT": "HKCR",
}

_SKIPPED_HIVES = {"HKU", "HKEY_USERS"}


def split_key(raw: str) -> Optional[tuple]:
    text = raw.strip()
    if not text:
        return None
    if "\\" in text:
        head, subkey = text.split("\\", 1)
    else:
        head, subkey = text, ""
    head = head.strip().upper()
    if head not in _HIVES:
        return None
    return head, subkey


def key_exists(raw: str, cache: dict) -> bool:
    parsed = split_key(raw)
    if parsed is None:
        return False
    hive, subkey = parsed
    if hive in _SKIPPED_HIVES:
        return False
    cache_key = (hive, subkey.casefold())
    if cache_key in cache:
        return cache[cache_key]
    try:
        with winreg.OpenKey(
            _HIVES[hive], subkey, 0, winreg.KEY_READ | winreg.KEY_WOW64_64KEY
        ):
            exists = True
    except OSError:
        exists = False
    cache[cache_key] = exists
    return exists


def delete_value(raw: str, value: str) -> None:
    parsed = split_key(raw)
    if parsed is None:
        raise OSError(f"无法解析注册表路径: {raw}")
    hive, subkey = parsed
    if hive in _SKIPPED_HIVES:
        raise OSError(f"暂不支持该根键: {raw}")
    with winreg.OpenKey(
        _HIVES[hive],
        subkey,
        0,
        winreg.KEY_SET_VALUE | winreg.KEY_WOW64_64KEY,
    ) as key:
        winreg.DeleteValue(key, value)


def _delete_tree(hive: int, subkey: str) -> None:
    if not subkey:
        raise OSError("拒绝删除根键")
    children = []
    try:
        with winreg.OpenKey(
            hive, subkey, 0, winreg.KEY_READ | winreg.KEY_WOW64_64KEY
        ) as key:
            index = 0
            while True:
                try:
                    children.append(winreg.EnumKey(key, index))
                except OSError:
                    break
                index += 1
    except OSError:
        children = []
    for child in children:
        _delete_tree(hive, subkey + "\\" + child)
    winreg.DeleteKeyEx(hive, subkey, access=winreg.KEY_WOW64_64KEY)


def _reg_delete(raw: str) -> None:
    parsed = split_key(raw)
    if parsed is None:
        raise OSError(f"无法解析注册表路径: {raw}")
    hive, subkey = parsed
    if hive in _SKIPPED_HIVES:
        raise OSError(f"暂不支持该根键: {raw}")
    full = _SHORT.get(hive, hive) + "\\" + subkey
    kwargs = {"check": False, "stdout": subprocess.PIPE, "stderr": subprocess.PIPE}
    if os.name == "nt":
        kwargs["creationflags"] = CREATE_NO_WINDOW
    completed = subprocess.run(["reg", "delete", full, "/f", "/reg:64"], **kwargs)
    if completed.returncode != 0:
        message = completed.stderr.decode("mbcs", errors="replace").strip()
        raise OSError(message or f"reg delete 退出码 {completed.returncode}")


def delete_key_tree(raw: str) -> None:
    parsed = split_key(raw)
    if parsed is None:
        raise OSError(f"无法解析注册表路径: {raw}")
    hive, subkey = parsed
    if hive in _SKIPPED_HIVES:
        raise OSError(f"暂不支持该根键: {raw}")
    try:
        _delete_tree(_HIVES[hive], subkey)
    except OSError:
        _reg_delete(raw)


def delete_reg_key(raw: str, value: str = "") -> None:
    if value:
        delete_value(raw, value)
    else:
        delete_key_tree(raw)
