from __future__ import annotations

import locale
import os
import re
import subprocess
from typing import Optional


CREATE_NO_WINDOW = 0x08000000


def decode(data: bytes) -> str:
    candidates = ["utf-8", locale.getpreferredencoding(False), "mbcs", "gbk"]
    for encoding in candidates:
        if not encoding:
            continue
        try:
            return data.decode(encoding)
        except (UnicodeDecodeError, LookupError):
            continue
    return data.decode("utf-8", errors="replace")


def run(args, timeout: int = 180) -> subprocess.CompletedProcess:
    kwargs = {
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
        "timeout": timeout,
        "check": False,
    }
    if os.name == "nt":
        kwargs["creationflags"] = CREATE_NO_WINDOW
    return subprocess.run(args, **kwargs)


def run_text(args, timeout: int = 180) -> tuple:
    try:
        completed = run(args, timeout=timeout)
    except (OSError, subprocess.SubprocessError) as exc:
        return -1, "", str(exc)
    return completed.returncode, decode(completed.stdout), decode(completed.stderr)


def windows_update_running() -> bool:
    code, stdout, _stderr = run_text(["sc", "query", "wuauserv"])
    if code != 0:
        return False
    if re.search(r"STATE\s*:\s*4\b", stdout, re.IGNORECASE):
        return True
    return "RUNNING" in stdout.upper()


def shadow_count() -> Optional[int]:
    code, stdout, _stderr = run_text(["vssadmin", "list", "shadows"], timeout=120)
    if code != 0:
        return None
    markers = ("shadow copy id", "卷影副本 id", "卷影副本标识")
    count = 0
    for line in stdout.splitlines():
        lowered = line.casefold()
        if any(marker in lowered for marker in markers):
            count += 1
    return count


def delete_shadows() -> tuple:
    code, stdout, stderr = run_text(
        ["vssadmin", "delete", "shadows", "/for=C:", "/all", "/quiet"], timeout=300
    )
    if code == 0:
        return True, stdout.strip() or stderr.strip()
    return False, (stderr.strip() or stdout.strip() or f"vssadmin 退出码 {code}")


def disable_hibernation() -> tuple:
    code, stdout, stderr = run_text(["powercfg", "/h", "off"], timeout=120)
    if code == 0:
        return True, stdout.strip() or stderr.strip()
    return False, (stderr.strip() or stdout.strip() or f"powercfg 退出码 {code}")


def hiberfil_size() -> int:
    try:
        return os.stat("C:\\hiberfil.sys").st_size
    except OSError:
        return 0
