from contextlib import suppress
from datetime import datetime, timedelta, UTC
from html import escape
from http.server import HTTPServer, BaseHTTPRequestHandler
import ipaddress
import json
from pathlib import Path
import os
import re
import socket
import sqlite3
import sys
import time
from urllib.parse import parse_qs, quote, unquote, urlencode, urlparse, urlsplit
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

# Register datetime adapter for sqlite3
sqlite3.register_adapter(datetime, lambda dt: dt.isoformat())
sqlite3.register_converter("timestamp", lambda s: datetime.fromisoformat(s.decode()))

from config import ADMIN_ID, BOT_TOKEN, get_stats_webapp_url
from database import (
    APP_TIMEZONE,
    APP_TIMEZONE_LABEL,
    DB_PATH,
    format_local_timestamp,
    get_daily_metric_series_snapshot,
    get_dashboard_summary_snapshot,
    local_day_keys,
    local_day_start_utc,
    local_now_text,
)
from services.stats_webapp_auth import (
    build_signed_share_query,
    build_signed_stats_webapp_url,
    verify_signed_share_request,
    verify_signed_stats_webapp_request,
    verify_telegram_webapp_init_data,
)

PORT = int(os.environ.get("WEBAPP_PORT", "8080"))
DEEP_LINK_WATCH_PREFIX = "watch_"
SHARE_MEDIA_ROUTE_PREFIX = "/share-media/"
SHARE_MEDIA_CACHE_TTL = 600
DETAIL_ROUTE_PREFIX = "/details/"
ACTION_ROUTE_PREFIX = "/actions/"
DETAIL_PAGE_SIZE = 50
MAX_DETAIL_SEARCH_LENGTH = 64
REQUEST_STATUS_FILTERS = {
    "all": "Barchasi",
    "pending-group": "Navbatda",
    "completed": "Bajarilgan",
    "rejected": "Rad etilgan",
    "other": "Boshqa statuslar",
}
_REQUEST_HOST_RE = re.compile(r"^\[?[A-Za-z0-9:.%-]+\]?(?::\d{1,5})?$")


class ReusableHTTPServer(HTTPServer):
    allow_reuse_address = False

    def server_bind(self) -> None:
        if os.name == "nt" and hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()


def _format_number(value: int) -> str:
    return f"{value:,}".replace(",", " ")


def _format_date(date_value: str) -> str:
    try:
        return datetime.fromisoformat(date_value).strftime("%d.%m.%Y")
    except ValueError:
        return date_value


def _db_timestamp(value: datetime) -> str:
    return value.replace(tzinfo=None, microsecond=0).strftime("%Y-%m-%d %H:%M:%S")


def _request_pipeline_counts(summary: dict[str, int]) -> dict[str, int]:
    completed = int(summary.get("completed_requests", 0) or 0)
    pending = int(summary.get("pending_requests", 0) or 0)
    rejected = int(summary.get("rejected_requests", 0) or 0)
    total = int(summary.get("total_requests", 0) or 0)
    known_total = completed + pending + rejected
    other = max(0, total - known_total)
    denominator = max(known_total, 1)
    if known_total <= 0:
        return {
            "completed": completed,
            "pending": pending,
            "rejected": rejected,
            "other": other,
            "completed_pct": 0,
            "pending_pct": 0,
            "rejected_pct": 0,
        }

    completed_pct = round((completed / denominator) * 100)
    pending_pct = round((pending / denominator) * 100)
    rejected_pct = max(0, 100 - completed_pct - pending_pct)
    return {
        "completed": completed,
        "pending": pending,
        "rejected": rejected,
        "other": other,
        "completed_pct": completed_pct,
        "pending_pct": pending_pct,
        "rejected_pct": rejected_pct,
    }


def _request_host(
    handler: BaseHTTPRequestHandler,
    *,
    fallback: str,
) -> str:
    raw_host = (handler.headers.get("Host") or "").strip()
    candidate = raw_host.split("/", 1)[0].split("\\", 1)[0]
    if candidate and _REQUEST_HOST_RE.fullmatch(candidate):
        return candidate
    return fallback


def _is_loopback_client(handler: BaseHTTPRequestHandler) -> bool:
    client_host = ""
    if handler.client_address:
        client_host = str(handler.client_address[0]).strip()
    if not client_host:
        return False

    try:
        return ipaddress.ip_address(client_host).is_loopback
    except ValueError:
        return client_host.lower() == "localhost"


def _is_local_host(host: str) -> bool:
    normalized = (host or "").split(":", 1)[0].strip().strip("[]").lower()
    return normalized in {"localhost", "127.0.0.1", "::1"}


def _is_local_stats_request(handler: BaseHTTPRequestHandler) -> bool:
    host = _request_host(handler, fallback="127.0.0.1")
    return _is_loopback_client(handler) and _is_local_host(host)


def _query_value(query: dict[str, list[str]], key: str) -> str:
    values = query.get(key) or []
    if not values:
        return ""
    return str(values[0]).strip()


def _request_scheme(handler: BaseHTTPRequestHandler) -> str:
    forwarded_proto = (
        (handler.headers.get("X-Forwarded-Proto") or "").split(",", 1)[0].strip().lower()
    )
    if forwarded_proto in {"http", "https"}:
        return forwarded_proto
    return "http" if _is_local_stats_request(handler) else "https"


def _configured_stats_base_url() -> str:
    raw_url = get_stats_webapp_url()
    if raw_url.startswith("https://"):
        return raw_url
    return ""


def _stats_base_url_for_request(handler: BaseHTTPRequestHandler) -> str:
    configured_url = _configured_stats_base_url()
    if configured_url:
        return configured_url

    host = _request_host(handler, fallback=f"127.0.0.1:{PORT}")
    return f"{_request_scheme(handler)}://{host}/"


def _is_stats_operator(user_id: int) -> bool:
    if int(user_id) == int(ADMIN_ID):
        return True
    if user_id <= 0 or not DB_PATH.exists():
        return False

    with sqlite3.connect(DB_PATH, timeout=5, check_same_thread=False) as conn:
        row = conn.execute(
            "SELECT 1 FROM helper_admins WHERE user_id = ? LIMIT 1",
            (int(user_id),),
        ).fetchone()
    return row is not None


def _stats_request_is_authorized(
    handler: BaseHTTPRequestHandler,
    query: dict[str, list[str]],
) -> bool:
    if _is_local_stats_request(handler):
        return True
    if not verify_signed_stats_webapp_request(query):
        return False
    try:
        signed_user_id = int(_query_value(query, "uid"))
    except ValueError:
        return False
    return _is_stats_operator(signed_user_id)


def _share_request_is_authorized(
    code: str,
    query: dict[str, list[str]],
    *,
    media: bool = False,
) -> bool:
    return verify_signed_share_request(code, query, media=media)


def _content_security_policy() -> str:
    return "; ".join(
        [
            "default-src 'self'",
            "script-src 'self' 'unsafe-inline' https://telegram.org",
            "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com",
            "font-src 'self' https://fonts.gstatic.com data:",
            "img-src 'self' data: https:",
            "media-src 'self' https://api.telegram.org blob:",
            "connect-src 'self' https://telegram.org https://*.telegram.org",
            "base-uri 'self'",
            "form-action 'self'",
            "object-src 'none'",
            "frame-ancestors 'self' https://web.telegram.org https://*.telegram.org https://t.me",
        ]
    )


def _send_common_headers(
    handler: BaseHTTPRequestHandler,
    *,
    content_type: str | None = None,
    content_length: str | None = None,
    cache_control: str = "no-store",
    include_csp: bool = False,
) -> None:
    if content_type:
        handler.send_header("Content-Type", content_type)
    if content_length:
        handler.send_header("Content-Length", content_length)

    handler.send_header("Cache-Control", cache_control)
    if "no-store" in cache_control.lower():
        handler.send_header("Pragma", "no-cache")
    handler.send_header("X-Content-Type-Options", "nosniff")
    handler.send_header("Referrer-Policy", "no-referrer")
    handler.send_header(
        "Permissions-Policy",
        "camera=(), microphone=(), geolocation=(), payment=()",
    )
    handler.send_header("X-Robots-Tag", "noindex, nofollow")
    if include_csp:
        handler.send_header("Content-Security-Policy", _content_security_policy())


def _redirect(handler: BaseHTTPRequestHandler, location: str) -> None:
    handler.send_response(303)
    handler.send_header("Location", location)
    handler.send_header("Cache-Control", "no-store")
    handler.end_headers()


def _send_bytes(
    handler: BaseHTTPRequestHandler,
    status: int,
    body: bytes,
    *,
    content_type: str,
    cache_control: str = "no-store",
) -> None:
    handler.send_response(status)
    _send_common_headers(
        handler,
        content_type=content_type,
        content_length=str(len(body)),
        cache_control=cache_control,
        include_csp=content_type.startswith(("text/html", "application/json")),
    )
    handler.end_headers()
    try:
        handler.wfile.write(body)
    except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, OSError):
        return


def _send_json(
    handler: BaseHTTPRequestHandler,
    status: int,
    payload: dict[str, object],
) -> None:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    _send_bytes(
        handler,
        status,
        body,
        content_type="application/json; charset=utf-8",
        cache_control="no-store",
    )


def _unauthorized_stats_page() -> bytes:
    return (
        "<!doctype html>"
        "<html lang='uz'>"
        "<head>"
        "<meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width, initial-scale=1.0'>"
        "<title>Ruxsat Yo'q</title>"
        "<style>"
        "body{font-family:Arial,sans-serif;background:#0f172a;color:#e2e8f0;"
        "display:flex;align-items:center;justify-content:center;min-height:100vh;margin:0;padding:24px;}"
        ".card{max-width:480px;background:#111827;border:1px solid #334155;border-radius:20px;padding:28px;"
        "box-shadow:0 20px 60px rgba(0,0,0,.35)}"
        "h1{margin:0 0 12px;font-size:28px}p{margin:0;line-height:1.6;color:#cbd5e1}"
        "</style>"
        "</head>"
        "<body><div class='card'><h1>Ruxsat yo'q</h1>"
        "<p>Statistika havolasi eskirgan yoki noto'g'ri. Botdagi <b>Statistika</b> tugmasi orqali qayta oching.</p>"
        "</div></body></html>"
    ).encode("utf-8")


def _unauthorized_share_page() -> bytes:
    return (
        "<!doctype html>"
        "<html lang='uz'>"
        "<head>"
        "<meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width, initial-scale=1.0'>"
        "<title>Havola Eskirgan</title>"
        "<style>"
        "body{font-family:Arial,sans-serif;background:#0f172a;color:#e2e8f0;"
        "display:flex;align-items:center;justify-content:center;min-height:100vh;margin:0;padding:24px;}"
        ".card{max-width:520px;background:#111827;border:1px solid #334155;border-radius:20px;padding:28px;"
        "box-shadow:0 20px 60px rgba(0,0,0,.35)}"
        "h1{margin:0 0 12px;font-size:28px}p{margin:0;line-height:1.6;color:#cbd5e1}"
        "</style>"
        "</head>"
        "<body><div class='card'><h1>Havola eskirgan</h1>"
        "<p>Ulashish havolasi muddati tugagan yoki noto'g'ri. Kontentni bot ichidan qayta ochib, yangidan ulashing.</p>"
        "</div></body></html>"
    ).encode("utf-8")


def _stats_auth_bootstrap_page() -> bytes:
    return (
        "<!doctype html>"
        "<html lang='uz'>"
        "<head>"
        "<meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width, initial-scale=1.0'>"
        "<title>Statistika ochilmoqda</title>"
        "<script src='https://telegram.org/js/telegram-web-app.js'></script>"
        "<style>"
        ":root{color-scheme:dark;font-family:Arial,sans-serif;}"
        "body{margin:0;min-height:100vh;display:flex;align-items:center;justify-content:center;"
        "padding:24px;background:linear-gradient(180deg,#0f172a,#111827);color:#e2e8f0;}"
        ".card{max-width:520px;width:100%;background:rgba(15,23,42,.86);border:1px solid #334155;"
        "border-radius:24px;padding:28px;box-shadow:0 24px 60px rgba(0,0,0,.35)}"
        ".badge{display:inline-flex;align-items:center;gap:10px;font-size:14px;color:#93c5fd;"
        "margin-bottom:14px;font-weight:700;letter-spacing:.02em}"
        ".spinner{width:18px;height:18px;border-radius:50%;border:3px solid rgba(148,163,184,.28);"
        "border-top-color:#38bdf8;animation:spin .9s linear infinite}"
        "h1{margin:0 0 10px;font-size:30px;color:#f8fafc}"
        "p{margin:0;line-height:1.7;color:#cbd5e1}"
        ".hint{margin-top:16px;font-size:14px;color:#94a3b8}"
        ".error{margin-top:16px;padding:14px 16px;border-radius:16px;background:#1e293b;color:#fecaca;"
        "border:1px solid rgba(248,113,113,.28);display:none}"
        "@keyframes spin{to{transform:rotate(360deg)}}"
        "</style>"
        "</head>"
        "<body>"
        "<div class='card'>"
        "<div class='badge'><span class='spinner' id='spinner'></span> Telegram tekshiruvi</div>"
        "<h1>Statistika ochilmoqda</h1>"
        "<p id='status'>Sizning ruxsatingiz tekshirilmoqda. Bir necha soniyadan keyin panel ochiladi.</p>"
        "<div class='hint' id='hint'>Agar oynani oddiy brauzerda ochgan bo'lsangiz, botdagi <b>Statistika</b> tugmasidan qayta kiring.</div>"
        "<div class='error' id='error'></div>"
        "</div>"
        "<script>"
        "(async function(){"
        "const statusEl=document.getElementById('status');"
        "const hintEl=document.getElementById('hint');"
        "const errorEl=document.getElementById('error');"
        "const spinnerEl=document.getElementById('spinner');"
        "const fail=(message)=>{statusEl.textContent='Ruxsat tasdiqlanmadi';"
        "errorEl.textContent=message;errorEl.style.display='block';spinnerEl.style.display='none';};"
        "const tg=window.Telegram&&window.Telegram.WebApp;"
        "if(!tg){fail('Sahifa Telegram ichida ochilmadi. Botdagi Statistika tugmasi orqali qayta oching.');return;}"
        "try{tg.ready();tg.expand();}catch(_err){}"
        "const initData=(tg.initData||'').trim();"
        "if(!initData){fail('Telegram ruxsat ma\\'lumoti topilmadi. Statistika tugmasini qayta bosing.');return;}"
        "hintEl.textContent='Ruxsat tasdiqlanmoqda, oynani yopmang.';"
        "try{"
        "const response=await fetch('/auth-bootstrap',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({initData:initData})});"
        "const payload=await response.json().catch(()=>({}));"
        "if(!response.ok||!payload.url){throw new Error(payload.error||'Ruxsatni tekshirib bo\\'lmadi.');}"
        "window.location.replace(payload.url);"
        "}catch(error){"
        "fail((error&&error.message)||'Statistika panelini ochib bo\\'lmadi.');"
        "}"
        "})();"
        "</script>"
        "</body>"
        "</html>"
    ).encode("utf-8")


def _make_labels(days: int = 7) -> list[str]:
    return local_day_keys(days)


def _users_block_filter(conn: sqlite3.Connection) -> str:
    try:
        columns = {
            str(row[1])
            for row in conn.execute("PRAGMA table_info(users)").fetchall()
            if len(row) > 1
        }
    except sqlite3.Error:
        return ""

    if "is_blocked" not in columns:
        return ""
    return " AND COALESCE(is_blocked, 0) = 0 "


def _table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1",
        (table_name,),
    ).fetchone()
    return row is not None


def _table_columns(conn: sqlite3.Connection, table_name: str) -> set[str]:
    try:
        return {str(row[1]) for row in conn.execute(f"PRAGMA table_info({table_name})").fetchall()}
    except sqlite3.Error:
        return set()


def _content_display_title_expr(alias: str = "m") -> str:
    return (
        "CASE "
        f"WHEN COALESCE({alias}.content_kind, 'movie') = 'serial' "
        f"AND COALESCE({alias}.episode_number, 0) > 0 "
        f"THEN COALESCE(NULLIF({alias}.series_title, ''), {alias}.title) || ' ' || {alias}.episode_number || '-qism' "
        f"ELSE COALESCE({alias}.title, 'Noma''lum kino') "
        "END"
    )


def _content_group_title_expr(alias: str = "m") -> str:
    return (
        "CASE "
        f"WHEN COALESCE({alias}.content_kind, 'movie') = 'serial' "
        f"THEN COALESCE(NULLIF({alias}.series_title, ''), {alias}.title, 'Noma''lum serial') "
        f"ELSE COALESCE({alias}.title, 'Noma''lum kino') "
        "END"
    )


def _query_summary() -> dict[str, int]:
    return get_dashboard_summary_snapshot()


def _query_daily_series(days: int = 7) -> dict[str, list[int]]:
    return get_daily_metric_series_snapshot(days)


def _query_top_movies(limit: int = 5) -> list[tuple[str, str, int, int]]:
    if not DB_PATH.exists():
        return []

    with sqlite3.connect(DB_PATH, timeout=5, check_same_thread=False) as conn:
        conn.row_factory = sqlite3.Row
        if _table_exists(conn, "movie_unique_views"):
            unique_expr = "COUNT(DISTINCT muv.user_id)"
            unique_join = "LEFT JOIN movie_unique_views muv ON muv.code = mv.code"
        else:
            unique_expr = "SUM(COALESCE(mv.unique_views, mv.views))"
            unique_join = ""
        group_title_expr = _content_group_title_expr("m")
        query = f"""
        SELECT
            COALESCE(sg.code, mv.code) AS code,
            {group_title_expr} AS display_title,
            SUM(COALESCE(mv.views, 0)) AS views,
            {unique_expr} AS unique_views
        FROM movie_views mv
        LEFT JOIN movies m ON m.code = mv.code
        LEFT JOIN serial_groups sg
          ON COALESCE(m.content_kind, 'movie') = 'serial'
         AND sg.title = COALESCE(NULLIF(m.series_title, ''), m.title)
        {unique_join}
        GROUP BY
            CASE WHEN COALESCE(m.content_kind, 'movie') = 'serial' THEN 'serial' ELSE 'movie' END,
            COALESCE(sg.code, mv.code),
            {group_title_expr}
        ORDER BY
            views DESC,
            unique_views DESC,
            code ASC
        LIMIT ?
    """
        return [
            (
                row["code"],
                row["display_title"],
                int(row["views"] or 0),
                int(row["unique_views"] or 0),
            )
            for row in conn.execute(query, (limit,)).fetchall()
        ]


def _query_recent_users(limit: int = 5) -> list[tuple[str, str, str]]:
    if not DB_PATH.exists():
        return []

    week_threshold = _db_timestamp(local_day_start_utc(6))
    with sqlite3.connect(DB_PATH, timeout=5, check_same_thread=False) as conn:
        conn.row_factory = sqlite3.Row
        blocked_filter = _users_block_filter(conn)
        query = (
            "SELECT user_id, username, full_name, last_seen FROM users "
            "WHERE last_seen >= ? "
            "AND user_id != ? "
            "AND user_id NOT IN (SELECT user_id FROM helper_admins) "
            f"{blocked_filter}"
            "ORDER BY last_seen DESC LIMIT ?"
        )
        rows = conn.execute(query, (week_threshold, ADMIN_ID, limit)).fetchall()

    return [
        (
            row["username"] or f"user_{row['user_id']}" or str(row["user_id"]),
            row["full_name"] or "No-name",
            row["last_seen"] or "",
        )
        for row in rows
    ]


def _render_chart(
    values: list[int],
    labels: list[str],
    color_var: str = "var(--primary)",
    hrefs: list[str] | None = None,
) -> str:
    maximum = max(values) if values else 0
    maximum = max(maximum, 1)
    html = ""
    for index, (val, lab) in enumerate(zip(values, labels)):
        h = int((val / maximum) * 100)
        visible_height = max(h, 8) if val > 0 else 0
        tag = "a" if hrefs and index < len(hrefs) and hrefs[index] else "div"
        href_attr = f' href="{escape(hrefs[index], quote=True)}"' if tag == "a" else ""
        link_style = ' style="text-decoration:none;color:inherit;"' if tag == "a" else ""
        html += f"""
        <{tag} class="chart-col" title="{val}"{href_attr}{link_style}>
            <div class="chart-value">{_format_number(val)}</div>
            <div class="bar-wrap"><div class="bar" style="background: {color_var}; height: {visible_height}%"></div></div>
            <div class="chart-label">{escape(lab[5:] if len(lab) > 5 else lab)}</div>
        </{tag}>
        """
    return html


def _query_share_content(code: str) -> dict[str, str] | None:
    if not DB_PATH.exists():
        return None

    normalized_code = (code or "").strip()
    if not normalized_code:
        return None

    with sqlite3.connect(DB_PATH, timeout=5, check_same_thread=False) as conn:
        conn.row_factory = sqlite3.Row

        movie_row = conn.execute(
            """
            SELECT
                title,
                description,
                COALESCE(content_kind, 'movie') AS content_kind,
                COALESCE(series_title, '') AS series_title,
                COALESCE(episode_number, 0) AS episode_number
            FROM movies
            WHERE code = ?
            LIMIT 1
            """,
            (normalized_code,),
        ).fetchone()
        if movie_row is not None:
            content_kind = "serial" if movie_row["content_kind"] == "serial" else "movie"
            base_title = (movie_row["title"] or "").strip()
            if content_kind == "serial":
                series_title = (movie_row["series_title"] or "").strip()
                episode_number = int(movie_row["episode_number"] or 0)
                if series_title:
                    if episode_number > 0:
                        base_title = f"{series_title} {episode_number}-qism"
                    else:
                        base_title = series_title

            description = (movie_row["description"] or "").strip()
            return {
                "title": base_title or "Kontent",
                "description": description,
                "label": "Serial" if content_kind == "serial" else "Kino",
            }

        group_row = conn.execute(
            """
            SELECT title, description
            FROM serial_groups
            WHERE code = ?
            LIMIT 1
            """,
            (normalized_code,),
        ).fetchone()
        if group_row is not None:
            return {
                "title": (group_row["title"] or "").strip() or "Serial",
                "description": (group_row["description"] or "").strip(),
                "label": "Serial",
            }

    return None


def _query_share_media_file_id(code: str) -> str | None:
    if not DB_PATH.exists():
        return None

    normalized_code = (code or "").strip()
    if not normalized_code:
        return None

    with sqlite3.connect(DB_PATH, timeout=5, check_same_thread=False) as conn:
        conn.row_factory = sqlite3.Row

        movie_row = conn.execute(
            """
            SELECT COALESCE(file_id, '') AS file_id
            FROM movies
            WHERE code = ?
            LIMIT 1
            """,
            (normalized_code,),
        ).fetchone()
        if movie_row is not None:
            direct_file_id = (movie_row["file_id"] or "").strip()
            if direct_file_id:
                return direct_file_id

        serial_row = conn.execute(
            """
            SELECT COALESCE(m.file_id, '') AS file_id
            FROM serial_groups AS g
            JOIN movies AS m
              ON COALESCE(NULLIF(m.series_title, ''), m.title) = g.title
            WHERE g.code = ?
              AND COALESCE(m.content_kind, 'movie') = 'serial'
              AND TRIM(COALESCE(m.file_id, '')) != ''
            ORDER BY
              CASE WHEN COALESCE(m.episode_number, 0) > 0 THEN m.episode_number ELSE 1000000 END ASC,
              m.id ASC
            LIMIT 1
            """,
            (normalized_code,),
        ).fetchone()
        if serial_row is not None:
            serial_file_id = (serial_row["file_id"] or "").strip()
            if serial_file_id:
                return serial_file_id

    return None


def _mime_type_from_file_path(file_path: str) -> str:
    lowered = (file_path or "").lower()
    if lowered.endswith(".mp4"):
        return "video/mp4"
    if lowered.endswith(".mkv"):
        return "video/x-matroska"
    if lowered.endswith(".mov"):
        return "video/quicktime"
    if lowered.endswith(".webm"):
        return "video/webm"
    if lowered.endswith(".avi"):
        return "video/x-msvideo"
    if lowered.endswith(".m4v"):
        return "video/mp4"
    return "application/octet-stream"


def _resolve_telegram_media_url(file_id: str) -> tuple[str, str] | None:
    normalized_file_id = (file_id or "").strip()
    if not normalized_file_id:
        return None

    token = (BOT_TOKEN or "").strip()
    if not token:
        return None

    api_url = (
        f"https://api.telegram.org/bot{token}/getFile"
        f"?file_id={quote(normalized_file_id, safe='')}"
    )
    request = Request(
        api_url,
        headers={
            "User-Agent": "CinemaWebApp/1.0",
            "Accept": "application/json",
        },
    )

    try:
        with urlopen(request, timeout=12) as response:
            payload = json.loads(response.read().decode("utf-8", "replace"))
    except (URLError, HTTPError, TimeoutError, OSError, ValueError, json.JSONDecodeError):
        return None

    if not isinstance(payload, dict) or not payload.get("ok"):
        return None

    result = payload.get("result")
    if not isinstance(result, dict):
        return None

    file_path = str(result.get("file_path") or "").strip()
    if not file_path:
        return None

    download_url = f"https://api.telegram.org/file/bot{token}/{quote(file_path, safe='/._-')}"
    return download_url, _mime_type_from_file_path(file_path)


_share_media_cache: dict[str, tuple[float, str, str]] = {}
_share_media_miss_cache: dict[str, float] = {}


def _resolve_share_media_source(code: str) -> tuple[str, str] | None:
    normalized_code = (code or "").strip()
    if not normalized_code:
        return None

    now = time.time()
    miss_ts = _share_media_miss_cache.get(normalized_code)
    if miss_ts is not None and now - miss_ts <= SHARE_MEDIA_CACHE_TTL:
        return None

    cached = _share_media_cache.get(normalized_code)
    if cached is not None and now - cached[0] <= SHARE_MEDIA_CACHE_TTL:
        return cached[1], cached[2]

    file_id = _query_share_media_file_id(normalized_code)
    if not file_id:
        _share_media_cache.pop(normalized_code, None)
        _share_media_miss_cache[normalized_code] = now
        return None

    resolved = _resolve_telegram_media_url(file_id)
    if resolved is None:
        _share_media_cache.pop(normalized_code, None)
        _share_media_miss_cache[normalized_code] = now
        return None

    download_url, mime_type = resolved
    _share_media_cache[normalized_code] = (now, download_url, mime_type)
    _share_media_miss_cache.pop(normalized_code, None)
    return download_url, mime_type


def _stream_share_media(handler: BaseHTTPRequestHandler, code: str) -> None:
    source = _resolve_share_media_source(code)
    if source is None:
        handler.send_error(404, "Media Not Found")
        return

    download_url, fallback_mime = source
    upstream_request = Request(
        download_url,
        headers={
            "User-Agent": "CinemaWebApp/1.0",
        },
    )
    incoming_range = (handler.headers.get("Range") or "").strip()
    if incoming_range:
        upstream_request.add_header("Range", incoming_range)

    try:
        with urlopen(upstream_request, timeout=20) as upstream:
            status = getattr(upstream, "status", 200)
            if not isinstance(status, int):
                status = 200

            handler.send_response(status)

            content_type = upstream.headers.get("Content-Type") or fallback_mime
            content_length = upstream.headers.get("Content-Length")
            content_range = upstream.headers.get("Content-Range")
            accept_ranges = upstream.headers.get("Accept-Ranges") or "bytes"

            _send_common_headers(
                handler,
                content_type=content_type,
                content_length=content_length,
                cache_control="private, max-age=300",
                include_csp=False,
            )
            if content_range:
                handler.send_header("Content-Range", content_range)
            if accept_ranges:
                handler.send_header("Accept-Ranges", accept_ranges)
            handler.end_headers()

            while True:
                chunk = upstream.read(64 * 1024)
                if not chunk:
                    break
                try:
                    handler.wfile.write(chunk)
                except (
                    BrokenPipeError,
                    ConnectionResetError,
                    ConnectionAbortedError,
                    OSError,
                ):
                    return
        return
    except HTTPError as exc:
        handler.send_error(exc.code, "Upstream Error")
        return
    except (URLError, TimeoutError, OSError):
        handler.send_error(502, "Media Proxy Error")
        return


def _safe_target_url(raw_target: str | None) -> str | None:
    value = (raw_target or "").strip()
    if not value:
        return None

    parsed = urlparse(value)
    if parsed.scheme != "https":
        return None
    if parsed.netloc.lower() not in {"t.me", "telegram.me"}:
        return None
    if not parsed.path.startswith("/"):
        return None

    return value


def _build_share_page(
    *,
    code: str,
    request_host: str,
    request_path: str,
    target_url: str | None,
    media_url: str | None,
) -> bytes:
    payload = _query_share_content(code)
    if payload is None:
        title = "Cinema"
        description = "Kontent bot ichida ochiladi."
        label = "Kontent"
    else:
        title = payload["title"] or "Kontent"
        description = payload["description"] or "Kontent bot ichida ochiladi."
        label = payload["label"] or "Kontent"

    clean_title = title.strip()
    clean_description = description.strip()
    if len(clean_description) > 260:
        clean_description = clean_description[:257].rstrip() + "..."

    open_target = target_url or "/"
    escaped_target = escape(open_target, quote=True)
    escaped_title = escape(clean_title)
    escaped_description = escape(clean_description)
    escaped_label = escape(label)
    canonical_url = f"https://{request_host}{request_path}"
    escaped_canonical = escape(canonical_url, quote=True)
    escaped_media_url = escape(media_url or "", quote=True)
    media_meta = ""
    media_block = ""

    if media_url:
        media_meta = (
            f'  <meta property="og:video" content="{escaped_media_url}">\n'
            '  <meta property="og:video:type" content="video/mp4">\n'
            '  <meta property="og:video:secure_url" content="{0}">\n'
            '  <meta name="twitter:player" content="{0}">\n'
        ).format(escaped_media_url)

        media_block = f"""
    <div class="video-wrap">
      <video id="shareVideo" class="video-preview" preload="metadata" playsinline muted poster="">
        <source src="{escaped_media_url}" type="video/mp4">
      </video>
      <a class="video-overlay" href="{escaped_target}" aria-label="Botda ko'rish">
        <span class="play-icon">▶</span>
        <span class="play-text">Botda ko'rish</span>
      </a>
    </div>"""
    else:
        media_block = f"""
    <a class="video-fallback" href="{escaped_target}" aria-label="Botda ko'rish">
      <span class="play-icon">▶</span>
      <span class="play-text">Videoni botda ko'rish</span>
    </a>"""

    html = f"""<!DOCTYPE html>
<html lang="uz">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{escaped_title} | Cinema</title>
  <meta name="description" content="{escaped_description}">
  <meta property="og:type" content="website">
  <meta property="og:site_name" content="Cinema">
  <meta property="og:title" content="{escaped_title}">
  <meta property="og:description" content="{escaped_description}">
  <meta property="og:url" content="{escaped_canonical}">
{media_meta}  <meta name="twitter:title" content="{escaped_title}">
  <meta name="twitter:description" content="{escaped_description}">
  <meta name="twitter:card" content="summary">
  <meta http-equiv="refresh" content="0;url={escaped_target}">
  <style>
    body {{
      margin: 0;
      background: #0b1220;
      color: #e2e8f0;
      font-family: Arial, sans-serif;
      display: grid;
      place-items: center;
      min-height: 100vh;
      padding: 24px;
    }}
    .card {{
      max-width: 520px;
      background: #111b2e;
      border: 1px solid #23314d;
      border-radius: 14px;
      padding: 18px;
    }}
    .label {{
      color: #7dd3fc;
      font-size: 13px;
      margin-bottom: 8px;
    }}
    h1 {{
      margin: 0 0 10px;
      font-size: 22px;
      line-height: 1.3;
    }}
    p {{
      margin: 0;
      color: #94a3b8;
      line-height: 1.5;
    }}
    .video-wrap {{
      margin: 14px 0 12px;
      border: 1px solid #22314e;
      border-radius: 12px;
      overflow: hidden;
      position: relative;
      background: #020617;
    }}
    .video-preview {{
      display: block;
      width: 100%;
      max-height: 360px;
      background: #030712;
    }}
    .video-overlay {{
      position: absolute;
      inset: 0;
      display: flex;
      align-items: center;
      justify-content: center;
      gap: 10px;
      background: linear-gradient(to top, rgba(2, 6, 23, 0.72), rgba(2, 6, 23, 0.25));
      color: #f8fafc;
      text-decoration: none;
      font-weight: 600;
    }}
    .play-icon {{
      width: 38px;
      height: 38px;
      border-radius: 999px;
      background: rgba(15, 23, 42, 0.75);
      border: 1px solid rgba(148, 163, 184, 0.4);
      display: inline-flex;
      align-items: center;
      justify-content: center;
      font-size: 15px;
      padding-left: 2px;
    }}
    .play-text {{
      font-size: 15px;
    }}
    .video-fallback {{
      margin-top: 14px;
      border: 1px solid #22314e;
      border-radius: 12px;
      background: linear-gradient(120deg, rgba(15, 23, 42, 0.95), rgba(30, 41, 59, 0.92));
      min-height: 120px;
      display: flex;
      align-items: center;
      justify-content: center;
      gap: 10px;
      color: #f8fafc;
      text-decoration: none;
      font-weight: 600;
    }}
    a {{
      display: inline-block;
      margin-top: 16px;
      color: #38bdf8;
      text-decoration: none;
      font-weight: 600;
    }}
  </style>
</head>
<body>
  <div class="card">
    <div class="label">{escaped_label}</div>
    <h1>{escaped_title}</h1>
    <p>{escaped_description}</p>
{media_block}
    <a href="{escaped_target}">Botda ochish</a>
  </div>
  <script>
    window.location.replace("{escaped_target}");
  </script>
</body>
</html>"""
    return html.encode("utf-8")


def _render_top_movies(movies: list[tuple[str, str, int, int]]) -> str:
    if not movies:
        return '<div class="text-dim text-sm" style="padding:10px 0;">Kino topilmadi.</div>'
    maximum = max(views for _, _, views, _ in movies) or 1
    rows = []
    for index, (code, title, views, unique_views) in enumerate(movies, start=1):
        safe_title = escape(title or "Noma'lum kino")
        safe_code = escape(code or "-")
        w = int((views / maximum) * 100)
        rank_class = f"rank-{index}" if index <= 3 else "rank-other"

        rows.append(
            f'<div class="list-item">'
            f'<div class="flex items-center"><div class="rank-badge {rank_class}">{index}</div>'
            f'<div><div style="font-weight:500; font-size:14px;">{safe_title}</div><div class="text-sm text-dim" style="margin-top:2px;">Kod: {safe_code} • {_format_number(unique_views)} ta unique user</div></div></div>'
            f'<div style="text-align:right;"><div class="text-sm" style="margin-bottom:4px; font-weight:600; color:var(--primary);">{_format_number(views)} ko&apos;rish</div>'
            f'<div class="progress-bg"><div class="progress-fill" style="width: {max(w, 2)}%"></div></div></div>'
            f"</div>"
        )
    return "".join(rows)


def _render_recent_users(users: list[tuple[str, str, str]]) -> str:
    if not users:
        return '<div class="text-dim text-sm" style="padding:10px 0;">Ma\'lumot yo\'q.</div>'
    html = ""
    for username, full_name, last_seen in users:
        safe_username = escape(username or "")
        safe_full_name = escape(full_name or "No-name")
        ini = escape((username or "U")[0].upper())
        hand = f"@{safe_username}" if username else "anonymous"
        last_seen_text = format_local_timestamp(last_seen, "%H:%M")
        html += f"""
        <div class="list-item">
            <div class="flex items-center">
                <div style="width:36px; height:36px; border-radius:50%; background:linear-gradient(135deg, var(--secondary), var(--primary)); display:flex; align-items:center; justify-content:center; margin-right:12px; font-weight:600; color:#fff;">{ini}</div>
                <div>
                   <div style="font-weight:500; font-size:14px; max-width: 150px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;">{safe_full_name}</div>
                   <div class="text-sm text-dim" style="max-width: 150px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;">{hand}</div>
                </div>
            </div>
            <div class="bg-success-light text-sm">{last_seen_text} {APP_TIMEZONE_LABEL}</div>
        </div>
        """
    return html


def _query_dashboard_payload() -> dict[str, object]:
    return {
        "summary": _query_summary(),
        "daily": _query_daily_series(7),
        "top_movies": _query_top_movies(5),
        "recent_users": _query_recent_users(6),
        "updated_at": f"{local_now_text()} {APP_TIMEZONE_LABEL}",
    }


def _normalize_search_text(raw_value: str) -> str:
    normalized = re.sub(r"\s+", " ", (raw_value or "").strip())
    return normalized[:MAX_DETAIL_SEARCH_LENGTH].strip()


def _parse_page_number(raw_value: str) -> int:
    try:
        page = int((raw_value or "").strip() or "1")
    except ValueError:
        return 1
    return max(page, 1)


def _pagination_meta(total_count: int, requested_page: int) -> dict[str, int]:
    total_pages = max(1, (max(total_count, 0) + DETAIL_PAGE_SIZE - 1) // DETAIL_PAGE_SIZE)
    page = min(max(requested_page, 1), total_pages)
    offset = (page - 1) * DETAIL_PAGE_SIZE
    return {
        "page": page,
        "page_size": DETAIL_PAGE_SIZE,
        "total_pages": total_pages,
        "offset": offset,
    }


def _auth_query_pairs(query: dict[str, list[str]]) -> list[tuple[str, str]]:
    pairs: list[tuple[str, str]] = []
    for key in ("uid", "exp", "sig"):
        value = _query_value(query, key)
        if value:
            pairs.append((key, value))
    return pairs


def _build_url(path: str, pairs: list[tuple[str, str]]) -> str:
    cleaned_pairs = [(key, value) for key, value in pairs if value]
    if not cleaned_pairs:
        return path
    return f"{path}?{urlencode(cleaned_pairs)}"


def _build_action_url(action_key: str, auth_query: list[tuple[str, str]]) -> str:
    return _build_url(f"{ACTION_ROUTE_PREFIX}{action_key}", auth_query)


def _dashboard_href(auth_query: list[tuple[str, str]]) -> str:
    return _build_url("/", auth_query)


def _build_detail_href(
    metric_key: str,
    auth_query: list[tuple[str, str]],
    *,
    page: int | None = None,
    q: str | None = None,
    status: str | None = None,
    day: str | None = None,
    code: str | None = None,
    user_id: int | str | None = None,
) -> str:
    pairs = list(auth_query)
    normalized_q = _normalize_search_text(q or "")
    if status and status in REQUEST_STATUS_FILTERS and status != "all":
        pairs.append(("status", status))
    if day:
        pairs.append(("day", day))
    if code:
        pairs.append(("code", code))
    if user_id:
        pairs.append(("user_id", str(user_id)))
    if normalized_q:
        pairs.append(("q", normalized_q))
    if page and page > 1:
        pairs.append(("page", str(page)))
    return _build_url(f"{DETAIL_ROUTE_PREFIX}{metric_key}", pairs)


def _format_detail_timestamp(value: str | None) -> str:
    normalized = (value or "").strip()
    if not normalized:
        return "-"
    return format_local_timestamp(normalized, "%d.%m.%Y %H:%M")


def _escape_like_value(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _build_like_param(value: str) -> str:
    return f"%{_escape_like_value(value.lower())}%"


def _user_search_clause(search_text: str) -> tuple[str, list[str]]:
    if not search_text:
        return "", []
    like_value = _build_like_param(search_text)
    clause = (
        " AND (CAST(user_id AS TEXT) LIKE ? ESCAPE '\\' "
        "OR LOWER(COALESCE(username, '')) LIKE ? ESCAPE '\\' "
        "OR LOWER(COALESCE(full_name, '')) LIKE ? ESCAPE '\\')"
    )
    return clause, [like_value, like_value, like_value]


def _request_search_clause(search_text: str) -> tuple[str, list[str]]:
    if not search_text:
        return "", []
    like_value = f"%{_escape_like_value(search_text)}%"
    clause = (
        " AND (CAST(r.id AS TEXT) LIKE ? ESCAPE '\\' "
        "OR CAST(r.user_id AS TEXT) LIKE ? ESCAPE '\\')"
    )
    return clause, [like_value, like_value]


def _movie_search_clause(search_text: str, title_expr: str) -> tuple[str, list[str]]:
    if not search_text:
        return "", []
    like_value = _build_like_param(search_text)
    clause = (
        " AND (LOWER(mv.code) LIKE ? ESCAPE '\\' "
        f"OR LOWER({title_expr}) LIKE ? ESCAPE '\\')"
    )
    return clause, [like_value, like_value]


def _render_hidden_inputs(pairs: list[tuple[str, str]]) -> str:
    return "".join(
        f'<input type="hidden" name="{escape(key, quote=True)}" value="{escape(value, quote=True)}">'
        for key, value in pairs
    )


def _render_search_form(
    *,
    action_path: str,
    auth_query: list[tuple[str, str]],
    current_q: str,
    placeholder: str,
    reset_href: str,
    hidden_pairs: list[tuple[str, str]] | None = None,
) -> str:
    hidden_inputs = _render_hidden_inputs(auth_query + list(hidden_pairs or []))
    return (
        f'<form class="card search-form" method="get" action="{escape(action_path, quote=True)}">'
        f"{hidden_inputs}"
        f'<input type="search" name="q" maxlength="{MAX_DETAIL_SEARCH_LENGTH}" '
        f'value="{escape(current_q, quote=True)}" placeholder="{escape(placeholder, quote=True)}">'
        '<button type="submit" class="button">Qidirish</button>'
        f'<a class="button ghost-button" href="{escape(reset_href, quote=True)}">Tozalash</a>'
        "</form>"
    )


def _render_stats_strip(items: list[tuple[str, str]]) -> str:
    if not items:
        return ""
    cards = []
    for label, value in items:
        cards.append(
            '<div class="stat-card">'
            f'<div class="stat-label">{escape(label)}</div>'
            f'<div class="stat-value">{escape(value)}</div>'
            "</div>"
        )
    return f'<div class="stats-strip">{"".join(cards)}</div>'


def _render_empty_card(message: str) -> str:
    return (
        '<div class="card empty-card">'
        f"<p>{escape(message)}</p>"
        "</div>"
    )


def _render_table_card(
    headers: list[str],
    rows_html: list[str],
    *,
    empty_message: str,
    min_width: int = 760,
) -> str:
    if not rows_html:
        return _render_empty_card(empty_message)
    head_html = "".join(f"<th>{escape(header)}</th>" for header in headers)
    return (
        '<div class="card table-card">'
        f'<div class="table-wrap"><table style="min-width:{min_width}px">'
        f"<thead><tr>{head_html}</tr></thead>"
        f"<tbody>{''.join(rows_html)}</tbody>"
        "</table></div></div>"
    )


def _render_pagination(
    metric_key: str,
    auth_query: list[tuple[str, str]],
    *,
    page: int,
    total_pages: int,
    q: str = "",
    status: str = "all",
) -> str:
    if total_pages <= 1:
        return ""

    page_links = []
    start_page = max(1, page - 2)
    end_page = min(total_pages, page + 2)
    for candidate in range(start_page, end_page + 1):
        href = _build_detail_href(metric_key, auth_query, page=candidate, q=q, status=status)
        class_name = "pager-link active" if candidate == page else "pager-link"
        page_links.append(
            f'<a class="{class_name}" href="{escape(href, quote=True)}">{candidate}</a>'
        )

    prev_html = '<span class="pager-link disabled">Oldingi</span>'
    if page > 1:
        prev_href = _build_detail_href(metric_key, auth_query, page=page - 1, q=q, status=status)
        prev_html = f'<a class="pager-link" href="{escape(prev_href, quote=True)}">Oldingi</a>'

    next_html = '<span class="pager-link disabled">Keyingi</span>'
    if page < total_pages:
        next_href = _build_detail_href(metric_key, auth_query, page=page + 1, q=q, status=status)
        next_html = f'<a class="pager-link" href="{escape(next_href, quote=True)}">Keyingi</a>'

    return (
        '<div class="card pager">'
        f'<div class="pager-meta">Sahifa {page} / {total_pages}</div>'
        f'<div class="pager-links">{prev_html}{"".join(page_links)}{next_html}</div>'
        "</div>"
    )


def _build_detail_shell(
    *,
    title: str,
    description: str,
    body_html: str,
    auth_query: list[tuple[str, str]],
) -> str:
    updated_at = f"{local_now_text()} {APP_TIMEZONE_LABEL}"
    back_href = _dashboard_href(auth_query)
    return f"""<!DOCTYPE html>
<html lang="uz">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
    <title>{escape(title)} | Admin Analytiq</title>
    <link href="https://fonts.googleapis.com/css2?family=Outfit:wght@300;400;500;600;700&display=swap" rel="stylesheet">
    <style>
        :root {{
            --bg: #090e17;
            --surface: #121826;
            --surface-hover: #1a2235;
            --primary: #38bdf8;
            --secondary: #818cf8;
            --accent: #f43f5e;
            --success: #10b981;
            --warning: #f59e0b;
            --text-main: #f8fafc;
            --text-muted: #94a3b8;
            --border: #1e293b;
        }}
        * {{ box-sizing: border-box; margin: 0; padding: 0; font-family: 'Outfit', sans-serif; }}
        body {{ background: var(--bg); color: var(--text-main); -webkit-font-smoothing: antialiased; }}
        .container {{ max-width: 1180px; margin: 0 auto; padding: 24px 20px 40px; }}
        .page-header {{ display: flex; justify-content: space-between; gap: 20px; flex-wrap: wrap; margin-bottom: 24px; }}
        .page-header h1 {{ font-size: 28px; margin: 10px 0 6px; }}
        .page-header p {{ color: var(--text-muted); line-height: 1.55; max-width: 760px; }}
        .back-link {{ display: inline-flex; align-items: center; gap: 8px; color: var(--primary); text-decoration: none; font-weight: 600; }}
        .back-link:hover {{ text-decoration: underline; }}
        .live-status {{ display: inline-flex; align-items: center; gap: 6px; background: rgba(16, 185, 129, 0.15); border: 1px solid rgba(16, 185, 129, 0.3); padding: 8px 12px; border-radius: 999px; color: var(--success); font-size: 12px; font-weight: 600; letter-spacing: 0.4px; height: fit-content; }}
        .card {{ background: var(--surface); border: 1px solid var(--border); border-radius: 18px; padding: 18px; }}
        .stats-strip {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 16px; margin-bottom: 18px; }}
        .stat-card {{ background: linear-gradient(180deg, rgba(255,255,255,0.03) 0%, transparent 100%); border: 1px solid rgba(255,255,255,0.04); border-radius: 14px; padding: 16px; }}
        .stat-label {{ color: var(--text-muted); font-size: 13px; text-transform: uppercase; letter-spacing: 0.4px; margin-bottom: 8px; }}
        .stat-value {{ color: var(--text-main); font-size: 24px; font-weight: 700; line-height: 1.15; }}
        .search-form {{ display: flex; gap: 12px; flex-wrap: wrap; align-items: center; margin-bottom: 18px; }}
        .search-form input {{ flex: 1 1 260px; min-width: 220px; border-radius: 12px; border: 1px solid var(--border); background: rgba(255,255,255,0.03); color: var(--text-main); padding: 12px 14px; outline: none; }}
        .search-form input::placeholder {{ color: var(--text-muted); }}
        .button {{ display: inline-flex; align-items: center; justify-content: center; border-radius: 12px; padding: 11px 16px; border: 1px solid transparent; background: var(--primary); color: #08121d; text-decoration: none; font-weight: 700; cursor: pointer; }}
        .ghost-button {{ background: transparent; color: var(--text-main); border-color: var(--border); }}
        .status-tabs {{ display: flex; flex-wrap: wrap; gap: 10px; margin-bottom: 18px; }}
        .status-tab {{ display: inline-flex; align-items: center; gap: 8px; padding: 10px 14px; border-radius: 999px; border: 1px solid var(--border); background: rgba(255,255,255,0.02); color: var(--text-main); text-decoration: none; }}
        .status-tab.active {{ border-color: rgba(56, 189, 248, 0.55); background: rgba(56, 189, 248, 0.12); color: var(--primary); }}
        .table-card {{ padding: 0; overflow: hidden; }}
        .table-wrap {{ overflow-x: auto; }}
        table {{ width: 100%; border-collapse: collapse; }}
        th, td {{ padding: 14px 16px; text-align: left; vertical-align: top; border-bottom: 1px solid rgba(255,255,255,0.06); }}
        th {{ color: var(--text-muted); font-size: 12px; text-transform: uppercase; letter-spacing: 0.45px; background: rgba(255,255,255,0.02); }}
        td {{ font-size: 14px; line-height: 1.5; }}
        tbody tr:hover {{ background: rgba(255,255,255,0.02); }}
        .muted {{ color: var(--text-muted); }}
        .badge {{ display: inline-flex; align-items: center; padding: 4px 10px; border-radius: 999px; font-size: 12px; font-weight: 700; }}
        .badge-success {{ background: rgba(16, 185, 129, 0.14); color: var(--success); }}
        .badge-warning {{ background: rgba(245, 158, 11, 0.15); color: var(--warning); }}
        .badge-accent {{ background: rgba(244, 63, 94, 0.14); color: var(--accent); }}
        .badge-neutral {{ background: rgba(148, 163, 184, 0.14); color: #cbd5e1; }}
        .pager {{ display: flex; justify-content: space-between; gap: 16px; flex-wrap: wrap; align-items: center; margin-top: 18px; }}
        .pager-meta {{ color: var(--text-muted); font-size: 13px; }}
        .pager-links {{ display: flex; gap: 8px; flex-wrap: wrap; }}
        .pager-link {{ display: inline-flex; align-items: center; justify-content: center; min-width: 40px; padding: 10px 12px; border-radius: 10px; border: 1px solid var(--border); background: rgba(255,255,255,0.02); color: var(--text-main); text-decoration: none; }}
        .pager-link.active {{ border-color: rgba(56, 189, 248, 0.55); color: var(--primary); background: rgba(56, 189, 248, 0.1); }}
        .pager-link.disabled {{ opacity: 0.45; pointer-events: none; }}
        .empty-card {{ text-align: center; color: var(--text-muted); padding: 32px 20px; }}
        .note-card {{ margin-bottom: 18px; color: var(--text-muted); line-height: 1.6; }}
        .chart {{ display: flex; align-items: flex-end; gap: 8px; height: 200px; margin-top: 18px; }}
        .chart-col {{ flex: 1; min-width: 0; height: 100%; display: flex; flex-direction: column; justify-content: flex-end; align-items: center; gap: 8px; }}
        .chart-value {{ min-height: 14px; font-size: 11px; color: var(--text-muted); line-height: 1; }}
        .bar-wrap {{ width: 100%; height: 130px; background: rgba(255,255,255,0.03); border-radius: 4px; display: flex; align-items: flex-end; overflow: hidden; }}
        .bar {{ width: 100%; display: block; border-radius: 4px 4px 0 0; transform-origin: bottom; animation: fillBar 0.8s ease-out forwards; }}
        .chart-label {{ font-size: 11px; color: var(--text-muted); }}
        .inline-link {{ color: var(--primary); text-decoration: none; font-weight: 600; }}
        .inline-link:hover {{ text-decoration: underline; }}
        @keyframes fillBar {{ from {{ transform: scaleY(0); }} to {{ transform: scaleY(1); }} }}
        @media (max-width: 768px) {{
            .container {{ padding: 18px 14px 32px; }}
            .page-header h1 {{ font-size: 24px; }}
            .search-form {{ flex-direction: column; align-items: stretch; }}
            .search-form input {{ width: 100%; }}
            .button, .ghost-button {{ width: 100%; }}
        }}
    </style>
</head>
<body>
    <div class="container">
        <header class="page-header">
            <div>
                <a class="back-link" href="{escape(back_href, quote=True)}">&larr; Dashboardga qaytish</a>
                <h1>{escape(title)}</h1>
                <p>{escape(description)}</p>
            </div>
            <div class="live-status">Yangilandi: {escape(updated_at)}</div>
        </header>
        {body_html}
    </div>
</body>
</html>"""


def _query_users_detail_page(
    *,
    include_blocked: bool,
    blocked_only: bool,
    last_seen_since: str | None,
    first_seen_since: str | None,
    order_by: str,
    search_text: str,
    page: int,
) -> dict[str, object]:
    empty_page = {
        "total_count": 0,
        "page": 1,
        "page_size": DETAIL_PAGE_SIZE,
        "total_pages": 1,
        "rows": [],
    }
    if not DB_PATH.exists():
        return empty_page

    with sqlite3.connect(DB_PATH, timeout=5, check_same_thread=False) as conn:
        conn.row_factory = sqlite3.Row
        user_columns = {
            str(row[1])
            for row in conn.execute("PRAGMA table_info(users)").fetchall()
            if len(row) > 1
        }
        has_block_tracking = "is_blocked" in user_columns
        has_blocked_at = "blocked_at" in user_columns
        if blocked_only and not has_block_tracking:
            return empty_page

        where_parts = [
            "user_id != ?",
            "user_id NOT IN (SELECT user_id FROM helper_admins)",
        ]
        params: list[object] = [ADMIN_ID]

        if blocked_only:
            where_parts.append("COALESCE(is_blocked, 0) = 1")
        elif has_block_tracking and not include_blocked:
            where_parts.append("COALESCE(is_blocked, 0) = 0")

        if last_seen_since:
            where_parts.append("COALESCE(last_seen, '') >= ?")
            params.append(last_seen_since)
        if first_seen_since:
            where_parts.append("COALESCE(first_seen, '') >= ?")
            params.append(first_seen_since)

        search_clause, search_params = _user_search_clause(search_text)
        where_sql = " AND ".join(where_parts) + search_clause
        params.extend(search_params)

        total_row = conn.execute(
            f"SELECT COUNT(*) AS total_count FROM users WHERE {where_sql}",
            tuple(params),
        ).fetchone()
        total_count = int(total_row["total_count"] or 0) if total_row else 0
        page_meta = _pagination_meta(total_count, page)

        if has_block_tracking:
            blocked_at_expr = "COALESCE(blocked_at, '')" if has_blocked_at else "''"
            status_select = (
                f"COALESCE(is_blocked, 0) AS is_blocked, {blocked_at_expr} AS blocked_at"
            )
        else:
            status_select = "0 AS is_blocked, '' AS blocked_at"

        rows = conn.execute(
            f"""
            SELECT
                user_id,
                COALESCE(username, '') AS username,
                COALESCE(full_name, '') AS full_name,
                COALESCE(first_seen, '') AS first_seen,
                COALESCE(last_seen, '') AS last_seen,
                {status_select}
            FROM users
            WHERE {where_sql}
            ORDER BY {order_by}
            LIMIT ? OFFSET ?
            """,
            tuple(params + [DETAIL_PAGE_SIZE, page_meta["offset"]]),
        ).fetchall()

    return {
        "total_count": total_count,
        "page": page_meta["page"],
        "page_size": DETAIL_PAGE_SIZE,
        "total_pages": page_meta["total_pages"],
        "rows": rows,
    }


def _query_requests_detail_page(
    *,
    search_text: str,
    page: int,
    status_filter: str,
) -> dict[str, object]:
    empty_page = {
        "total_count": 0,
        "page": 1,
        "page_size": DETAIL_PAGE_SIZE,
        "total_pages": 1,
        "rows": [],
    }
    if not DB_PATH.exists():
        return empty_page

    with sqlite3.connect(DB_PATH, timeout=5, check_same_thread=False) as conn:
        conn.row_factory = sqlite3.Row
        where_parts = [
            "r.user_id != ?",
            "r.user_id NOT IN (SELECT user_id FROM helper_admins)",
        ]
        params: list[object] = [ADMIN_ID]
        if status_filter == "pending-group":
            where_parts.append("r.status IN ('pending', 'accepted')")
        elif status_filter == "completed":
            where_parts.append("r.status = 'completed'")
        elif status_filter == "rejected":
            where_parts.append("r.status = 'rejected'")
        elif status_filter == "other":
            where_parts.append(
                "LOWER(COALESCE(r.status, 'pending')) NOT IN "
                "('pending', 'accepted', 'completed', 'rejected')"
            )

        search_clause, search_params = _request_search_clause(search_text)
        where_sql = " AND ".join(where_parts) + search_clause
        params.extend(search_params)

        total_row = conn.execute(
            f"SELECT COUNT(*) AS total_count FROM requests r WHERE {where_sql}",
            tuple(params),
        ).fetchone()
        total_count = int(total_row["total_count"] or 0) if total_row else 0
        page_meta = _pagination_meta(total_count, page)

        rows = conn.execute(
            f"""
            SELECT
                r.id,
                r.user_id,
                COALESCE(r.text, '') AS text,
                COALESCE(r.file_id, '') AS file_id,
                COALESCE(r.status, 'pending') AS status,
                COALESCE(u.username, '') AS username,
                COALESCE(u.full_name, '') AS full_name
            FROM requests r
            LEFT JOIN users u ON u.user_id = r.user_id
            WHERE {where_sql}
            ORDER BY r.id DESC
            LIMIT ? OFFSET ?
            """,
            tuple(params + [DETAIL_PAGE_SIZE, page_meta["offset"]]),
        ).fetchall()

    return {
        "total_count": total_count,
        "page": page_meta["page"],
        "page_size": DETAIL_PAGE_SIZE,
        "total_pages": page_meta["total_pages"],
        "rows": rows,
    }


def _query_top_movies_detail_page(
    *,
    search_text: str,
    page: int,
) -> dict[str, object]:
    empty_page = {
        "total_count": 0,
        "page": 1,
        "page_size": DETAIL_PAGE_SIZE,
        "total_pages": 1,
        "rows": [],
    }
    if not DB_PATH.exists():
        return empty_page

    with sqlite3.connect(DB_PATH, timeout=5, check_same_thread=False) as conn:
        conn.row_factory = sqlite3.Row
        if _table_exists(conn, "movie_unique_views"):
            unique_expr = "COUNT(DISTINCT muv.user_id)"
            unique_join = "LEFT JOIN movie_unique_views muv ON muv.code = mv.code"
        else:
            unique_expr = "SUM(COALESCE(mv.unique_views, mv.views))"
            unique_join = ""
        title_expr = _content_group_title_expr("m")
        group_select = (
            "CASE WHEN COALESCE(m.content_kind, 'movie') = 'serial' "
            "THEN COALESCE(sg.code, COALESCE(NULLIF(m.series_title, ''), m.title)) "
            "ELSE mv.code END"
        )
        kind_expr = (
            "CASE WHEN COALESCE(m.content_kind, 'movie') = 'serial' THEN 'serial' ELSE 'movie' END"
        )
        search_clause, search_params = _movie_search_clause(search_text, title_expr)

        total_row = conn.execute(
            f"""
            SELECT COUNT(*) AS total_count FROM (
                SELECT {kind_expr} AS item_kind, {group_select} AS item_code
                FROM movie_views mv
                LEFT JOIN movies m ON m.code = mv.code
                LEFT JOIN serial_groups sg
                  ON COALESCE(m.content_kind, 'movie') = 'serial'
                 AND sg.title = COALESCE(NULLIF(m.series_title, ''), m.title)
                WHERE 1 = 1 {search_clause}
                GROUP BY item_kind, item_code, {title_expr}
            )
            """,
            tuple(search_params),
        ).fetchone()
        total_count = int(total_row["total_count"] or 0) if total_row else 0
        page_meta = _pagination_meta(total_count, page)

        rows = conn.execute(
            f"""
            SELECT
                {group_select} AS item_code,
                {kind_expr} AS item_kind,
                {title_expr} AS display_title,
                SUM(COALESCE(mv.views, 0)) AS views,
                {unique_expr} AS unique_views,
                MAX(COALESCE(mv.last_viewed_at, '')) AS last_viewed_at
            FROM movie_views mv
            LEFT JOIN movies m ON m.code = mv.code
            LEFT JOIN serial_groups sg
              ON COALESCE(m.content_kind, 'movie') = 'serial'
             AND sg.title = COALESCE(NULLIF(m.series_title, ''), m.title)
            {unique_join}
            WHERE 1 = 1 {search_clause}
            GROUP BY item_kind, item_code, display_title
            ORDER BY
                views DESC,
                unique_views DESC,
                last_viewed_at DESC,
                item_code ASC
            LIMIT ? OFFSET ?
            """,
            tuple(search_params + [DETAIL_PAGE_SIZE, page_meta["offset"]]),
        ).fetchall()

    return {
        "total_count": total_count,
        "page": page_meta["page"],
        "page_size": DETAIL_PAGE_SIZE,
        "total_pages": page_meta["total_pages"],
        "rows": rows,
    }


def _query_serial_episodes_detail_page(
    *,
    group_code: str,
    page: int,
) -> dict[str, object] | None:
    if not DB_PATH.exists() or not group_code:
        return None

    with sqlite3.connect(DB_PATH, timeout=5, check_same_thread=False) as conn:
        conn.row_factory = sqlite3.Row
        group = conn.execute(
            "SELECT code, title, COALESCE(description, '') AS description FROM serial_groups WHERE code=?",
            (group_code,),
        ).fetchone()
        if group is None:
            return None

        if _table_exists(conn, "movie_unique_views"):
            unique_expr = "COUNT(DISTINCT muv.user_id)"
            unique_join = "LEFT JOIN movie_unique_views muv ON muv.code = mv.code"
        else:
            unique_expr = "COALESCE(mv.unique_views, mv.views)"
            unique_join = ""

        total_row = conn.execute(
            """
            SELECT COUNT(*) AS total_count
            FROM movies
            WHERE COALESCE(content_kind, 'movie') = 'serial'
              AND COALESCE(NULLIF(series_title, ''), title) = ?
            """,
            (str(group["title"]),),
        ).fetchone()
        total_count = int(total_row["total_count"] or 0) if total_row else 0
        page_meta = _pagination_meta(total_count, page)

        rows = conn.execute(
            f"""
            SELECT
                m.code,
                COALESCE(m.episode_number, 0) AS episode_number,
                {_content_display_title_expr("m")} AS display_title,
                COALESCE(mv.views, 0) AS views,
                {unique_expr} AS unique_views,
                COALESCE(mv.last_viewed_at, '') AS last_viewed_at
            FROM movies m
            LEFT JOIN movie_views mv ON mv.code = m.code
            {unique_join}
            WHERE COALESCE(m.content_kind, 'movie') = 'serial'
              AND COALESCE(NULLIF(m.series_title, ''), m.title) = ?
            GROUP BY m.code, episode_number, display_title, mv.views, mv.unique_views, mv.last_viewed_at
            ORDER BY
                CASE WHEN episode_number <= 0 THEN 1 ELSE 0 END,
                episode_number ASC,
                views DESC,
                m.code ASC
            LIMIT ? OFFSET ?
            """,
            (str(group["title"]), DETAIL_PAGE_SIZE, page_meta["offset"]),
        ).fetchall()

    return {
        "group": group,
        "total_count": total_count,
        "page": page_meta["page"],
        "page_size": DETAIL_PAGE_SIZE,
        "total_pages": page_meta["total_pages"],
        "rows": rows,
    }


def _day_bounds(day: str) -> tuple[str, str] | None:
    try:
        local_start = datetime.fromisoformat(day).replace(tzinfo=APP_TIMEZONE)
    except ValueError:
        return None
    utc_start = local_start.astimezone(UTC).replace(tzinfo=None, microsecond=0)
    utc_end = (local_start + timedelta(days=1)).astimezone(UTC).replace(
        tzinfo=None,
        microsecond=0,
    )
    return _db_timestamp(utc_start), _db_timestamp(utc_end)


def _query_content_activity_day_page(
    *,
    day: str,
    page: int,
) -> dict[str, object]:
    empty_page = {
        "total_count": 0,
        "page": 1,
        "page_size": DETAIL_PAGE_SIZE,
        "total_pages": 1,
        "rows": [],
        "day": day,
        "has_events": False,
    }
    bounds = _day_bounds(day)
    if not bounds or not DB_PATH.exists():
        return empty_page

    start_at, end_at = bounds
    with sqlite3.connect(DB_PATH, timeout=5, check_same_thread=False) as conn:
        conn.row_factory = sqlite3.Row
        if not _table_exists(conn, "content_view_events"):
            return empty_page

        title_expr = _content_display_title_expr("m")
        total_row = conn.execute(
            """
            SELECT COUNT(*) AS total_count FROM (
                SELECT cve.code
                FROM content_view_events cve
                WHERE cve.viewed_at >= ? AND cve.viewed_at < ?
                GROUP BY cve.code
            )
            """,
            (start_at, end_at),
        ).fetchone()
        total_count = int(total_row["total_count"] or 0) if total_row else 0
        page_meta = _pagination_meta(total_count, page)
        rows = conn.execute(
            f"""
            SELECT
                cve.code,
                {title_expr} AS display_title,
                COALESCE(m.content_kind, 'movie') AS content_kind,
                COUNT(*) AS views,
                COUNT(DISTINCT cve.user_id) AS unique_users,
                MAX(cve.viewed_at) AS last_viewed_at
            FROM content_view_events cve
            LEFT JOIN movies m ON m.code = cve.code
            WHERE cve.viewed_at >= ? AND cve.viewed_at < ?
            GROUP BY cve.code, display_title, content_kind
            ORDER BY views DESC, unique_users DESC, last_viewed_at DESC, cve.code ASC
            LIMIT ? OFFSET ?
            """,
            (start_at, end_at, DETAIL_PAGE_SIZE, page_meta["offset"]),
        ).fetchall()

    return {
        "total_count": total_count,
        "page": page_meta["page"],
        "page_size": DETAIL_PAGE_SIZE,
        "total_pages": page_meta["total_pages"],
        "rows": rows,
        "day": day,
        "has_events": True,
    }


def _query_user_today_activity_map(user_ids: list[int]) -> dict[int, str]:
    if not user_ids or not DB_PATH.exists():
        return {}

    bounds = _day_bounds(local_day_keys(1)[0])
    if not bounds:
        return {}
    start_at, end_at = bounds
    placeholders = ", ".join("?" for _ in user_ids)
    result: dict[int, str] = {user_id: "" for user_id in user_ids}
    with sqlite3.connect(DB_PATH, timeout=5, check_same_thread=False) as conn:
        conn.row_factory = sqlite3.Row
        if _table_exists(conn, "user_search_events"):
            rows = conn.execute(
                f"""
                SELECT user_id, raw_query, normalized_code, resolved_code, result_status, content_kind
                FROM user_search_events
                WHERE created_at >= ? AND created_at < ?
                  AND user_id IN ({placeholders})
                ORDER BY created_at DESC
                """,
                (start_at, end_at, *user_ids),
            ).fetchall()
            seen_counts: dict[int, int] = {}
            snippets: dict[int, list[str]] = {}
            for row in rows:
                user_id = int(row["user_id"])
                count = seen_counts.get(user_id, 0)
                seen_counts[user_id] = count + 1
                if count >= 3:
                    continue
                raw_query = str(row["raw_query"] or row["normalized_code"] or "-")
                resolved = str(row["resolved_code"] or "")
                status = str(row["result_status"] or "")
                label = raw_query
                if resolved:
                    label = f"{raw_query} -> {resolved}"
                if status and status != "movie":
                    label = f"{label} ({status})"
                snippets.setdefault(user_id, []).append(escape(label))
            for user_id, values in snippets.items():
                extra = seen_counts.get(user_id, 0) - len(values)
                result[user_id] = "; ".join(values) + (f"; +{extra}" if extra > 0 else "")

        if _table_exists(conn, "content_view_events"):
            rows = conn.execute(
                f"""
                SELECT cve.user_id, cve.code, {_content_display_title_expr("m")} AS display_title, COUNT(*) AS views
                FROM content_view_events cve
                LEFT JOIN movies m ON m.code = cve.code
                WHERE cve.viewed_at >= ? AND cve.viewed_at < ?
                  AND cve.user_id IN ({placeholders})
                GROUP BY cve.user_id, cve.code, display_title
                ORDER BY cve.user_id ASC, views DESC
                """,
                (start_at, end_at, *user_ids),
            ).fetchall()
            opened: dict[int, list[str]] = {}
            for row in rows:
                user_id = int(row["user_id"])
                if len(opened.setdefault(user_id, [])) >= 3:
                    continue
                opened[user_id].append(escape(str(row["display_title"] or row["code"] or "-")))
            for user_id, values in opened.items():
                prefix = result.get(user_id, "")
                opened_text = "Ochgan: " + "; ".join(values)
                result[user_id] = f"{prefix}<br>{opened_text}" if prefix else opened_text

    return {user_id: value for user_id, value in result.items() if value}


def _query_user_activity_detail_page(
    *,
    user_id: int,
    page: int,
) -> dict[str, object] | None:
    if user_id <= 0 or not DB_PATH.exists():
        return None

    with sqlite3.connect(DB_PATH, timeout=5, check_same_thread=False) as conn:
        conn.row_factory = sqlite3.Row
        user = conn.execute(
            "SELECT user_id, username, full_name, COALESCE(first_seen, '') AS first_seen FROM users WHERE user_id=?",
            (user_id,),
        ).fetchone()
        if user is None:
            return None
        first_seen = str(user["first_seen"] or "").strip()

        search_exists = _table_exists(conn, "user_search_events")
        view_exists = _table_exists(conn, "content_view_events")
        if not search_exists and not view_exists:
            return {
                "user": user,
                "total_count": 0,
                "page": 1,
                "page_size": DETAIL_PAGE_SIZE,
                "total_pages": 1,
                "rows": [],
            }

        union_parts = []
        params: list[object] = []
        if search_exists:
            first_seen_filter = " AND created_at >= ?" if first_seen else ""
            union_parts.append(
                f"""
                SELECT
                    created_at AS event_at,
                    'search' AS event_type,
                    raw_query,
                    normalized_code,
                    resolved_code,
                    result_status,
                    content_kind,
                    NULL AS title
                FROM user_search_events
                WHERE user_id=?
                {first_seen_filter}
                """
            )
            params.append(user_id)
            if first_seen:
                params.append(first_seen)
        if view_exists:
            first_seen_filter = " AND cve.viewed_at >= ?" if first_seen else ""
            union_parts.append(
                f"""
                SELECT
                    cve.viewed_at AS event_at,
                    'view' AS event_type,
                    NULL AS raw_query,
                    cve.code AS normalized_code,
                    cve.code AS resolved_code,
                    'opened' AS result_status,
                    COALESCE(m.content_kind, 'movie') AS content_kind,
                    {_content_display_title_expr("m")} AS title
                FROM content_view_events cve
                LEFT JOIN movies m ON m.code = cve.code
                WHERE cve.user_id=?
                {first_seen_filter}
                """
            )
            params.append(user_id)
            if first_seen:
                params.append(first_seen)

        union_sql = " UNION ALL ".join(union_parts)
        total_row = conn.execute(
            f"SELECT COUNT(*) AS total_count FROM ({union_sql})",
            tuple(params),
        ).fetchone()
        total_count = int(total_row["total_count"] or 0) if total_row else 0
        page_meta = _pagination_meta(total_count, page)
        rows = conn.execute(
            f"""
            SELECT * FROM ({union_sql})
            ORDER BY event_at DESC
            LIMIT ? OFFSET ?
            """,
            tuple(params + [DETAIL_PAGE_SIZE, page_meta["offset"]]),
        ).fetchall()

    return {
        "user": user,
        "total_count": total_count,
        "page": page_meta["page"],
        "page_size": DETAIL_PAGE_SIZE,
        "total_pages": page_meta["total_pages"],
        "rows": rows,
    }


def _short_text(value: str, limit: int = 120) -> str:
    compact = re.sub(r"\s+", " ", (value or "").strip())
    if len(compact) <= limit:
        return compact
    return compact[: limit - 3].rstrip() + "..."


def _render_request_tabs(
    *,
    auth_query: list[tuple[str, str]],
    current_status: str,
    current_q: str,
    counts: dict[str, int],
) -> str:
    items = [
        ("all", REQUEST_STATUS_FILTERS["all"], counts.get("all", 0)),
        ("pending-group", REQUEST_STATUS_FILTERS["pending-group"], counts.get("pending-group", 0)),
        ("completed", REQUEST_STATUS_FILTERS["completed"], counts.get("completed", 0)),
        ("rejected", REQUEST_STATUS_FILTERS["rejected"], counts.get("rejected", 0)),
    ]
    if counts.get("other", 0):
        items.append(("other", REQUEST_STATUS_FILTERS["other"], counts.get("other", 0)))
    links = []
    for status_key, label, count in items:
        href = _build_detail_href(
            "requests-overview",
            auth_query,
            q=current_q,
            status=status_key,
        )
        class_name = "status-tab active" if current_status == status_key else "status-tab"
        links.append(
            f'<a class="{class_name}" href="{escape(href, quote=True)}">{escape(label)} <span class="muted">{_format_number(count)}</span></a>'
        )
    return f'<div class="status-tabs">{"".join(links)}</div>'


def _build_page() -> str:
    summary = _query_summary()
    daily = _query_daily_series(7)
    top_movies = _query_top_movies(5)
    recent_users = _query_recent_users(6)
    updated_at = f"{local_now_text()} {APP_TIMEZONE_LABEL}"

    pipeline = _request_pipeline_counts(summary)
    completed_pct = pipeline["completed_pct"]
    pending_pct = pipeline["pending_pct"]
    rejected_pct = pipeline["rejected_pct"]
    other_requests = pipeline["other"]
    other_status_note = (
        f'<div class="text-sm text-dim" style="margin-top:12px;">Boshqa statuslar pipeline foiziga kiritilmadi: {_format_number(other_requests)} ta.</div>'
        if other_requests
        else ""
    )

    return """<!DOCTYPE html>
<html lang="uz">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
    <title>Analytiq Dashboard</title>
    <link href="https://fonts.googleapis.com/css2?family=Outfit:wght@300;400;500;600;700&display=swap" rel="stylesheet">
    <style>
         /* Clean, high-contrast UI variables */
        :root {{
            --bg: #090e17;
            --surface: #121826;
            --surface-hover: #1a2235;
            --primary: #38bdf8;
            --secondary: #818cf8;
            --accent: #f43f5e;
            --success: #10b981;
            --warning: #f59e0b;
            --text-main: #f8fafc;
            --text-muted: #94a3b8;
            --border: #1e293b;
        }}
        * {{ margin: 0; padding: 0; box-sizing: border-box; font-family: 'Outfit', sans-serif; }}
        body {{ background-color: var(--bg); color: var(--text-main); font-size: 15px; -webkit-font-smoothing: antialiased; padding-bottom: 40px; }}
        
        .container {{ max-width: 1100px; margin: 0 auto; padding: 20px; }}
        
        /* Typography */
        h1 {{ font-size: 24px; font-weight: 600; margin-bottom: 4px; }}
        h2 {{ font-size: 18px; font-weight: 500; color: var(--text-main); margin-bottom: 20px; display: flex; align-items: center; gap: 8px; }}
        .text-dim {{ color: var(--text-muted); }}
        .text-sm {{ font-size: 13px; }}
        
        /* Flex & Grid Utils */
        .flex {{ display: flex; }}
        .flex-col {{ display: flex; flex-direction: column; }}
        .items-center {{ align-items: center; }}
        .justify-between {{ justify-content: space-between; }}
        .gap-4 {{ gap: 16px; }}
        .gap-6 {{ gap: 24px; }}
        
        .grid {{ display: grid; gap: 20px; }}
        .grid-cols-2 {{ grid-template-columns: repeat(auto-fit, minmax(320px, 1fr)); }}
        .grid-cols-4 {{ grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); }}
        
        /* Card */
        .card {{ background: var(--surface); border: 1px solid var(--border); border-radius: 16px; padding: 20px; transition: transform 0.2s; }}
        .card:hover {{ border-color: rgba(56, 189, 248, 0.4); }}
        
        /* Hero Metrics */
        .metric-box {{ text-align: center; padding: 24px 10px; background: linear-gradient(180deg, rgba(255,255,255,0.03) 0%, transparent 100%); border-radius: 12px; }}
        .metric-title {{ font-size: 14px; font-weight: 500; color: var(--text-muted); margin-bottom: 12px; text-transform: uppercase; letter-spacing: 0.5px; }}
        .metric-val {{ font-size: 42px; font-weight: 700; line-height: 1; margin-bottom: 8px; }}
        .metric-sub {{ font-size: 13px; font-weight: 500; line-height: 1.45; }}
        .metric-sub + .metric-sub {{ margin-top: 4px; }}
        
        /* Specific Colors */
        .c-primary {{ color: var(--primary); }}
        .c-success {{ color: var(--success); }}
        .c-warning {{ color: var(--warning); }}
        .c-accent {{ color: var(--accent); }}
        .bg-success-light {{ background: rgba(16, 185, 129, 0.15); color: var(--success); padding: 4px 8px; border-radius: 6px; font-weight: 600; display: inline-block; }}
        .bg-warning-light {{ background: rgba(245, 158, 11, 0.15); color: var(--warning); padding: 4px 8px; border-radius: 6px; font-weight: 600; display: inline-block; }}
        .bg-accent-light {{ background: rgba(244, 63, 94, 0.15); color: var(--accent); padding: 4px 8px; border-radius: 6px; font-weight: 600; display: inline-block; }}
        
        /* Status Bar */
        .status-pipeline {{ display: flex; height: 12px; border-radius: 6px; overflow: hidden; margin-top: 15px; background: rgba(255,255,255,0.05); }}
        .pipeline-item {{ height: 100%; transition: width 1s ease-out; animation: fillWidth 1.5s ease-out forwards; }}
        .legend {{ display: flex; justify-content: space-between; margin-top: 16px; font-size: 13px; font-weight: 500; flex-wrap: wrap; gap: 10px; }}
        .legend > div {{ display: flex; align-items: center; gap: 6px; }}
        .dot {{ width: 10px; height: 10px; border-radius: 50%; }}

        /* Lists */
        .list-item {{ display: flex; justify-content: space-between; align-items: center; padding: 12px 0; border-bottom: 1px solid rgba(255,255,255,0.05); }}
        .list-item:last-child {{ border-bottom: none; }}
        .rank-badge {{ width: 28px; height: 28px; border-radius: 8px; background: rgba(255,255,255,0.1); display: flex; align-items: center; justify-content: center; font-weight: 600; font-size: 13px; margin-right: 12px; flex-shrink: 0; }}
        .rank-1 {{ background: rgba(245, 158, 11, 0.2); color: var(--warning); }}
        .rank-2 {{ background: rgba(148, 163, 184, 0.2); color: #cbd5e1; }}
        .rank-3 {{ background: rgba(217, 119, 6, 0.2); color: #b45309; }}
        .rank-other {{ background: rgba(255,255,255,0.05); color: var(--text-muted); }}
        
        .progress-bg {{ height: 6px; background: rgba(255,255,255,0.1); border-radius: 3px; width: 80px; overflow: hidden; display:inline-block; vertical-align: middle; }}
        .progress-fill {{ height: 100%; background: var(--primary); border-radius: 3px; animation: fillWidth 1s ease-out forwards; }}
        
        /* Bar chart */
        .chart {{ display: flex; align-items: flex-end; gap: 8px; height: 180px; margin-top: 20px; }}
        .chart-col {{ flex: 1; min-width: 0; height: 100%; display: flex; flex-direction: column; justify-content: flex-end; align-items: center; gap: 8px; }}
        .chart-value {{ min-height: 14px; font-size: 11px; color: var(--text-muted); line-height: 1; }}
        .bar-wrap {{ width: 100%; height: 120px; flex: none; background: rgba(255,255,255,0.03); border-radius: 4px; display: flex; align-items: flex-end; overflow: hidden; }}
        .bar-wrap:hover .bar {{ filter: brightness(1.2); }}
        .bar {{ width: 100%; display: block; border-radius: 4px 4px 0 0; animation: fillBar 1s ease-out forwards; transform-origin: bottom; }}
        .chart-label {{ font-size: 11px; color: var(--text-muted); }}
        
        /* Header Live indicator */
        .live-status {{ display: inline-flex; align-items: center; gap: 6px; background: rgba(16, 185, 129, 0.15); border: 1px solid rgba(16, 185, 129, 0.3); padding: 4px 10px; border-radius: 50px; color: var(--success); font-size: 12px; font-weight: 600; letter-spacing: 0.5px; }}
        @keyframes pulse-dot {{ 0% {{ opacity: 1; }} 50% {{ opacity: 0.4; }} 100% {{ opacity: 1; }} }}
        .dot-blink {{ width: 6px; height: 6px; background: var(--success); border-radius: 50%; animation: pulse-dot 1.5s infinite; }}

        /* Animations */
        .fade-in {{ animation: fadeIn 0.4s ease-out forwards; opacity: 0; }}
        @keyframes fadeIn {{ from{{ opacity:0; transform:translateY(10px); }} to{{ opacity:1; transform:translateY(0); }} }}
        @keyframes fillBar {{ from {{ transform: scaleY(0); }} to {{ transform: scaleY(1); }} }}
        @keyframes fillWidth {{ from {{ width: 0; }} }}

    </style>
</head>
<body style="background-color:#090e17;color:#f8fafc;">
    <div class="container">
        <!-- Header -->
        <header class="flex justify-between items-center fade-in" style="margin-bottom: 30px;">
            <div>
                <h1>Admin Analytiq</h1>
                <p class="text-dim text-sm">Bot va tizim ma'lumotlari markazi</p>
            </div>
            <div class="live-status"><div class="dot-blink"></div>JONLI</div>
        </header>

        <!-- Main 6 Metrics -->
        <div class="grid grid-cols-4 fade-in" style="animation-delay: 0.1s; margin-bottom: 24px;">
            <div class="card metric-box">
                <div class="metric-title">Faol Obunachilar</div>
                <div class="metric-val c-primary">{total_users}</div>
                <div class="metric-sub text-dim">Hozir botni bloklamagan userlar</div>
            </div>
            <div class="card metric-box">
                <div class="metric-title">Jami Kirganlar</div>
                <div class="metric-val">{all_time_users}</div>
                <div class="metric-sub text-dim">Admin/helperlardan tashqari barcha userlar</div>
            </div>
            <div class="card metric-box">
                <div class="metric-title">Bugun Kirganlar</div>
                <div class="metric-val c-success">{entered_today}</div>
                <div class="metric-sub text-dim">Bugun oxirgi faolligi bor userlar</div>
            </div>
            <div class="card metric-box">
                <div class="metric-title">Bugun Yangi Obunachi</div>
                <div class="metric-val c-primary">{new_subscribers_today}</div>
                <div class="metric-sub text-dim">Bugun birinchi marta kirgan foydalanuvchilar</div>
            </div>
            <div class="card metric-box" style="border-color: rgba(244, 63, 94, 0.3);">
                <div class="metric-title" style="color: var(--accent)">Bloklaganlar</div>
                <div class="metric-val c-accent">{blocked_users}</div>
                <div class="metric-sub text-dim">Hozir blok holatida turgan userlar</div>
            </div>
            <div class="card metric-box" style="border-color: rgba(245, 158, 11, 0.3);">
                <div class="metric-title" style="color: var(--warning)">24 Soat Faol</div>
                <div class="metric-val c-warning">{active_today}</div>
                <div class="metric-sub text-dim">So'nggi 24 soat, bloklamaganlar<br>7 kun faol: <span style="color:var(--text-main)">{active_week}</span></div>
            </div>
        </div>

        <!-- Request Pipeline Bar -->
        <div class="card fade-in" style="animation-delay: 0.2s; margin-bottom: 24px;">
            <h2>So'rovlar pipeline</h2>
            <div class="flex justify-between items-center text-sm">
                <span class="text-dim">Jami kelib tushgan so'rovlar: <strong style="color:var(--text-main); font-size:16px;">{total_requests}</strong> ta</span>
            </div>
            
            <div class="status-pipeline">
                <div class="pipeline-item" style="background: var(--success); width: {completed_pct}%;"></div>
                <div class="pipeline-item" style="background: var(--warning); width: {pending_pct}%;"></div>
                <div class="pipeline-item" style="background: var(--accent); width: {rejected_pct}%;"></div>
            </div>
            
            <div class="legend">
                <div><div class="dot" style="background: var(--success);"></div> {completed_requests} Bajarilgan ({completed_pct}%)</div>
                <div><div class="dot" style="background: var(--warning);"></div> {pending_requests} Navbatda ({pending_pct}%)</div>
                <div><div class="dot" style="background: var(--accent);"></div> {rejected_requests} Rad etilgan ({rejected_pct}%)</div>
            </div>
            {other_status_note}
        </div>

        <div class="grid grid-cols-2">
            <!-- Traffic Chart -->
            <div class="card fade-in" style="animation-delay: 0.3s;">
                <h2>Oxirgi 7 kun murojaatlar</h2>
                <div class="text-sm text-dim">daily_stats jadvalidagi requests agregati</div>
                <div class="chart">
                    {requests_bars}
                </div>
            </div>

            <!-- Views Chart -->
            <div class="card fade-in" style="animation-delay: 0.4s;">
                <h2>Kontent faolligi</h2>
                <div class="text-sm text-dim">daily_stats jadvalidagi movie_views agregati</div>
                <div class="chart">
                    {views_bars}
                </div>
            </div>
            
            <!-- Top Movies List -->
            <div class="card fade-in" style="animation-delay: 0.5s;">
                <h2>Top kinolar</h2>
                <div>
                    {top_movies_html}
                </div>
            </div>

            <!-- Recent Users List -->
            <div class="card fade-in" style="animation-delay: 0.6s;">
                <h2>Yaqinda faol bo'lganlar</h2>
                <div>
                    {recent_users_html}
                </div>
            </div>
        </div>

        <div class="text-sm text-dim flex justify-between" style="margin-top: 30px;">
            <span>Yaratildi: PrimeCinema Tech</span>
            <span>So'nggi yangilanish: {updated_at}</span>
        </div>
    </div>
</body>
</html>""".format(
        updated_at=updated_at,
        total_users=_format_number(summary["total_users"]),
        all_time_users=_format_number(summary["all_time_users"]),
        entered_today=_format_number(summary["entered_today"]),
        new_subscribers_today=_format_number(summary["new_subscribers_today"]),
        blocked_users=_format_number(summary["blocked_users"]),
        joined_today=_format_number(summary["joined_today"]),
        active_today=_format_number(summary["active_today"]),
        active_week=_format_number(summary["active_week"]),
        total_movies=_format_number(summary["total_movies"]),
        total_views=_format_number(summary["total_views"]),
        total_requests=_format_number(summary["total_requests"]),
        pending_requests=_format_number(summary["pending_requests"]),
        completed_requests=_format_number(summary["completed_requests"]),
        rejected_requests=_format_number(summary["rejected_requests"]),
        other_status_note=other_status_note,
        completed_pct=completed_pct,
        pending_pct=pending_pct,
        rejected_pct=rejected_pct,
        requests_bars=_render_chart(
            daily["requests"], daily["labels"], "var(--primary)"
        ),
        views_bars=_render_chart(
            daily["movie_views"], daily["labels"], "var(--secondary)"
        ),
        top_movies_html=_render_top_movies(top_movies),
        recent_users_html=_render_recent_users(recent_users),
    )

def _build_user_detail_page(
    metric_key: str,
    auth_query: list[tuple[str, str]],
    query: dict[str, list[str]],
    config: dict[str, object],
) -> str:
    search_text = _normalize_search_text(_query_value(query, "q"))
    requested_page = _parse_page_number(_query_value(query, "page"))
    page_data = _query_users_detail_page(
        include_blocked=bool(config["include_blocked"]),
        blocked_only=bool(config["blocked_only"]),
        last_seen_since=str(config["last_seen_since"] or "") or None,
        first_seen_since=str(config["first_seen_since"] or "") or None,
        order_by=str(config["order_by"]),
        search_text=search_text,
        page=requested_page,
    )

    stats = [
        ("Jami", _format_number(int(page_data["total_count"]))),
        ("Sahifa", f"{page_data['page']} / {page_data['total_pages']}"),
    ]
    if search_text:
        stats.append(("Qidiruv", search_text))
    if metric_key == "active-24h":
        summary = _query_summary()
        stats.append(("7 kun faol", _format_number(summary["active_week"])))

    rows_html = []
    row_user_ids = [int(row["user_id"]) for row in page_data["rows"]]
    today_activity = _query_user_today_activity_map(row_user_ids)
    for row in page_data["rows"]:
        username = str(row["username"] or "").strip()
        full_name = str(row["full_name"] or "").strip() or f"User {row['user_id']}"
        user_id = int(row["user_id"])
        status_badge = (
            '<span class="badge badge-accent">Bloklangan</span>'
            if int(row["is_blocked"] or 0)
            else '<span class="badge badge-success">Faol</span>'
        )
        activity_href = _build_detail_href("user-activity", auth_query, user_id=user_id)
        activity_text = today_activity.get(user_id) or (
            '<span class="muted">Bugun qidiruv/ochish yo&apos;q</span>'
        )
        rows_html.append(
            "<tr>"
            f"<td>{user_id}</td>"
            f"<td>{escape(full_name)}</td>"
            f"<td>{escape(f'@{username}' if username else '-')}</td>"
            f"<td>{escape(_format_detail_timestamp(row['first_seen']))}</td>"
            f"<td>{escape(_format_detail_timestamp(row['last_seen']))}</td>"
            f"<td>{status_badge}</td>"
            f"<td>{escape(_format_detail_timestamp(row['blocked_at']))}</td>"
            f"<td>{activity_text}<br><a class=\"inline-link\" href=\"{escape(activity_href, quote=True)}\">Batafsil</a></td>"
            "</tr>"
        )

    body_html = _render_stats_strip(stats)
    if metric_key == "blocked-users" and int(page_data["total_count"]) > 0:
        action_url = _build_action_url("delete-blocked-users", auth_query)
        body_html += (
            '<form class="card note-card" method="post" '
            f'action="{escape(action_url, quote=True)}" '
            'onsubmit="return confirm(\'Bloklagan userlar users jadvalidan o\\\'chirilsinmi?\');">'
            '<p>Bu amal bloklagan foydalanuvchilarni faqat users ro&apos;yxatidan o&apos;chiradi. '
            'Tarixiy ko&apos;rish statistikasi saqlanadi.</p>'
            '<button class="button" type="submit" style="margin-top:12px;background:var(--accent);color:white;">'
            'Bloklaganlarni udalit qilish</button>'
            '</form>'
        )
    body_html += _render_search_form(
        action_path=f"{DETAIL_ROUTE_PREFIX}{metric_key}",
        auth_query=auth_query,
        current_q=search_text,
        placeholder="ID, username yoki ism bo'yicha qidiring",
        reset_href=_build_detail_href(metric_key, auth_query),
    )
    if config.get("note"):
        body_html += f'<div class="card note-card"><p>{escape(str(config["note"]))}</p></div>'
    body_html += _render_table_card(
        ["User ID", "Ism", "Username", "Birinchi kirgan", "Oxirgi faollik", "Holat", "Blok vaqti", "Bugungi qidiruv / ochgan"],
        rows_html,
        empty_message="Bu filtr bo'yicha foydalanuvchi topilmadi.",
        min_width=1180,
    )
    body_html += _render_pagination(
        metric_key,
        auth_query,
        page=int(page_data["page"]),
        total_pages=int(page_data["total_pages"]),
        q=search_text,
    )
    return _build_detail_shell(
        title=str(config["title"]),
        description=str(config["description"]),
        body_html=body_html,
        auth_query=auth_query,
    )


def _build_requests_overview_page(
    auth_query: list[tuple[str, str]],
    query: dict[str, list[str]],
) -> str:
    status_filter = _query_value(query, "status")
    if status_filter not in REQUEST_STATUS_FILTERS:
        status_filter = "all"
    search_text = _normalize_search_text(_query_value(query, "q"))
    requested_page = _parse_page_number(_query_value(query, "page"))
    page_data = _query_requests_detail_page(
        search_text=search_text,
        page=requested_page,
        status_filter=status_filter,
    )
    summary = _query_summary()
    counts = {
        "all": summary["total_requests"],
        "pending-group": summary["pending_requests"],
        "completed": summary["completed_requests"],
        "rejected": summary["rejected_requests"],
        "other": _request_pipeline_counts(summary)["other"],
    }

    stats = [
        ("Jami", _format_number(summary["total_requests"])),
        ("Ko'rsatilgan", _format_number(int(page_data["total_count"]))),
        ("Status", REQUEST_STATUS_FILTERS[status_filter]),
    ]
    if counts["other"]:
        stats.append(("Boshqa status", _format_number(counts["other"])))
    if search_text:
        stats.append(("Qidiruv", search_text))

    rows_html = []
    for row in page_data["rows"]:
        full_name = str(row["full_name"] or "").strip() or f"User {row['user_id']}"
        username = str(row["username"] or "").strip()
        status_value = str(row["status"] or "pending").strip().lower()
        if status_value == "completed":
            badge = '<span class="badge badge-success">completed</span>'
        elif status_value == "rejected":
            badge = '<span class="badge badge-accent">rejected</span>'
        elif status_value == "accepted":
            badge = '<span class="badge badge-neutral">accepted</span>'
        elif status_value == "pending":
            badge = '<span class="badge badge-warning">pending</span>'
        else:
            badge = f'<span class="badge badge-neutral">{escape(status_value or "other")}</span>'
        file_label = "Bor" if str(row["file_id"] or "").strip() else "Yo'q"
        rows_html.append(
            "<tr>"
            f"<td>{int(row['id'])}</td>"
            f"<td><strong>{escape(full_name)}</strong><br><span class=\"muted\">{escape(f'@{username}' if username else '-')}</span><br><span class=\"muted\">ID: {int(row['user_id'])}</span></td>"
            f"<td>{badge}</td>"
            f"<td>{escape(_short_text(str(row['text'] or '-')) or '-')}</td>"
            f"<td>{escape(file_label)}</td>"
            "</tr>"
        )

    body_html = _render_stats_strip(stats)
    body_html += _render_request_tabs(
        auth_query=auth_query,
        current_status=status_filter,
        current_q=search_text,
        counts=counts,
    )
    body_html += _render_search_form(
        action_path=f"{DETAIL_ROUTE_PREFIX}requests-overview",
        auth_query=auth_query,
        current_q=search_text,
        placeholder="Request ID yoki user ID bo'yicha qidiring",
        reset_href=_build_detail_href("requests-overview", auth_query, status=status_filter),
        hidden_pairs=[] if status_filter == "all" else [("status", status_filter)],
    )
    body_html += _render_table_card(
        ["Request ID", "Foydalanuvchi", "Status", "Matn", "Fayl"],
        rows_html,
        empty_message="Bu filtr bo'yicha request topilmadi.",
        min_width=860,
    )
    body_html += _render_pagination(
        "requests-overview",
        auth_query,
        page=int(page_data["page"]),
        total_pages=int(page_data["total_pages"]),
        q=search_text,
        status=status_filter,
    )
    return _build_detail_shell(
        title="So'rovlar overview",
        description="requests jadvalidagi admin/helperlardan tashqari user so'rovlari. Navbatda filtri pending va accepted statuslarini birga ko'rsatadi.",
        body_html=body_html,
        auth_query=auth_query,
    )


def _build_top_movies_detail_page(
    auth_query: list[tuple[str, str]],
    query: dict[str, list[str]],
) -> str:
    search_text = _normalize_search_text(_query_value(query, "q"))
    requested_page = _parse_page_number(_query_value(query, "page"))
    page_data = _query_top_movies_detail_page(search_text=search_text, page=requested_page)
    summary = _query_summary()

    stats = [
        ("Jami kino", _format_number(int(page_data["total_count"]))),
        ("Ko'rishlar jami", _format_number(summary["total_views"])),
        ("Unique user", "Takroriy ko'rishsiz"),
        ("Sahifa", f"{page_data['page']} / {page_data['total_pages']}"),
    ]
    if search_text:
        stats.append(("Qidiruv", search_text))

    rows_html = []
    rank_offset = (int(page_data["page"]) - 1) * DETAIL_PAGE_SIZE
    for index, row in enumerate(page_data["rows"], start=1):
        display_title = str(row["display_title"] or "Noma'lum kino")
        item_kind = str(row["item_kind"] or "movie")
        code_text = str(row["item_code"] or "-")
        if item_kind == "serial":
            title_html = (
                f'<a class="inline-link" href="{escape(_build_detail_href("serial-episodes", auth_query, code=code_text), quote=True)}">'
                f"{escape(display_title)}</a>"
            )
            kind_badge = '<span class="badge badge-neutral">Serial fasli</span>'
        else:
            title_html = escape(display_title)
            kind_badge = '<span class="badge badge-success">Kino</span>'
        rows_html.append(
            "<tr>"
            f"<td>{rank_offset + index}</td>"
            f"<td>{escape(code_text)}</td>"
            f"<td>{title_html}<br>{kind_badge}</td>"
            f"<td>{_format_number(int(row['views'] or 0))}</td>"
            f"<td>{_format_number(int(row['unique_views'] or 0))}</td>"
            f"<td>{escape(_format_detail_timestamp(row['last_viewed_at']))}</td>"
            "</tr>"
        )

    body_html = _render_stats_strip(stats)
    body_html += _render_search_form(
        action_path=f"{DETAIL_ROUTE_PREFIX}top-movies",
        auth_query=auth_query,
        current_q=search_text,
        placeholder="Kod yoki nom bo'yicha qidiring",
        reset_href=_build_detail_href("top-movies", auth_query),
    )
    body_html += _render_table_card(
        ["#", "Kod", "Nomi", "Ko'rishlar", "Unique user", "Oxirgi ko'rilgan"],
        rows_html,
        empty_message="Bu filtr bo'yicha kino topilmadi.",
        min_width=860,
    )
    body_html += _render_pagination(
        "top-movies",
        auth_query,
        page=int(page_data["page"]),
        total_pages=int(page_data["total_pages"]),
        q=search_text,
    )
    return _build_detail_shell(
        title="Top kinolar",
        description="movie_views jadvalidagi kontent reytingi. Ko'rishlar jami ochilishlar, Unique user esa takroriy ko'rishsiz userlar soni.",
        body_html=body_html,
        auth_query=auth_query,
    )


def _build_serial_episodes_detail_page(
    auth_query: list[tuple[str, str]],
    query: dict[str, list[str]],
) -> str | None:
    group_code = _query_value(query, "code")
    requested_page = _parse_page_number(_query_value(query, "page"))
    page_data = _query_serial_episodes_detail_page(group_code=group_code, page=requested_page)
    if page_data is None:
        return None

    group = page_data["group"]
    rows_html = []
    for row in page_data["rows"]:
        episode_number = int(row["episode_number"] or 0)
        label = f"{episode_number}-qism" if episode_number > 0 else str(row["display_title"] or "-")
        rows_html.append(
            "<tr>"
            f"<td>{escape(str(row['code'] or '-'))}</td>"
            f"<td>{escape(label)}</td>"
            f"<td>{escape(str(row['display_title'] or '-'))}</td>"
            f"<td>{_format_number(int(row['views'] or 0))}</td>"
            f"<td>{_format_number(int(row['unique_views'] or 0))}</td>"
            f"<td>{escape(_format_detail_timestamp(row['last_viewed_at']))}</td>"
            "</tr>"
        )

    body_html = _render_stats_strip(
        [
            ("Qismlar", _format_number(int(page_data["total_count"]))),
            ("Sahifa", f"{page_data['page']} / {page_data['total_pages']}"),
        ]
    )
    body_html += _render_table_card(
        ["Kod", "Qism", "Nomi", "Ko'rishlar", "Unique user", "Oxirgi ko'rilgan"],
        rows_html,
        empty_message="Bu serial uchun qism statistikasi topilmadi.",
        min_width=860,
    )
    body_html += _render_pagination(
        "serial-episodes",
        auth_query,
        page=int(page_data["page"]),
        total_pages=int(page_data["total_pages"]),
        q="",
    ).replace("serial-episodes?", f"serial-episodes?code={quote(group_code, safe='')}&", 1)
    return _build_detail_shell(
        title=f"{group['title']} qismlari",
        description="Serial fasli ichidagi qismlar ko'rishlar bo'yicha tartibli ro'yxat.",
        body_html=body_html,
        auth_query=auth_query,
    )


def _build_content_activity_day_page(
    auth_query: list[tuple[str, str]],
    query: dict[str, list[str]],
) -> str:
    day = _query_value(query, "day") or local_day_keys(1)[0]
    requested_page = _parse_page_number(_query_value(query, "page"))
    page_data = _query_content_activity_day_page(day=day, page=requested_page)

    rows_html = []
    for row in page_data["rows"]:
        kind = str(row["content_kind"] or "movie")
        badge = '<span class="badge badge-neutral">Serial</span>' if kind == "serial" else '<span class="badge badge-success">Kino</span>'
        rows_html.append(
            "<tr>"
            f"<td>{escape(str(row['code'] or '-'))}</td>"
            f"<td>{escape(str(row['display_title'] or '-'))}<br>{badge}</td>"
            f"<td>{_format_number(int(row['views'] or 0))}</td>"
            f"<td>{_format_number(int(row['unique_users'] or 0))}</td>"
            f"<td>{escape(_format_detail_timestamp(row['last_viewed_at']))}</td>"
            "</tr>"
        )

    note = ""
    if not page_data.get("has_events"):
        note = (
            '<div class="card note-card"><p>Kun-kod kesimidagi detail statistika yangi tracking yoqilgandan keyin yig&apos;iladi. '
            'Eski kunlar uchun faqat umumiy daily_stats sonlari mavjud.</p></div>'
        )
    body_html = _render_stats_strip(
        [
            ("Sana", _format_date(day)),
            ("Kontentlar", _format_number(int(page_data["total_count"]))),
            ("Sahifa", f"{page_data['page']} / {page_data['total_pages']}"),
        ]
    )
    body_html += note
    body_html += _render_table_card(
        ["Kod", "Kontent", "Ko'rishlar", "Unique user", "Oxirgi ko'rilgan"],
        rows_html,
        empty_message="Bu kun uchun kontent detail topilmadi.",
        min_width=820,
    )
    body_html += _render_pagination(
        "content-activity-day",
        auth_query,
        page=int(page_data["page"]),
        total_pages=int(page_data["total_pages"]),
        q="",
    ).replace("content-activity-day?", f"content-activity-day?day={quote(day, safe='')}&", 1)
    return _build_detail_shell(
        title=f"Kontent faolligi: {_format_date(day)}",
        description="Tanlangan kunda ko'rilgan kinolar va serial qismlari.",
        body_html=body_html,
        auth_query=auth_query,
    )


def _build_user_activity_detail_page(
    auth_query: list[tuple[str, str]],
    query: dict[str, list[str]],
) -> str | None:
    try:
        user_id = int(_query_value(query, "user_id"))
    except ValueError:
        return None

    requested_page = _parse_page_number(_query_value(query, "page"))
    page_data = _query_user_activity_detail_page(user_id=user_id, page=requested_page)
    if page_data is None:
        return None

    user = page_data["user"]
    rows_html = []
    for row in page_data["rows"]:
        event_type = str(row["event_type"] or "")
        if event_type == "view":
            detail = str(row["title"] or row["resolved_code"] or "-")
            badge = '<span class="badge badge-success">Ochdi</span>'
        else:
            raw_query = str(row["raw_query"] or row["normalized_code"] or "-")
            resolved = str(row["resolved_code"] or "")
            status = str(row["result_status"] or "")
            detail = f"{raw_query} -> {resolved}" if resolved else raw_query
            badge = f'<span class="badge badge-neutral">{escape(status or "search")}</span>'
            if status in {"not_found", "error"}:
                badge = f'<span class="badge badge-accent">{escape(status)}</span>'
        rows_html.append(
            "<tr>"
            f"<td>{escape(_format_detail_timestamp(row['event_at']))}</td>"
            f"<td>{badge}</td>"
            f"<td>{escape(detail)}</td>"
            f"<td>{escape(str(row['content_kind'] or '-'))}</td>"
            "</tr>"
        )

    username = str(user["username"] or "").strip()
    full_name = str(user["full_name"] or "").strip() or f"User {user_id}"
    body_html = _render_stats_strip(
        [
            ("User ID", str(user_id)),
            ("Eventlar", _format_number(int(page_data["total_count"]))),
            ("Birinchi kirgan", _format_detail_timestamp(user["first_seen"])),
            ("Sahifa", f"{page_data['page']} / {page_data['total_pages']}"),
        ]
    )
    body_html += _render_table_card(
        ["Vaqt", "Tur", "Qidiruv / kontent", "Kontent turi"],
        rows_html,
        empty_message="Bu user uchun activity log topilmadi.",
        min_width=760,
    )
    body_html += _render_pagination(
        "user-activity",
        auth_query,
        page=int(page_data["page"]),
        total_pages=int(page_data["total_pages"]),
        q="",
    ).replace("user-activity?", f"user-activity?user_id={user_id}&", 1)
    return _build_detail_shell(
        title=f"{full_name} activity",
        description=f"{'@' + username if username else 'Username yoq'} | botga kirgandan hozirgacha qidiruv urinishlari va ochilgan kontentlar.",
        body_html=body_html,
        auth_query=auth_query,
    )


def _build_aggregate_detail_page(
    *,
    title: str,
    description: str,
    values: list[int],
    labels: list[str],
    color_var: str,
    stats: list[tuple[str, str]],
    note: str,
    auth_query: list[tuple[str, str]],
    footer_html: str = "",
    chart_hrefs: list[str] | None = None,
) -> str:
    rows_html = [
        "<tr>"
        f"<td>{escape(label)}</td>"
        f"<td>{_format_number(value)}</td>"
        "</tr>"
        for label, value in zip(labels, values)
    ]
    body_html = _render_stats_strip(stats)
    body_html += f'<div class="card note-card"><p>{escape(note)}</p></div>'
    body_html += (
        '<div class="card">'
        f'<div class="chart">{_render_chart(values, labels, color_var, hrefs=chart_hrefs)}</div>'
        "</div>"
    )
    body_html += _render_table_card(
        ["Sana", "Qiymat"],
        rows_html,
        empty_message="Bu davr uchun agregat ma'lumot topilmadi.",
        min_width=520,
    )
    body_html += footer_html
    return _build_detail_shell(
        title=title,
        description=description,
        body_html=body_html,
        auth_query=auth_query,
    )


def _build_detail_page(metric_key: str, query: dict[str, list[str]]) -> str | None:
    auth_query = _auth_query_pairs(query)
    user_configs = {
        "active-subscribers": {
            "title": "Faol obunachilar",
            "description": "users jadvalidagi hozir botni bloklamagan, admin/helper bo'lmagan userlar.",
            "include_blocked": False,
            "blocked_only": False,
            "last_seen_since": None,
            "first_seen_since": None,
            "order_by": "COALESCE(last_seen, '') DESC, user_id DESC",
        },
        "all-users": {
            "title": "Jami kirganlar",
            "description": "users jadvalidagi admin/helperlardan tashqari barcha userlar, bloklaganlar ham kiradi.",
            "include_blocked": True,
            "blocked_only": False,
            "last_seen_since": None,
            "first_seen_since": None,
            "order_by": "COALESCE(last_seen, '') DESC, user_id DESC",
        },
        "entered-today": {
            "title": "Bugun kirganlar",
            "description": "Bugun oxirgi faolligi qayd etilgan userlar. Bloklagan userlar ham ko'rsatiladi.",
            "include_blocked": True,
            "blocked_only": False,
            "last_seen_since": _db_timestamp(local_day_start_utc()),
            "first_seen_since": None,
            "order_by": "COALESCE(last_seen, '') DESC, user_id DESC",
        },
        "new-users-today": {
            "title": "Bugun yangi obunachi",
            "description": "first_seen bugungi lokal kunga tushgan, admin/helper bo'lmagan userlar.",
            "include_blocked": True,
            "blocked_only": False,
            "last_seen_since": None,
            "first_seen_since": _db_timestamp(local_day_start_utc()),
            "order_by": "COALESCE(first_seen, '') DESC, COALESCE(last_seen, '') DESC, user_id DESC",
        },
        "blocked-users": {
            "title": "Bloklaganlar",
            "description": "users jadvalidagi hozir botni bloklagan userlar.",
            "include_blocked": True,
            "blocked_only": True,
            "last_seen_since": None,
            "first_seen_since": None,
            "order_by": "COALESCE(blocked_at, last_seen, '') DESC, user_id DESC",
        },
        "active-24h": {
            "title": "24 soat faol",
            "description": "Oxirgi 24 soatda last_seen yangilangan, botni bloklamagan userlar.",
            "include_blocked": False,
            "blocked_only": False,
            "last_seen_since": _db_timestamp(datetime.now(UTC) - timedelta(days=1)),
            "first_seen_since": None,
            "order_by": "COALESCE(last_seen, '') DESC, user_id DESC",
            "note": "Asosiy ro'yxat so'nggi 24 soat bo'yicha. 7 kun faol soni faqat solishtirish uchun ko'rsatiladi.",
        },
        "recent-users": {
            "title": "Yaqinda faol bo'lganlar",
            "description": "Oxirgi 7 kun ichida last_seen yangilangan, botni bloklamagan userlar.",
            "include_blocked": False,
            "blocked_only": False,
            "last_seen_since": _db_timestamp(local_day_start_utc(6)),
            "first_seen_since": None,
            "order_by": "COALESCE(last_seen, '') DESC, user_id DESC",
        },
    }
    if metric_key in user_configs:
        return _build_user_detail_page(metric_key, auth_query, query, user_configs[metric_key])

    if metric_key == "requests-overview":
        return _build_requests_overview_page(auth_query, query)

    if metric_key == "top-movies":
        return _build_top_movies_detail_page(auth_query, query)

    if metric_key == "serial-episodes":
        return _build_serial_episodes_detail_page(auth_query, query)

    if metric_key == "content-activity-day":
        return _build_content_activity_day_page(auth_query, query)

    if metric_key == "user-activity":
        return _build_user_activity_detail_page(auth_query, query)

    if metric_key == "requests-7d":
        daily = _query_daily_series(7)
        summary = _query_summary()
        values = daily["requests"]
        footer_html = (
            '<div class="card note-card" style="margin-top:18px;">'
            f'<a class="inline-link" href="{escape(_build_detail_href("requests-overview", auth_query), quote=True)}">Requestlar ro&apos;yxatini ochish</a>'
            "</div>"
        )
        return _build_aggregate_detail_page(
            title="Ohirgi 7 kun murojaatlar",
            description="daily_stats jadvalidagi requests agregatlari bo'yicha oxirgi 7 kun.",
            values=values,
            labels=daily["labels"],
            color_var="var(--primary)",
            stats=[
                ("7 kun jami", _format_number(sum(values))),
                ("Bugun", _format_number(values[-1] if values else 0)),
                ("Jami tarixiy request", _format_number(summary["total_requests"])),
            ],
            note="Bu sahifada individual requestlar emas, event vaqtida yozilgan kunlik agregatlar ko'rsatiladi.",
            auth_query=auth_query,
            footer_html=footer_html,
        )

    if metric_key == "content-activity-7d":
        daily = _query_daily_series(7)
        summary = _query_summary()
        values = daily["movie_views"]
        day_hrefs = [
            _build_detail_href("content-activity-day", auth_query, day=label)
            for label in daily["labels"]
        ]
        footer_html = (
            '<div class="card note-card" style="margin-top:18px;">'
            f'<a class="inline-link" href="{escape(_build_detail_href("top-movies", auth_query), quote=True)}">Top kinolar detail sahifasiga o&apos;tish</a>'
            "</div>"
        )
        return _build_aggregate_detail_page(
            title="Kontent faolligi 7 kun",
            description="daily_stats jadvalidagi movie_views agregatlari bo'yicha oxirgi 7 kun.",
            values=values,
            labels=daily["labels"],
            color_var="var(--secondary)",
            chart_hrefs=day_hrefs,
            stats=[
                ("7 kun jami", _format_number(sum(values))),
                ("Bugun", _format_number(values[-1] if values else 0)),
                ("Jami ko'rishlar", _format_number(summary["total_views"])),
            ],
            note="Kunlik ko'rishlar event vaqtida daily_stats ga yoziladi; user kesimida tekshirish uchun Top kinolar sahifasidagi Unique user ustunidan foydalaning.",
            auth_query=auth_query,
            footer_html=footer_html,
        )

    return None


def _build_dashboard_page(
    auth_query: list[tuple[str, str]],
    payload: dict[str, object] | None = None,
) -> str:
    dashboard_payload = payload or _query_dashboard_payload()
    summary = dashboard_payload["summary"]
    daily = dashboard_payload["daily"]
    top_movies = dashboard_payload["top_movies"]
    recent_users = dashboard_payload["recent_users"]
    updated_at = str(dashboard_payload["updated_at"])

    active_subscribers_href = escape(_build_detail_href("active-subscribers", auth_query), quote=True)
    all_users_href = escape(_build_detail_href("all-users", auth_query), quote=True)
    entered_today_href = escape(_build_detail_href("entered-today", auth_query), quote=True)
    new_users_today_href = escape(_build_detail_href("new-users-today", auth_query), quote=True)
    blocked_users_href = escape(_build_detail_href("blocked-users", auth_query), quote=True)
    active_today_href = escape(_build_detail_href("active-24h", auth_query), quote=True)
    requests_overview_href = escape(_build_detail_href("requests-overview", auth_query), quote=True)
    requests_7d_href = escape(_build_detail_href("requests-7d", auth_query), quote=True)
    content_activity_7d_href = escape(_build_detail_href("content-activity-7d", auth_query), quote=True)
    top_movies_href = escape(_build_detail_href("top-movies", auth_query), quote=True)
    recent_users_href = escape(_build_detail_href("recent-users", auth_query), quote=True)

    pipeline = _request_pipeline_counts(summary)
    completed_pct = pipeline["completed_pct"]
    pending_pct = pipeline["pending_pct"]
    rejected_pct = pipeline["rejected_pct"]
    other_requests = pipeline["other"]
    other_status_note = (
        f'<div class="text-sm text-dim" style="margin-top:12px;">Boshqa statuslar pipeline foiziga kiritilmadi: {_format_number(other_requests)} ta.</div>'
        if other_requests
        else ""
    )

    return """<!DOCTYPE html>
<html lang="uz">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
    <title>Analytiq Dashboard</title>
    <link href="https://fonts.googleapis.com/css2?family=Outfit:wght@300;400;500;600;700&display=swap" rel="stylesheet">
    <style>
        :root {{
            --bg: #090e17;
            --surface: #121826;
            --surface-hover: #1a2235;
            --primary: #38bdf8;
            --secondary: #818cf8;
            --accent: #f43f5e;
            --success: #10b981;
            --warning: #f59e0b;
            --text-main: #f8fafc;
            --text-muted: #94a3b8;
            --border: #1e293b;
        }}
        * {{ margin: 0; padding: 0; box-sizing: border-box; font-family: 'Outfit', sans-serif; }}
        body {{ background-color: var(--bg); color: var(--text-main); font-size: 15px; -webkit-font-smoothing: antialiased; padding-bottom: 40px; }}
        .container {{ max-width: 1100px; margin: 0 auto; padding: 20px; }}
        h1 {{ font-size: 24px; font-weight: 600; margin-bottom: 4px; }}
        h2 {{ font-size: 18px; font-weight: 500; color: var(--text-main); margin-bottom: 20px; display: flex; align-items: center; gap: 8px; }}
        .text-dim {{ color: var(--text-muted); }}
        .text-sm {{ font-size: 13px; }}
        .flex {{ display: flex; }}
        .items-center {{ align-items: center; }}
        .justify-between {{ justify-content: space-between; }}
        .grid {{ display: grid; gap: 20px; }}
        .grid-cols-2 {{ grid-template-columns: repeat(auto-fit, minmax(320px, 1fr)); }}
        .grid-cols-4 {{ grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); }}
        .card {{ background: var(--surface); border: 1px solid var(--border); border-radius: 16px; padding: 20px; transition: transform 0.2s; }}
        .card:hover {{ border-color: rgba(56, 189, 248, 0.4); }}
        .panel-link {{ display: block; color: inherit; text-decoration: none; }}
        .panel-link:hover {{ background: var(--surface-hover); transform: translateY(-1px); }}
        .metric-box {{ text-align: center; padding: 24px 10px; background: linear-gradient(180deg, rgba(255,255,255,0.03) 0%, transparent 100%); border-radius: 12px; }}
        .metric-title {{ font-size: 14px; font-weight: 500; color: var(--text-muted); margin-bottom: 12px; text-transform: uppercase; letter-spacing: 0.5px; }}
        .metric-val {{ font-size: 42px; font-weight: 700; line-height: 1; margin-bottom: 8px; }}
        .metric-sub {{ font-size: 13px; font-weight: 500; line-height: 1.45; }}
        .metric-sub + .metric-sub {{ margin-top: 4px; }}
        .c-primary {{ color: var(--primary); }}
        .c-success {{ color: var(--success); }}
        .c-warning {{ color: var(--warning); }}
        .c-accent {{ color: var(--accent); }}
        .bg-success-light {{ background: rgba(16, 185, 129, 0.15); color: var(--success); padding: 4px 8px; border-radius: 6px; font-weight: 600; display: inline-block; }}
        .status-pipeline {{ display: flex; height: 12px; border-radius: 6px; overflow: hidden; margin-top: 15px; background: rgba(255,255,255,0.05); }}
        .pipeline-item {{ height: 100%; transition: width 1s ease-out; animation: fillWidth 1.5s ease-out forwards; }}
        .legend {{ display: flex; justify-content: space-between; margin-top: 16px; font-size: 13px; font-weight: 500; flex-wrap: wrap; gap: 10px; }}
        .legend > div {{ display: flex; align-items: center; gap: 6px; }}
        .dot {{ width: 10px; height: 10px; border-radius: 50%; }}
        .list-item {{ display: flex; justify-content: space-between; align-items: center; padding: 12px 0; border-bottom: 1px solid rgba(255,255,255,0.05); }}
        .list-item:last-child {{ border-bottom: none; }}
        .rank-badge {{ width: 28px; height: 28px; border-radius: 8px; background: rgba(255,255,255,0.1); display: flex; align-items: center; justify-content: center; font-weight: 600; font-size: 13px; margin-right: 12px; flex-shrink: 0; }}
        .rank-1 {{ background: rgba(245, 158, 11, 0.2); color: var(--warning); }}
        .rank-2 {{ background: rgba(148, 163, 184, 0.2); color: #cbd5e1; }}
        .rank-3 {{ background: rgba(217, 119, 6, 0.2); color: #b45309; }}
        .rank-other {{ background: rgba(255,255,255,0.05); color: var(--text-muted); }}
        .progress-bg {{ height: 6px; background: rgba(255,255,255,0.1); border-radius: 3px; width: 80px; overflow: hidden; display:inline-block; vertical-align: middle; }}
        .progress-fill {{ height: 100%; background: var(--primary); border-radius: 3px; animation: fillWidth 1s ease-out forwards; }}
        .chart {{ display: flex; align-items: flex-end; gap: 8px; height: 180px; margin-top: 20px; }}
        .chart-col {{ flex: 1; min-width: 0; height: 100%; display: flex; flex-direction: column; justify-content: flex-end; align-items: center; gap: 8px; }}
        .chart-value {{ min-height: 14px; font-size: 11px; color: var(--text-muted); line-height: 1; }}
        .bar-wrap {{ width: 100%; height: 120px; flex: none; background: rgba(255,255,255,0.03); border-radius: 4px; display: flex; align-items: flex-end; overflow: hidden; }}
        .bar-wrap:hover .bar {{ filter: brightness(1.2); }}
        .bar {{ width: 100%; display: block; border-radius: 4px 4px 0 0; animation: fillBar 1s ease-out forwards; transform-origin: bottom; }}
        .chart-label {{ font-size: 11px; color: var(--text-muted); }}
        .live-status {{ display: inline-flex; align-items: center; gap: 6px; background: rgba(16, 185, 129, 0.15); border: 1px solid rgba(16, 185, 129, 0.3); padding: 4px 10px; border-radius: 50px; color: var(--success); font-size: 12px; font-weight: 600; letter-spacing: 0.5px; }}
        @keyframes pulse-dot {{ 0% {{ opacity: 1; }} 50% {{ opacity: 0.4; }} 100% {{ opacity: 1; }} }}
        .dot-blink {{ width: 6px; height: 6px; background: var(--success); border-radius: 50%; animation: pulse-dot 1.5s infinite; }}
        .fade-in {{ animation: fadeIn 0.4s ease-out forwards; opacity: 0; }}
        @keyframes fadeIn {{ from{{ opacity:0; transform:translateY(10px); }} to{{ opacity:1; transform:translateY(0); }} }}
        @keyframes fillBar {{ from {{ transform: scaleY(0); }} to {{ transform: scaleY(1); }} }}
        @keyframes fillWidth {{ from {{ width: 0; }} }}
    </style>
</head>
<body style="background-color:#090e17;color:#f8fafc;">
    <div class="container">
        <header class="flex justify-between items-center fade-in" style="margin-bottom: 30px;">
            <div>
                <h1>Admin Analytiq</h1>
                <p class="text-dim text-sm">Bot va tizim ma'lumotlari markazi</p>
            </div>
            <div class="live-status"><div class="dot-blink"></div>JONLI</div>
        </header>
        <div class="grid grid-cols-4 fade-in" style="animation-delay: 0.1s; margin-bottom: 24px;">
            <a class="card metric-box panel-link" href="{active_subscribers_href}">
                <div class="metric-title">Faol Obunachilar</div>
                <div class="metric-val c-primary">{total_users}</div>
                <div class="metric-sub text-dim">Hozir botni bloklamagan userlar</div>
            </a>
            <a class="card metric-box panel-link" href="{all_users_href}">
                <div class="metric-title">Jami Kirganlar</div>
                <div class="metric-val">{all_time_users}</div>
                <div class="metric-sub text-dim">Admin/helperlardan tashqari barcha userlar</div>
            </a>
            <a class="card metric-box panel-link" href="{entered_today_href}">
                <div class="metric-title">Bugun Kirganlar</div>
                <div class="metric-val c-success">{entered_today}</div>
                <div class="metric-sub text-dim">Bugun oxirgi faolligi bor userlar</div>
            </a>
            <a class="card metric-box panel-link" href="{new_users_today_href}">
                <div class="metric-title">Bugun Yangi Obunachi</div>
                <div class="metric-val c-primary">{new_subscribers_today}</div>
                <div class="metric-sub text-dim">Bugun birinchi marta kirgan foydalanuvchilar</div>
            </a>
            <a class="card metric-box panel-link" href="{blocked_users_href}" style="border-color: rgba(244, 63, 94, 0.3);">
                <div class="metric-title" style="color: var(--accent)">Bloklaganlar</div>
                <div class="metric-val c-accent">{blocked_users}</div>
                <div class="metric-sub text-dim">Hozir blok holatida turgan userlar</div>
            </a>
            <a class="card metric-box panel-link" href="{active_today_href}" style="border-color: rgba(245, 158, 11, 0.3);">
                <div class="metric-title" style="color: var(--warning)">24 Soat Faol</div>
                <div class="metric-val c-warning">{active_today}</div>
                <div class="metric-sub text-dim">So'nggi 24 soat, bloklamaganlar<br>7 kun faol: <span style="color:var(--text-main)">{active_week}</span></div>
            </a>
        </div>
        <a class="card panel-link fade-in" href="{requests_overview_href}" style="animation-delay: 0.2s; margin-bottom: 24px;">
            <h2>So'rovlar pipeline</h2>
            <div class="flex justify-between items-center text-sm">
                <span class="text-dim">Jami kelib tushgan so'rovlar: <strong style="color:var(--text-main); font-size:16px;">{total_requests}</strong> ta</span>
            </div>
            <div class="status-pipeline">
                <div class="pipeline-item" style="background: var(--success); width: {completed_pct}%;"></div>
                <div class="pipeline-item" style="background: var(--warning); width: {pending_pct}%;"></div>
                <div class="pipeline-item" style="background: var(--accent); width: {rejected_pct}%;"></div>
            </div>
            <div class="legend">
                <div><div class="dot" style="background: var(--success);"></div> {completed_requests} Bajarilgan ({completed_pct}%)</div>
                <div><div class="dot" style="background: var(--warning);"></div> {pending_requests} Navbatda ({pending_pct}%)</div>
                <div><div class="dot" style="background: var(--accent);"></div> {rejected_requests} Rad etilgan ({rejected_pct}%)</div>
            </div>
            {other_status_note}
        </a>
        <div class="grid grid-cols-2">
            <a class="card panel-link fade-in" href="{requests_7d_href}" style="animation-delay: 0.3s;">
                <h2>Oxirgi 7 kun murojaatlar</h2>
                <div class="text-sm text-dim">daily_stats jadvalidagi requests agregati</div>
                <div class="chart">
                    {requests_bars}
                </div>
            </a>
            <a class="card panel-link fade-in" href="{content_activity_7d_href}" style="animation-delay: 0.4s;">
                <h2>Kontent faolligi</h2>
                <div class="text-sm text-dim">daily_stats jadvalidagi movie_views agregati</div>
                <div class="chart">
                    {views_bars}
                </div>
            </a>
            <a class="card panel-link fade-in" href="{top_movies_href}" style="animation-delay: 0.5s;">
                <h2>Top kinolar</h2>
                <div>
                    {top_movies_html}
                </div>
            </a>
            <a class="card panel-link fade-in" href="{recent_users_href}" style="animation-delay: 0.6s;">
                <h2>Yaqinda faol bo'lganlar</h2>
                <div>
                    {recent_users_html}
                </div>
            </a>
        </div>
        <div class="text-sm text-dim flex justify-between" style="margin-top: 30px;">
            <span>Yaratildi: PrimeCinema Tech</span>
            <span>So'nggi yangilanish: {updated_at}</span>
        </div>
    </div>
</body>
</html>""".format(
        updated_at=updated_at,
        total_users=_format_number(summary["total_users"]),
        all_time_users=_format_number(summary["all_time_users"]),
        entered_today=_format_number(summary["entered_today"]),
        new_subscribers_today=_format_number(summary["new_subscribers_today"]),
        blocked_users=_format_number(summary["blocked_users"]),
        active_today=_format_number(summary["active_today"]),
        active_week=_format_number(summary["active_week"]),
        total_requests=_format_number(summary["total_requests"]),
        pending_requests=_format_number(summary["pending_requests"]),
        completed_requests=_format_number(summary["completed_requests"]),
        rejected_requests=_format_number(summary["rejected_requests"]),
        other_status_note=other_status_note,
        completed_pct=completed_pct,
        pending_pct=pending_pct,
        rejected_pct=rejected_pct,
        active_subscribers_href=active_subscribers_href,
        all_users_href=all_users_href,
        entered_today_href=entered_today_href,
        new_users_today_href=new_users_today_href,
        blocked_users_href=blocked_users_href,
        active_today_href=active_today_href,
        requests_overview_href=requests_overview_href,
        requests_7d_href=requests_7d_href,
        content_activity_7d_href=content_activity_7d_href,
        top_movies_href=top_movies_href,
        recent_users_href=recent_users_href,
        requests_bars=_render_chart(daily["requests"], daily["labels"], "var(--primary)"),
        views_bars=_render_chart(daily["movie_views"], daily["labels"], "var(--secondary)"),
        top_movies_html=_render_top_movies(top_movies),
        recent_users_html=_render_recent_users(recent_users),
    )


_cache = {"payload": None, "timestamp": 0.0}
CACHE_TTL = 30


class SimpleHandler(BaseHTTPRequestHandler):
    server_version = "Cinema"
    sys_version = ""

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)
        if parsed.path.startswith(ACTION_ROUTE_PREFIX):
            if not _stats_request_is_authorized(self, query):
                _send_bytes(
                    self,
                    403,
                    _unauthorized_stats_page(),
                    content_type="text/html; charset=utf-8",
                )
                return

            action_key = unquote(parsed.path[len(ACTION_ROUTE_PREFIX) :]).strip().strip("/")
            auth_query = _auth_query_pairs(query)
            if action_key == "delete-blocked-users":
                if DB_PATH.exists():
                    with sqlite3.connect(DB_PATH, timeout=5, check_same_thread=False) as conn:
                        conn.execute("DELETE FROM users WHERE COALESCE(is_blocked, 0) = 1")
                        conn.commit()
                _cache["payload"] = None
                _redirect(self, _build_detail_href("blocked-users", auth_query))
                return

            self.send_error(404, "Not Found")
            return

        if parsed.path != "/auth-bootstrap":
            self.send_error(404, "Not Found")
            return

        content_type = (
            (self.headers.get("Content-Type") or "").split(";", 1)[0].strip().lower()
        )
        if content_type and content_type != "application/json":
            _send_json(self, 415, {"error": "Faqat JSON so'rov qabul qilinadi."})
            return

        try:
            content_length = int((self.headers.get("Content-Length") or "0").strip())
        except ValueError:
            _send_json(self, 400, {"error": "So'rov uzunligi noto'g'ri."})
            return

        if content_length <= 0 or content_length > 32_768:
            _send_json(self, 400, {"error": "So'rov formati noto'g'ri."})
            return

        raw_body = self.rfile.read(content_length)
        try:
            payload = json.loads(raw_body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            _send_json(self, 400, {"error": "JSON formati noto'g'ri."})
            return

        if not isinstance(payload, dict):
            _send_json(self, 400, {"error": "So'rov tanasi noto'g'ri."})
            return

        verified_init_data = verify_telegram_webapp_init_data(
            str(payload.get("initData") or "")
        )
        if not verified_init_data:
            _send_json(
                self,
                403,
                {"error": "Telegram ruxsati tasdiqlanmadi. Statistika tugmasini qayta bosing."},
            )
            return

        user_id = int(verified_init_data.get("user_id") or 0)
        if not _is_stats_operator(user_id):
            _send_json(
                self,
                403,
                {"error": "Sizda statistika panelini ochish ruxsati yo'q."},
            )
            return

        signed_url = build_signed_stats_webapp_url(
            _stats_base_url_for_request(self),
            user_id,
        )
        _send_json(self, 200, {"ok": True, "url": signed_url})

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        query = parse_qs(parsed.query)

        if path.startswith(SHARE_MEDIA_ROUTE_PREFIX):
            code = unquote(path[len(SHARE_MEDIA_ROUTE_PREFIX) :]).strip().strip("/")
            if not code:
                self.send_error(404, "Not Found")
                return
            if not _share_request_is_authorized(code, query, media=True):
                _send_bytes(
                    self,
                    403,
                    _unauthorized_share_page(),
                    content_type="text/html; charset=utf-8",
                )
                return
            _stream_share_media(self, code)
            return

        if path.startswith("/share/"):
            code = unquote(path[len("/share/") :]).strip().strip("/")
            if not code:
                self.send_error(404, "Not Found")
                return
            if not _share_request_is_authorized(code, query):
                _send_bytes(
                    self,
                    403,
                    _unauthorized_share_page(),
                    content_type="text/html; charset=utf-8",
                )
                return

            target = _safe_target_url((query.get("target") or [""])[0])
            public_base = urlsplit(_stats_base_url_for_request(self))
            host = public_base.netloc or _request_host(self, fallback="localhost")
            scheme = public_base.scheme or _request_scheme(self)
            has_preview_media = _resolve_share_media_source(code) is not None
            media_url = None
            if has_preview_media:
                media_signature = build_signed_share_query(code, media=True)
                if media_signature:
                    media_url = (
                        f"{scheme}://{host}{SHARE_MEDIA_ROUTE_PREFIX}"
                        f"{quote(code, safe='')}?{urlencode(media_signature)}"
                    )
            body = _build_share_page(
                code=code,
                request_host=host,
                request_path=path,
                target_url=target,
                media_url=media_url,
            )
            _send_bytes(self, 200, body, content_type="text/html; charset=utf-8")
            return

        if path.startswith(DETAIL_ROUTE_PREFIX):
            if not _stats_request_is_authorized(self, query):
                body = (
                    _unauthorized_stats_page()
                    if _is_local_stats_request(self)
                    else _stats_auth_bootstrap_page()
                )
                _send_bytes(
                    self,
                    403 if _is_local_stats_request(self) else 200,
                    body,
                    content_type="text/html; charset=utf-8",
                )
                return

            metric_key = unquote(path[len(DETAIL_ROUTE_PREFIX) :]).strip().strip("/")
            body_text = _build_detail_page(metric_key, query)
            if body_text is None:
                self.send_error(404, "Not Found")
                return
            _send_bytes(
                self,
                200,
                body_text.encode("utf-8"),
                content_type="text/html; charset=utf-8",
            )
            return

        if path not in {"/", "/index.html"}:
            self.send_error(404, "Not Found")
            return

        if not _stats_request_is_authorized(self, query):
            body = (
                _unauthorized_stats_page()
                if _is_local_stats_request(self)
                else _stats_auth_bootstrap_page()
            )
            _send_bytes(
                self,
                403 if _is_local_stats_request(self) else 200,
                body,
                content_type="text/html; charset=utf-8",
            )
            return

        global _cache
        current_time = time.time()
        payload = _cache.get("payload")
        if payload is None or current_time - _cache["timestamp"] > CACHE_TTL:
            payload = _query_dashboard_payload()
            _cache["payload"] = payload
            _cache["timestamp"] = current_time

        body = _build_dashboard_page(_auth_query_pairs(query), payload).encode("utf-8")
        _send_bytes(self, 200, body, content_type="text/html; charset=utf-8")

    def log_message(self, format: str, *args: object) -> None:
        return


if __name__ == "__main__":
    pid_path = Path(__file__).resolve().with_name(".webapp.pid")
    server_address = ("0.0.0.0", PORT)
    try:
        httpd = ReusableHTTPServer(server_address, SimpleHandler)
    except OSError as exc:
        print(
            f"Web app porti band yoki serverni ochib bo'lmadi ({server_address[1]}): {exc}",
            file=sys.stderr,
        )
        raise SystemExit(1) from exc

    try:
        pid_path.write_text(str(os.getpid()), encoding="utf-8")
        print(
            f"Web app server running on http://{server_address[0]}:{server_address[1]}"
        )
        print(
            "Expose this address with a tunnel and use the HTTPS URL as STATS_WEBAPP_URL."
        )
        httpd.serve_forever()
    finally:
        with suppress(OSError):
            httpd.server_close()
        with suppress(OSError):
            pid_path.unlink()
