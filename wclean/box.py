from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

from . import elevate
from .cleaners import run_selection
from .options import (
    ALL_OPTION_IDS,
    OPTION_MAP,
    QUICK_SAFE_OPTION_IDS,
    resolve_selection,
)
from .paths import c_free_bytes, default_exclusions, is_admin
from .report import build_report, render_report


@dataclass
class RunOutcome:
    code: int
    report: Optional[dict] = None
    report_path: Optional[Path] = None
    message: str = "ok"


def normalize_bool(value, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "y", "on", "是")
    return bool(value)


CUSTOM_OPTION_PARAMS = {
    f"opt_{option_id}": option_id for option_id in ALL_OPTION_IDS
}


def build_selection(params: dict):
    preset = str(params.get("preset", "safe")).strip().lower()
    allow_risky = normalize_bool(params.get("allow_risky"), False)
    if preset == "safe":
        return resolve_selection(include_risk=allow_risky)
    if preset == "quick":
        return resolve_selection(quick=True, include_risk=allow_risky)
    if preset == "all":
        return resolve_selection(all_options=True, include_risk=allow_risky)
    if preset == "custom":
        ids = [
            option_id
            for param_name, option_id in CUSTOM_OPTION_PARAMS.items()
            if normalize_bool(
                params.get(param_name), option_id in QUICK_SAFE_OPTION_IDS
            )
        ]
        if not ids:
            raise ValueError("请至少勾选一个清理项")
        return resolve_selection(explicit=ids, include_risk=True)
    raise ValueError(f"未知清理范围: {preset}")


def action_mode(params: dict) -> str:
    action = str(params.get("action", "scan")).strip().lower()
    return "clean" if action == "clean" else "scan"


def option_names(ids) -> str:
    return ", ".join(OPTION_MAP[item].name for item in ids)


def print_selection(mode: str, selection) -> None:
    print("=" * 64)
    print(f"C盘清理 · {'预览扫描' if mode == 'scan' else '执行清理'}")
    print(f"清理项 ({len(selection.ids)}): {option_names(selection.ids)}")
    if selection.gated:
        print(f"已跳过高风险项: {option_names(selection.gated)}")
    risky = [item for item in selection.ids if OPTION_MAP[item].risky]
    if mode == "clean" and risky:
        print(f"高风险项已启用: {option_names(risky)}")
    print("=" * 64)


def make_progress():
    state = {"last": 0.0}

    def callback(count: int) -> None:
        now = time.monotonic()
        if now - state["last"] < 1.0:
            return
        state["last"] = now
        try:
            print(
                f"\r已扫描 {count} 个文件...",
                end="",
                file=sys.stderr,
                flush=True,
            )
        except (OSError, ValueError):
            pass

    return callback


def save_report(report: dict, environment: dict) -> Optional[Path]:
    base = environment.get("script_dir") or str(Path(__file__).resolve().parent.parent)
    directory = Path(base) / "reports"
    try:
        directory.mkdir(parents=True, exist_ok=True)
    except OSError:
        directory = Path(environment.get("workspace") or tempfile.gettempdir())
        try:
            directory.mkdir(parents=True, exist_ok=True)
        except OSError:
            return None
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")[:-3]
    target = directory / f"wclean-{stamp}.json"
    try:
        target.write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except OSError as exc:
        print(f"报告保存失败: {exc}", file=sys.stderr)
        return None
    return target


def write_envelope(environment: dict, code: int, message: str, extra=None) -> None:
    target = environment.get("output_json")
    if not target:
        return
    data = {"code": int(code), "msg": str(message)}
    if extra:
        data.update(extra)
    write_json_atomic(Path(target), data)


def write_json_atomic(path: Path, data: dict) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        os.replace(temporary, path)
    except OSError as exc:
        print(f"结果写入失败: {exc}", file=sys.stderr)


def read_result(path: Path) -> Optional[dict]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def write_child_result(path: Path, outcome: RunOutcome) -> None:
    data = {"code": int(outcome.code), "msg": outcome.message}
    if outcome.report is not None:
        total = outcome.report.get("total", {})
        data["freed_bytes"] = str(total.get("matchBytes", 0))
        data["files"] = int(total.get("files", 0))
        data["errors"] = int(total.get("errors", 0))
    if outcome.report_path is not None:
        data["report_path"] = str(outcome.report_path)
    write_json_atomic(path, data)


def execute(payload: dict, selection, write_envelope_flag: bool) -> RunOutcome:
    environment = payload.get("environment") or {}
    params = payload.get("params") or {}
    mode = action_mode(params)

    print_selection(mode, selection)
    if not is_admin():
        admin_ids = [item for item in selection.ids if OPTION_MAP[item].admin]
        if admin_ids:
            print(f"警告: 未获得管理员权限，以下项可能失败: {option_names(admin_ids)}")
            print("请允许 UAC 提权后重新运行。")

    before = c_free_bytes()
    progress = make_progress()
    start = time.monotonic()
    try:
        outcomes = run_selection(
            selection.ids,
            dry_run=mode == "scan",
            exclusions=default_exclusions(),
            verbose=False,
            progress=progress,
            use_registry=True,
            glob_enabled=True,
        )
    except KeyboardInterrupt:
        print("", file=sys.stderr)
        print("已中断，未继续执行。")
        if write_envelope_flag and environment.get("invoke_mode") == "node":
            write_envelope(environment, 130, "已中断")
        return RunOutcome(130, message="已中断")
    except Exception as exc:
        print("", file=sys.stderr)
        print(f"清理失败: {exc}")
        if write_envelope_flag and environment.get("invoke_mode") == "node":
            write_envelope(environment, -1, f"清理失败: {exc}")
        return RunOutcome(1, message=f"清理失败: {exc}")

    print("", file=sys.stderr)
    after = c_free_bytes() if mode == "clean" else None
    elapsed = time.monotonic() - start
    report = build_report(mode, outcomes, before, after, elapsed)
    print()
    print(render_report(report))
    report_path = save_report(report, environment)
    if report_path is not None:
        print(f"报告已保存: {report_path}")
    total = report.get("total", {})
    code = 0 if int(total.get("errors", 0)) == 0 else 1
    message = "ok" if code == 0 else "部分清理项返回错误"
    outcome = RunOutcome(code, report, report_path, message)
    if write_envelope_flag and environment.get("invoke_mode") == "node":
        extra = {
            "freed_bytes": str(total.get("matchBytes", 0)),
            "files": int(total.get("files", 0)),
            "errors": int(total.get("errors", 0)),
        }
        if report_path is not None:
            extra["report_path"] = str(report_path)
        write_envelope(environment, code, message, extra)
    return outcome


def run_elevated_child(payload: dict) -> int:
    environment = payload.get("environment") or {}
    params = payload.get("params") or {}
    log_path = environment.get("child_log")
    result_path = environment.get("child_result")
    old_stdout, old_stderr = sys.stdout, sys.stderr
    stream = None
    if log_path:
        try:
            Path(log_path).parent.mkdir(parents=True, exist_ok=True)
            stream = open(log_path, "w", encoding="utf-8", buffering=1)
            sys.stdout = stream
            sys.stderr = stream
        except OSError:
            stream = None

    if not is_admin():
        print("警告: 提权子进程未获得管理员权限，部分操作可能失败。")

    outcome = RunOutcome(1, message="清理失败")
    try:
        try:
            selection = build_selection(params)
        except ValueError as exc:
            print(f"参数错误: {exc}")
            outcome = RunOutcome(2, message=f"参数错误: {exc}")
        else:
            outcome = execute(payload, selection, write_envelope_flag=False)
    except Exception as exc:
        print(f"清理失败: {exc}")
        outcome = RunOutcome(1, message=f"清理失败: {exc}")
    finally:
        if result_path:
            write_child_result(Path(result_path), outcome)
        if stream is not None:
            try:
                stream.flush()
                stream.close()
            except OSError:
                pass
        sys.stdout = old_stdout
        sys.stderr = old_stderr
    return outcome.code


def run_parent_elevated(payload: dict, selection) -> int:
    environment = payload.get("environment") or {}
    params = payload.get("params") or {}
    mode = action_mode(params)
    print_selection(mode, selection)
    print("需要管理员权限，正在请求 UAC 授权...")

    base = Path(tempfile.mkdtemp(prefix="wclean-elev-"))
    job_path = base / "job.json"
    log_path = base / "child.log"
    result_path = base / "child-result.json"
    script_path = Path(__file__).resolve().parent.parent / "box_main.py"
    if not script_path.is_file():
        script_path = Path(sys.argv[0]).resolve()
    python_exe = elevate.elevation_python()
    job = elevate.build_job_payload(payload, log_path, result_path)
    elevate.write_job(job_path, job)

    remove_temp = True
    try:
        try:
            exit_code = elevate.elevate_and_wait(
                python_exe,
                script_path,
                job_path,
                log_path,
                on_log=lambda text: print(text, end="", flush=True),
            )
        except elevate.ElevationCancelled:
            print("已取消管理员授权，未执行任何操作。")
            write_envelope(environment, -1, "已取消管理员授权")
            return 2
        except OSError as exc:
            print(f"提权失败: {exc}")
            write_envelope(environment, -1, f"提权失败: {exc}")
            remove_temp = False
            print(f"临时文件保留在: {base}")
            return 2
        print()
        result = read_result(result_path)
        if result is None:
            print("提权子进程未返回结果。")
            print(f"临时文件保留在: {base}")
            remove_temp = False
            write_envelope(environment, -1, "提权子进程未返回结果")
            return 1
        code = int(result.get("code", exit_code if exit_code is not None else 1))
        message = str(result.get("msg", "ok"))
        if environment.get("invoke_mode") == "node":
            extra = {}
            for key in ("freed_bytes", "files", "errors", "report_path"):
                if key in result:
                    extra[key] = result[key]
            write_envelope(environment, code, message, extra)
        return code
    finally:
        if remove_temp:
            shutil.rmtree(base, ignore_errors=True)


def maybe_pause(environment: dict) -> None:
    if environment.get("invoke_mode") != "manual":
        return
    try:
        input("按回车键退出...")
    except (EOFError, OSError, KeyboardInterrupt):
        pass


def run_box(payload: dict) -> int:
    environment = payload.get("environment") or {}
    params = payload.get("params") or {}
    if environment.get("elevated_child"):
        return run_elevated_child(payload)
    try:
        selection = build_selection(params)
    except ValueError as exc:
        print(f"参数错误: {exc}", file=sys.stderr)
        maybe_pause(environment)
        return 2
    try:
        if not is_admin():
            return run_parent_elevated(payload, selection)
        outcome = execute(payload, selection, write_envelope_flag=True)
        return outcome.code
    except KeyboardInterrupt:
        print("已中断。")
        write_envelope(environment, 130, "已中断")
        return 130
    except Exception as exc:
        print(f"清理失败: {exc}", file=sys.stderr)
        write_envelope(environment, -1, f"清理失败: {exc}")
        return 1
    finally:
        maybe_pause(environment)
