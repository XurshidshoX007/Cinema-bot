from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent
WEBAPP_PORT = int(os.environ.get("WEBAPP_PORT", "8080"))
PID_FILES = (
    BASE_DIR / ".bot-instance.pid",
    BASE_DIR / ".webapp.pid",
    BASE_DIR / ".stats_tunnel.pid",
)
CLEANUP_FILES = (
    BASE_DIR / ".bot-instance.pid",
    BASE_DIR / ".webapp.pid",
    BASE_DIR / ".stats_tunnel.pid",
    BASE_DIR / ".stats_webapp_url",
)
TARGET_SCRIPTS = (
    str(BASE_DIR / "main.py").lower(),
    str(BASE_DIR / "webapp.py").lower(),
    str(BASE_DIR / "stats_tunnel.py").lower(),
)
CLOUDFLARED_MARKER = f"http://127.0.0.1:{WEBAPP_PORT}".lower()


def _powershell_executable() -> str:
    return os.path.join(
        os.environ.get("SystemRoot", r"C:\Windows"),
        "System32",
        "WindowsPowerShell",
        "v1.0",
        "powershell.exe",
    )


def _iter_process_rows() -> list[tuple[int, str, str]]:
    command = (
        "Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | "
        "Select-Object ProcessId,Name,CommandLine | "
        "ForEach-Object { "
        "if ($_.CommandLine) { "
        "$cmd = ($_.CommandLine -replace '[\\r\\n]+', ' '); "
        "Write-Output (('{0}`t{1}`t{2}' -f $_.ProcessId, $_.Name, $cmd)) "
        "} "
        "}"
    )
    result = subprocess.run(
        [_powershell_executable(), "-NoProfile", "-Command", command],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="ignore",
        check=False,
    )
    if result.returncode != 0:
        return []

    rows: list[tuple[int, str, str]] = []
    for raw_line in result.stdout.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        parts = line.split("\t", 2)
        if len(parts) != 3:
            continue
        pid_text, name, command_line = parts
        if not pid_text.isdigit():
            continue
        rows.append((int(pid_text), name.strip(), command_line.strip()))
    return rows


def _is_target_process(pid: int, name: str, command_line: str) -> bool:
    if pid <= 0 or pid == os.getpid():
        return False

    lowered_name = (name or "").strip().casefold()
    lowered_command = (command_line or "").strip().casefold()
    if not lowered_command:
        return False

    if lowered_name in {"python.exe", "pythonw.exe"}:
        return any(target in lowered_command for target in TARGET_SCRIPTS)

    if lowered_name == "cloudflared.exe":
        return CLOUDFLARED_MARKER in lowered_command

    return False


def _collect_target_pids() -> set[int]:
    pids: set[int] = set()

    for pid_file in PID_FILES:
        try:
            pid_value = int(pid_file.read_text(encoding="utf-8").strip())
        except Exception:
            continue
        if pid_value > 0:
            pids.add(pid_value)

    for pid, name, command_line in _iter_process_rows():
        if _is_target_process(pid, name, command_line):
            pids.add(pid)

    return pids


def _terminate_pid(pid: int) -> None:
    if pid <= 0 or pid == os.getpid():
        return

    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(pid), "/T", "/F"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        return

    try:
        os.kill(pid, 15)
    except OSError:
        return


def _cleanup_runtime_files() -> None:
    for path in CLEANUP_FILES:
        try:
            path.unlink(missing_ok=True)
        except Exception:
            continue


def stop_all() -> int:
    attempted = sorted(_collect_target_pids())
    for pid in attempted:
        _terminate_pid(pid)

    deadline = time.time() + 8
    remaining = attempted
    while time.time() < deadline:
        active = sorted(_collect_target_pids())
        if not active:
            _cleanup_runtime_files()
            for pid in attempted:
                print(pid)
            return 0
        remaining = active
        for pid in active:
            _terminate_pid(pid)
        time.sleep(0.4)

    _cleanup_runtime_files()
    for pid in attempted:
        print(pid)
    if remaining:
        print(
            "Quyidagi processlar to'liq to'xtamadi: "
            + ", ".join(str(pid) for pid in remaining),
            file=sys.stderr,
        )
        return 1
    return 0


def show_status() -> int:
    rows = [
        (pid, name, command_line)
        for pid, name, command_line in _iter_process_rows()
        if _is_target_process(pid, name, command_line)
    ]
    if not rows:
        print("Runtime processlar topilmadi.")
        return 0

    for pid, name, command_line in rows:
        print(f"{pid}\t{name}\t{command_line}")
    return 0


def main() -> int:
    action = (sys.argv[1] if len(sys.argv) > 1 else "status").strip().casefold()
    if action == "stop":
        return stop_all()
    if action == "status":
        return show_status()

    print("Usage: runtime_manager.py [stop|status]", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
