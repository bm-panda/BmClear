from __future__ import annotations

import copy
import ctypes
import json
import os
import sys
import time
from ctypes import wintypes
from pathlib import Path
from typing import Callable, Optional


SEE_MASK_NOCLOSEPROCESS = 0x00000040
SEE_MASK_NOASYNC = 0x00000100
SW_HIDE = 0
WAIT_TIMEOUT = 258
WAIT_FAILED = 0xFFFFFFFF
ERROR_CANCELLED = 1223


class ElevationCancelled(Exception):
    pass


class SHELLEXECUTEINFOW(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("fMask", ctypes.c_ulong),
        ("hwnd", wintypes.HWND),
        ("lpVerb", wintypes.LPCWSTR),
        ("lpFile", wintypes.LPCWSTR),
        ("lpParameters", wintypes.LPCWSTR),
        ("lpDirectory", wintypes.LPCWSTR),
        ("nShow", ctypes.c_int),
        ("hInstApp", wintypes.HINSTANCE),
        ("lpIDList", ctypes.c_void_p),
        ("lpClass", wintypes.LPCWSTR),
        ("hkeyClass", wintypes.HKEY),
        ("dwHotKey", wintypes.DWORD),
        ("hIcon", wintypes.HANDLE),
        ("hProcess", wintypes.HANDLE),
    ]


_shell32 = ctypes.WinDLL("shell32", use_last_error=True)
_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

_shell32.ShellExecuteExW.argtypes = [ctypes.POINTER(SHELLEXECUTEINFOW)]
_shell32.ShellExecuteExW.restype = wintypes.BOOL
_kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
_kernel32.WaitForSingleObject.restype = wintypes.DWORD
_kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
_kernel32.GetExitCodeProcess.restype = wintypes.BOOL
_kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
_kernel32.CloseHandle.restype = wintypes.BOOL


def elevation_python() -> Path:
    executable = Path(sys.executable)
    for name in ("pythonw.exe", "BmScriptsBoxw.exe"):
        candidate = executable.with_name(name)
        try:
            if candidate.is_file():
                return candidate
        except OSError:
            continue
    return executable


def build_parameters(script_path, job_path) -> str:
    return f'"{script_path}" "{job_path}"'


def tail_file(path, offset: int, sink: Callable[[str], None]) -> int:
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            handle.seek(offset)
            chunk = handle.read()
            position = handle.tell()
    except OSError:
        return offset
    if chunk:
        try:
            sink(chunk)
        except (OSError, ValueError):
            pass
    return position


def elevate_and_wait(
    python_exe,
    script_path,
    job_path,
    log_path,
    on_log: Optional[Callable[[str], None]] = None,
    poll_interval: float = 0.5,
    verb: str = "runas",
    window: int = SW_HIDE,
) -> int:
    log_target = Path(log_path)
    try:
        log_target.parent.mkdir(parents=True, exist_ok=True)
        log_target.touch(exist_ok=True)
    except OSError:
        pass

    info = SHELLEXECUTEINFOW()
    info.cbSize = ctypes.sizeof(SHELLEXECUTEINFOW)
    info.fMask = SEE_MASK_NOCLOSEPROCESS | SEE_MASK_NOASYNC
    info.lpVerb = verb
    info.lpFile = str(python_exe)
    info.lpParameters = build_parameters(script_path, job_path)
    info.lpDirectory = str(Path(script_path).resolve().parent)
    info.nShow = window
    ctypes.set_last_error(0)
    if not _shell32.ShellExecuteExW(ctypes.byref(info)):
        error = ctypes.get_last_error()
        if error == ERROR_CANCELLED:
            raise ElevationCancelled("用户取消了管理员授权")
        raise OSError(error, "UAC 提权失败")
    handle = info.hProcess
    if not handle:
        raise OSError("无法获取提权进程句柄")

    sink = on_log or (lambda text: sys.stdout.write(text))
    offset = 0
    try:
        while True:
            result = _kernel32.WaitForSingleObject(handle, int(poll_interval * 1000))
            offset = tail_file(log_target, offset, sink)
            if result == WAIT_FAILED:
                error = ctypes.get_last_error()
                raise OSError(error, "等待提权进程失败")
            if result != WAIT_TIMEOUT:
                break
        tail_file(log_target, offset, sink)
        code = wintypes.DWORD()
        if not _kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
            error = ctypes.get_last_error()
            raise OSError(error, "无法读取提权进程退出码")
        return int(code.value)
    finally:
        _kernel32.CloseHandle(handle)


def build_job_payload(payload, log_path, result_path) -> dict:
    job = copy.deepcopy(payload)
    environment = job.get("environment")
    if not isinstance(environment, dict):
        environment = {}
    environment["elevated_child"] = True
    environment["child_log"] = str(log_path)
    environment["child_result"] = str(result_path)
    job["environment"] = environment
    return job


def write_job(job_path, job) -> None:
    target = Path(job_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(target.suffix + ".tmp")
    temporary.write_text(
        json.dumps(job, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    os.replace(temporary, target)
