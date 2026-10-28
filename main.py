from __future__ import annotations

import json
import os
import sys
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from wclean.box import run_box


def ensure_streams() -> None:
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w", encoding="utf-8")
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w", encoding="utf-8")
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except Exception:
            pass


def main(argv=None) -> int:
    arguments = sys.argv if argv is None else argv
    ensure_streams()
    if len(arguments) < 2:
        print("缺少盒子参数文件路径", file=sys.stderr)
        return 2
    try:
        with open(arguments[1], "r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        print(f"读取参数文件失败: {exc}", file=sys.stderr)
        return 2
    return run_box(payload)


if __name__ == "__main__":
    sys.exit(main())
