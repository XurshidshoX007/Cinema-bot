"""Database file path resolution."""

import shutil
import sqlite3
from contextlib import suppress
from pathlib import Path

PRIMARY_DB_PATH = Path(__file__).resolve().with_name("movies.db").parent.parent / "movies.db"
# Keep original logic: Path(__file__).resolve().with_name("movies.db") but file is now inside database/ folder.
# So we need to point to project root.
# To preserve backward compat, we use parent.parent (database/ -> Cinema-bot/).
# Also define runtime, inspect, copy paths in same root.

RUNTIME_DB_PATH = PRIMARY_DB_PATH.with_name("movies.runtime.db")
INSPECT_DB_PATH = PRIMARY_DB_PATH.with_name("movies.inspect.db")
COPY_DB_PATH = PRIMARY_DB_PATH.with_name("movies_copy.db")


def _copy_db_sidecar(source: Path, destination: Path) -> None:
    if not source.exists():
        return
    with suppress(OSError, PermissionError):
        shutil.copy2(source, destination)


def _can_open_sqlite(path: Path) -> bool:
    try:
        with sqlite3.connect(path, timeout=1) as connection:
            connection.execute("SELECT 1")
        return True
    except sqlite3.Error:
        return False


def _inspect_sqlite(path: Path) -> dict[str, int | Path] | None:
    if not path.exists():
        return None
    try:
        with sqlite3.connect(path, timeout=1) as connection:
            connection.execute("SELECT 1")
            has_movies_table = connection.execute(
                """
                SELECT 1
                FROM sqlite_master
                WHERE type = 'table' AND name = 'movies'
                LIMIT 1
                """
            ).fetchone()
            movie_count = 0
            if has_movies_table is not None:
                row = connection.execute("SELECT COUNT(*) FROM movies").fetchone()
                movie_count = int(row[0] or 0)
            has_serial_groups_table = connection.execute(
                """
                SELECT 1
                FROM sqlite_master
                WHERE type = 'table' AND name = 'serial_groups'
                LIMIT 1
                """
            ).fetchone()
            serial_group_count = 0
            if has_serial_groups_table is not None:
                row = connection.execute("SELECT COUNT(*) FROM serial_groups").fetchone()
                serial_group_count = int(row[0] or 0)
    except (sqlite3.Error, OSError):
        return None

    try:
        modified_at_ns = path.stat().st_mtime_ns
    except OSError:
        modified_at_ns = 0

    return {
        "path": path,
        "movie_count": movie_count,
        "serial_group_count": serial_group_count,
        "modified_at_ns": modified_at_ns,
    }


def _prepare_runtime_db(primary_path: Path, runtime_path: Path) -> Path:
    candidate_paths = [
        primary_path,
        runtime_path,
        INSPECT_DB_PATH,
        COPY_DB_PATH,
    ]
    inspected: list[tuple[int, dict[str, int | Path]]] = []

    for priority, candidate_path in enumerate(candidate_paths):
        info = _inspect_sqlite(candidate_path)
        if info is not None:
            inspected.append((priority, info))

    if inspected:
        _priority, best_info = max(
            inspected,
            key=lambda item: (
                int(item[1]["movie_count"]),
                int(item[1]["serial_group_count"]),
                int(item[1]["modified_at_ns"]),
                -item[0],
            ),
        )
        return Path(best_info["path"])

    if primary_path.exists():
        shutil.copy2(primary_path, runtime_path)
        _copy_db_sidecar(
            primary_path.with_name(f"{primary_path.name}-wal"),
            runtime_path.with_name(f"{runtime_path.name}-wal"),
        )
        _copy_db_sidecar(
            primary_path.with_name(f"{primary_path.name}-shm"),
            runtime_path.with_name(f"{runtime_path.name}-shm"),
        )

    if _can_open_sqlite(runtime_path):
        return runtime_path

    return primary_path


DB_PATH = _prepare_runtime_db(PRIMARY_DB_PATH, RUNTIME_DB_PATH)
