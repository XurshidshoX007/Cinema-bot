from __future__ import annotations

import os
import re
import shutil
import signal
import subprocess
import sys
import time
from urllib.request import urlopen
from urllib.error import URLError
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent
PID_PATH = BASE_DIR / ".stats_tunnel.pid"
URL_PATH = BASE_DIR / ".stats_webapp_url"
LOG_PATH = BASE_DIR / "stats_tunnel.log"
WEBAPP_PORT = int(os.environ.get("WEBAPP_PORT", "8080"))
URL_PATTERN = re.compile(r"https://[-a-z0-9]+\.trycloudflare\.com", re.IGNORECASE)
TUNNEL_URL_MARKER = f"http://127.0.0.1:{WEBAPP_PORT}".lower()
NGROK_API_URL = "http://127.0.0.1:4040/api/tunnels"


def _powershell_executable() -> str:
    return os.path.join(
        os.environ.get("SystemRoot", r"C:\Windows"),
        "System32",
        "WindowsPowerShell",
        "v1.0",
        "powershell.exe",
    )


def _resolve_cloudflared() -> str | None:
    discovered = shutil.which("cloudflared")
    if discovered:
        return discovered

    candidate_paths = [
        Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"))
        / "cloudflared"
        / "cloudflared.exe",
        Path(os.environ.get("ProgramFiles", r"C:\Program Files"))
        / "cloudflared"
        / "cloudflared.exe",
    ]
    for candidate in candidate_paths:
        if candidate.is_file():
            return str(candidate)

    return None


def _iter_cloudflared_pids() -> list[int]:
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
        return []

    pids: list[int] = []
    for raw_line in result.stdout.splitlines():
        parts = raw_line.strip().split("\t", 1)
        if len(parts) != 2:
            continue
        pid_text, command_line = parts
        if not pid_text.isdigit():
            continue
        if TUNNEL_URL_MARKER in command_line.casefold():
            pids.append(int(pid_text))
    return pids


def _unlink_with_retry(path: Path, *, retries: int = 20, delay: float = 0.25) -> None:
    for _ in range(retries):
        try:
            path.unlink(missing_ok=True)
            return
        except PermissionError:
            time.sleep(delay)

    path.unlink(missing_ok=True)


def _terminate_pid(pid: int) -> None:
    if pid <= 0:
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
        os.kill(pid, signal.SIGTERM)
    except OSError:
        return


def _kill_existing() -> None:
    if not PID_PATH.exists():
        pid = 0
    else:
        try:
            pid = int(PID_PATH.read_text(encoding="utf-8").strip())
        except Exception:
            pid = 0

    target_pids = set(_iter_cloudflared_pids())
    if pid > 0:
        target_pids.add(pid)

    for target_pid in sorted(target_pids):
        _terminate_pid(target_pid)

    if target_pids:
        time.sleep(0.8)

    PID_PATH.unlink(missing_ok=True)
    URL_PATH.unlink(missing_ok=True)


def _start_tunnel() -> int:
    cloudflared = _resolve_cloudflared()
    if not cloudflared:
        ngrok_url = _find_ngrok_url()
        if ngrok_url:
            URL_PATH.write_text(ngrok_url, encoding="utf-8")
            print(ngrok_url)
            return 0
        print("cloudflared topilmadi.", file=sys.stderr)
        return 1

    _kill_existing()
    _unlink_with_retry(LOG_PATH)
    _unlink_with_retry(URL_PATH)

    command = [
        cloudflared,
        "tunnel",
        "--url",
        f"http://127.0.0.1:{WEBAPP_PORT}",
        "--no-autoupdate",
    ]

    creationflags = 0
    if os.name == "nt":
        creationflags = subprocess.CREATE_NEW_PROCESS_GROUP

    with LOG_PATH.open("a", encoding="utf-8") as log_stream:
        process = subprocess.Popen(
            command,
            cwd=str(BASE_DIR),
            stdout=log_stream,
            stderr=subprocess.STDOUT,
            creationflags=creationflags,
        )

    PID_PATH.write_text(str(process.pid), encoding="utf-8")

    deadline = time.time() + 25
    while time.time() < deadline:
        if process.poll() is not None:
            break

        try:
            contents = LOG_PATH.read_text(encoding="utf-8", errors="ignore")
        except FileNotFoundError:
            contents = ""

        match = URL_PATTERN.search(contents)
        if match:
            url = match.group(0)
            URL_PATH.write_text(url, encoding="utf-8")
            print(url)
            return 0

        time.sleep(0.5)

    try:
        _terminate_pid(process.pid)
    except OSError:
        pass

    PID_PATH.unlink(missing_ok=True)
    URL_PATH.unlink(missing_ok=True)
    print("Stats tunnel URL olinmadi.", file=sys.stderr)
    return 1


def _stop_tunnel() -> int:
    _kill_existing()
    _unlink_with_retry(LOG_PATH)
    return 0


def _find_ngrok_url() -> str | None:
    try:
        with urlopen(NGROK_API_URL, timeout=3) as response:
            payload = response.read().decode("utf-8", errors="ignore")
    except (OSError, URLError):
        return None

    try:
        import json

        data = json.loads(payload)
    except Exception:
        return None

    tunnels = data.get("tunnels")
    if not isinstance(tunnels, list):
        return None

    expected_addr = f"http://localhost:{WEBAPP_PORT}".casefold()
    expected_addr_alt = f"http://127.0.0.1:{WEBAPP_PORT}".casefold()
    for tunnel in tunnels:
        if not isinstance(tunnel, dict):
            continue
        public_url = str(tunnel.get("public_url") or "").strip()
        config = tunnel.get("config") or {}
        addr = str(config.get("addr") or "").strip().casefold()
        if not public_url.startswith("https://"):
            continue
        if addr not in {expected_addr, expected_addr_alt}:
            continue
        return public_url

    return None


def main() -> int:
    action = sys.argv[1] if len(sys.argv) > 1 else "start"
    if action == "start":
        return _start_tunnel()
    if action == "stop":
        return _stop_tunnel()

    print("Usage: stats_tunnel.py [start|stop]", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
