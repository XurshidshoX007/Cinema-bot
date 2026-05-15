import os
import subprocess
import sys
import time
from pathlib import Path

if len(sys.argv) != 3:
    print("Usage: launcher.py <script> <logfile>")
    sys.exit(1)

script_name = sys.argv[1]
log_file = Path(sys.argv[2])
working_dir = Path.cwd()
script_path = working_dir / script_name
python_executable = sys.executable

log_file.parent.mkdir(parents=True, exist_ok=True)

creation_flags = 0
if os.name == "nt":
    creation_flags = subprocess.CREATE_NEW_PROCESS_GROUP

with log_file.open("a", encoding="utf-8") as log_stream:
    process = subprocess.Popen(
        [python_executable, str(script_path)],
        cwd=str(working_dir),
        stdin=subprocess.DEVNULL,
        stdout=log_stream,
        stderr=subprocess.STDOUT,
        creationflags=creation_flags,
    )

deadline = time.time() + 3.0
while time.time() < deadline:
    exit_code = process.poll()
    if exit_code is not None:
        print(f"{script_name} tez yopildi: exit_code={exit_code}", file=sys.stderr)
        sys.exit(exit_code or 1)
    time.sleep(0.2)

print(process.pid)
