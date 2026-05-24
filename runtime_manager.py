from __future__ import annotations

import ctypes
import os
import re
import subprocess
import sys
import time
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent
WEBAPP_PORT = int(os.environ.get("WEBAPP_PORT", "8080"))
PID_FILE_BY_KIND = {
    "bot": BASE_DIR / ".bot-instance.pid",
    "webapp": BASE_DIR / ".webapp.pid",
    "tunnel": BASE_DIR / ".stats_tunnel.pid",
}
CLEANUP_FILES = tuple(PID_FILE_BY_KIND.values()) + (BASE_DIR / ".stats_webapp_url",)
SCRIPT_MARKERS = {
    "bot": ("main.py",),
    "webapp": ("webapp.py",),
    "tunnel": ("stats_tunnel.py",),
}
CLOUDFLARED_MARKER = f"http://127.0.0.1:{WEBAPP_PORT}".casefold()
LISTENER_PORT_RE = re.compile(r":(\d+)$")


def _powershell_executable() -> str:
    return os.path.join(
        os.environ.get("SystemRoot", r"C:\Windows"),
        "System32",
        "WindowsPowerShell",
        "v1.0",
        "powershell.exe",
    )


def _read_pid_file(path: Path) -> int | None:
    try:
        pid_value = int(path.read_text(encoding="utf-8").strip())
    except Exception:
        return None
    return pid_value if pid_value > 0 else None


def _pid_is_running(pid: int) -> bool:
    if pid <= 0 or pid == os.getpid():
        return False

    if os.name == "nt":
        process_handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)
        if process_handle == 0:
            return False
        ctypes.windll.kernel32.CloseHandle(process_handle)
        return True

    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


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


def _iter_cloudflared_pids() -> set[int]:
    command = (
        "Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | "
        "Where-Object { $_.Name -eq 'cloudflared.exe' -and $_.CommandLine } | "
        "Select-Object ProcessId,CommandLine | "
        "ForEach-Object { "
        "$cmd = ($_.CommandLine -replace '[\\r\\n]+', ' '); "
        "Write-Output (('{0}`t{1}' -f $_.ProcessId, $cmd)) "
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
        return set()

    pids: set[int] = set()
    for raw_line in result.stdout.splitlines():
        parts = raw_line.strip().split("\t", 1)
        if len(parts) != 2:
            continue
        pid_text, command_line = parts
        if pid_text.isdigit() and CLOUDFLARED_MARKER in command_line.casefold():
            pids.add(int(pid_text))
    return pids


def _identify_runtime_kind(name: str, command_line: str) -> str | None:
    lowered_name = (name or "").strip().casefold()
    lowered_command = (command_line or "").strip().casefold()
    if not lowered_command:
        return None

    if lowered_name == "cloudflared.exe":
        return "tunnel" if CLOUDFLARED_MARKER in lowered_command else None

    if lowered_name not in {"python.exe", "pythonw.exe"}:
        return None

    for kind, markers in SCRIPT_MARKERS.items():
        if any(marker in lowered_command for marker in markers):
            return kind
    return None


def _extract_port(endpoint: str) -> int | None:
    match = LISTENER_PORT_RE.search((endpoint or "").strip())
    if not match:
        return None
    try:
        return int(match.group(1))
    except ValueError:
        return None


def _iter_listener_pids(port: int) -> set[int]:
    result = subprocess.run(
        ["netstat", "-ano", "-p", "tcp"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="ignore",
        check=False,
    )
    if result.returncode != 0:
        return set()

    pids: set[int] = set()
    for raw_line in result.stdout.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        parts = line.split()
        if len(parts) < 5 or parts[0].upper() != "TCP":
            continue
        local_address = parts[1]
        state = parts[3].upper()
        pid_text = parts[4]
        if state != "LISTENING" or not pid_text.isdigit():
            continue
        if _extract_port(local_address) != port:
            continue
        pid_value = int(pid_text)
        if pid_value > 0 and pid_value != os.getpid():
            pids.add(pid_value)
    return pids


def _collect_runtime_state() -> dict[str, set[int]]:
    state = {"bot": set(), "webapp": set(), "tunnel": set()}

    for kind, pid_file in PID_FILE_BY_KIND.items():
        pid_value = _read_pid_file(pid_file)
        if pid_value and _pid_is_running(pid_value):
            state[kind].add(pid_value)

    for pid, name, command_line in _iter_process_rows():
        if pid <= 0 or pid == os.getpid():
            continue
        kind = _identify_runtime_kind(name, command_line)
        if kind:
            state[kind].add(pid)

    state["webapp"].update(_iter_listener_pids(WEBAPP_PORT))
    state["tunnel"].update(_iter_cloudflared_pids())
    return state


def _collect_target_pids() -> set[int]:
    state = _collect_runtime_state()
    return set().union(*state.values())


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


def wait_port_free(timeout: float = 8.0) -> int:
    deadline = time.time() + max(timeout, 0.5)
    last_seen: list[int] = []
    while time.time() < deadline:
        listeners = sorted(_iter_listener_pids(WEBAPP_PORT))
        if not listeners:
            return 0
        last_seen = listeners
        time.sleep(0.25)

    if last_seen:
        print(
            f"Port {WEBAPP_PORT} hali band: {', '.join(str(pid) for pid in last_seen)}",
            file=sys.stderr,
        )
    else:
        print(f"Port {WEBAPP_PORT} hali bo'shamadi.", file=sys.stderr)
    return 1


def verify_webapp(expected_pid: int | None, timeout: float = 8.0) -> int:
    deadline = time.time() + max(timeout, 0.5)
    last_seen: list[int] = []
    while time.time() < deadline:
        listeners = sorted(_iter_listener_pids(WEBAPP_PORT))
        if len(listeners) == 1:
            listener_pid = listeners[0]
            if expected_pid is not None and listener_pid != expected_pid:
                print(
                    "Web app listener PID mos emas: "
                    f"kutilgan {expected_pid}, topilgan {listener_pid}.",
                    file=sys.stderr,
                )
                return 1
            print(listener_pid)
            return 0
        last_seen = listeners
        time.sleep(0.25)

    if not last_seen:
        print(f"Web app {WEBAPP_PORT}-portda tinglamayapti.", file=sys.stderr)
    elif len(last_seen) > 1:
        print(
            f"Web app uchun birdan ortiq listener topildi: {', '.join(str(pid) for pid in last_seen)}",
            file=sys.stderr,
        )
    else:
        listener_pid = last_seen[0]
        print(
            "Web app listener PID mos emas: "
            f"kutilgan {expected_pid}, topilgan {listener_pid}.",
            file=sys.stderr,
        )
    return 1


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
    state = _collect_runtime_state()
    listener_pids = sorted(_iter_listener_pids(WEBAPP_PORT))
    if not any(state.values()) and not listener_pids:
        print("Runtime processlar topilmadi.")
        return 0

    for kind in ("webapp", "bot", "tunnel"):
        pids = sorted(state[kind])
        if pids:
            print(f"{kind}\t{', '.join(str(pid) for pid in pids)}")

    if listener_pids:
        print(f"listeners:{WEBAPP_PORT}\t{', '.join(str(pid) for pid in listener_pids)}")
    return 0


def _parse_pid(value: str) -> int | None:
    try:
        pid_value = int(str(value).strip())
    except (TypeError, ValueError):
        return None
    return pid_value if pid_value > 0 else None


def _parse_timeout(value: str | None, default: float) -> float:
    if value is None:
        return default
    try:
        parsed = float(value)
    except ValueError:
        return default
    return parsed if parsed > 0 else default


def main() -> int:
    action = (sys.argv[1] if len(sys.argv) > 1 else "status").strip().casefold()
    if action == "stop":
        return stop_all()
    if action == "status":
        return show_status()
    if action == "wait-port-free":
        timeout = _parse_timeout(sys.argv[2] if len(sys.argv) > 2 else None, 8.0)
        return wait_port_free(timeout)
    if action == "verify-webapp":
        expected_pid = _parse_pid(sys.argv[2] if len(sys.argv) > 2 else "")
        timeout = _parse_timeout(sys.argv[3] if len(sys.argv) > 3 else None, 8.0)
        return verify_webapp(expected_pid, timeout)

    print(
        "Usage: runtime_manager.py [stop|status|wait-port-free|verify-webapp]",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
