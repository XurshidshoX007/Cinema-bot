from contextlib import suppress
from datetime import datetime, timedelta, UTC
from html import escape
from http.server import HTTPServer, BaseHTTPRequestHandler
import ipaddress
import json
from pathlib import Path
import os
import re
import sqlite3
import time
from urllib.parse import parse_qs, quote, unquote, urlencode, urlparse, urlsplit
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

# Register datetime adapter for sqlite3
sqlite3.register_adapter(datetime, lambda dt: dt.isoformat())
sqlite3.register_converter("timestamp", lambda s: datetime.fromisoformat(s.decode()))

from config import ADMIN_ID, BOT_TOKEN, get_stats_webapp_url
from database import (
    APP_TIMEZONE_LABEL,
    DB_PATH,
    format_local_timestamp,
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
_REQUEST_HOST_RE = re.compile(r"^\[?[A-Za-z0-9:.%-]+\]?(?::\d{1,5})?$")


class ReusableHTTPServer(HTTPServer):
    allow_reuse_address = True


def _format_number(value: int) -> str:
    return f"{value:,}".replace(",", " ")


def _format_date(date_value: str) -> str:
    try:
        return datetime.fromisoformat(date_value).strftime("%d.%m.%Y")
    except ValueError:
        return date_value


def _db_timestamp(value: datetime) -> str:
    return value.replace(tzinfo=None, microsecond=0).strftime("%Y-%m-%d %H:%M:%S")


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


def _query_summary() -> dict[str, int]:
    default_summary = {
        "total_users": 0,
        "all_time_users": 0,
        "entered_today": 0,
        "new_subscribers_today": 0,
        "blocked_users": 0,
        "joined_today": 0,
        "active_today": 0,
        "active_week": 0,
        "total_movies": 0,
        "total_views": 0,
        "total_requests": 0,
        "pending_requests": 0,
        "completed_requests": 0,
        "rejected_requests": 0,
    }
    if not DB_PATH.exists():
        return default_summary

    today_start = _db_timestamp(local_day_start_utc())
    today_threshold = _db_timestamp(datetime.now(UTC) - timedelta(days=1))
    week_threshold = _db_timestamp(local_day_start_utc(6))

    with sqlite3.connect(DB_PATH, timeout=5, check_same_thread=False) as conn:
        conn.row_factory = sqlite3.Row
        summary = default_summary.copy()
        blocked_filter = _users_block_filter(conn)

        cur = conn.execute(
            "SELECT COUNT(*) as all_time_users, "
            "SUM(CASE WHEN last_seen >= ? THEN 1 ELSE 0 END) as entered_today "
            "FROM users "
            "WHERE user_id != ? "
            "AND user_id NOT IN (SELECT user_id FROM helper_admins)",
            (today_start, ADMIN_ID),
        )
        row = cur.fetchone()
        if row:
            summary["all_time_users"] = int(row["all_time_users"] or 0)
            summary["entered_today"] = int(row["entered_today"] or 0)

        cur = conn.execute(
            "SELECT COUNT(*) as new_subscribers_today "
            "FROM users "
            "WHERE first_seen >= ? "
            "AND user_id != ? "
            "AND user_id NOT IN (SELECT user_id FROM helper_admins)",
            (today_start, ADMIN_ID),
        )
        row = cur.fetchone()
        if row:
            summary["new_subscribers_today"] = int(row["new_subscribers_today"] or 0)

        if blocked_filter:
            cur = conn.execute(
                "SELECT COUNT(*) as blocked_users "
                "FROM users "
                "WHERE COALESCE(is_blocked, 0) = 1 "
                "AND user_id != ? "
                "AND user_id NOT IN (SELECT user_id FROM helper_admins)",
                (ADMIN_ID,),
            )
            row = cur.fetchone()
            if row:
                summary["blocked_users"] = int(row["blocked_users"] or 0)

        cur = conn.execute(
            "SELECT COUNT(*) as total_users, "
            "SUM(CASE WHEN last_seen >= ? THEN 1 ELSE 0 END) as active_today, "
            "SUM(CASE WHEN last_seen >= ? THEN 1 ELSE 0 END) as active_week "
            "FROM users "
            "WHERE user_id != ? "
            "AND user_id NOT IN (SELECT user_id FROM helper_admins)"
            f"{blocked_filter}",
            (today_threshold, week_threshold, ADMIN_ID),
        )
        row = cur.fetchone()
        if row:
            summary["total_users"] = int(row["total_users"] or 0)
            summary["active_today"] = int(row["active_today"] or 0)
            summary["active_week"] = int(row["active_week"] or 0)

        summary["joined_today"] = summary["entered_today"]

        cur = conn.execute("SELECT COUNT(*) as total_movies FROM movies")
        row = cur.fetchone()
        if row:
            summary["total_movies"] = int(row["total_movies"] or 0)

        cur = conn.execute("SELECT SUM(views) as total_views FROM movie_views")
        row = cur.fetchone()
        if row:
            summary["total_views"] = int(row["total_views"] or 0)

        cur = conn.execute(
            "SELECT COUNT(*) as total_requests, "
            "SUM(CASE WHEN status IN ('pending', 'accepted') THEN 1 ELSE 0 END) as pending_requests, "
            "SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END) as completed_requests, "
            "SUM(CASE WHEN status = 'rejected' THEN 1 ELSE 0 END) as rejected_requests "
            "FROM requests "
            "WHERE user_id != ? "
            "AND user_id NOT IN (SELECT user_id FROM helper_admins)",
            (ADMIN_ID,),
        )
        row = cur.fetchone()
        if row:
            summary["total_requests"] = int(row["total_requests"] or 0)
            summary["pending_requests"] = int(row["pending_requests"] or 0)
            summary["completed_requests"] = int(row["completed_requests"] or 0)
            summary["rejected_requests"] = int(row["rejected_requests"] or 0)

    return summary


def _query_daily_series(days: int = 7) -> dict[str, list[int]]:
    labels = _make_labels(days)
    series = {
        "requests": [0] * days,
        "movie_views": [0] * days,
        "new_users": [0] * days,
    }
    if not DB_PATH.exists():
        return {"labels": labels, **series}

    placeholders = ",".join("?" for _ in ("requests", "movie_views", "new_users"))
    query = f"SELECT day, metric, value FROM daily_stats WHERE day >= ? AND metric IN ({placeholders}) ORDER BY day ASC"
    with sqlite3.connect(DB_PATH, timeout=5, check_same_thread=False) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            query, (labels[0], "requests", "movie_views", "new_users")
        ).fetchall()
        user_rows = conn.execute(
            f"""
            SELECT SUBSTR(first_seen, 1, 10) as day, COUNT(*) as total
            FROM users
            WHERE first_seen IS NOT NULL
              AND SUBSTR(first_seen, 1, 10) >= ?
              AND user_id != ?
              AND user_id NOT IN (SELECT user_id FROM helper_admins)
            GROUP BY SUBSTR(first_seen, 1, 10)
            ORDER BY day ASC
            """,
            (labels[0], ADMIN_ID),
        ).fetchall()

    for row in rows:
        day = row["day"]
        metric = row["metric"]
        value = int(row["value"] or 0)
        if day in labels and metric in series:
            index = labels.index(day)
            series[metric][index] = value

    for row in user_rows:
        day = row["day"]
        if day in labels:
            series["new_users"][labels.index(day)] = int(row["total"] or 0)

    return {"labels": labels, **series}


def _query_top_movies(limit: int = 5) -> list[tuple[str, str, int, int]]:
    if not DB_PATH.exists():
        return []

    with sqlite3.connect(DB_PATH, timeout=5, check_same_thread=False) as conn:
        conn.row_factory = sqlite3.Row
        movie_view_columns = {
            str(row[1]) for row in conn.execute("PRAGMA table_info(movie_views)").fetchall()
        }
        unique_views_expr = (
            "COALESCE(mv.unique_views, mv.views)"
            if "unique_views" in movie_view_columns
            else "mv.views"
        )
        query = f"""
        SELECT
            mv.code,
            CASE
                WHEN COALESCE(m.content_kind, 'movie') = 'serial'
                     AND COALESCE(m.episode_number, 0) > 0
                THEN COALESCE(NULLIF(m.series_title, ''), m.title) || ' ' || m.episode_number || '-qism'
                ELSE COALESCE(m.title, 'Noma''lum kino')
            END,
            mv.views,
            {unique_views_expr} AS unique_views
        FROM movie_views mv
        LEFT JOIN movies m ON m.code = mv.code
        ORDER BY
            mv.views DESC,
            unique_views DESC,
            COALESCE(mv.last_viewed_at, '') DESC,
            mv.code ASC
        LIMIT ?
    """
        return [
            (
                row["code"],
                row[1],
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
    values: list[int], labels: list[str], color_var: str = "var(--primary)"
) -> str:
    maximum = max(values) if values else 0
    maximum = max(maximum, 1)
    html = ""
    for val, lab in zip(values, labels):
        h = int((val / maximum) * 100)
        visible_height = max(h, 8) if val > 0 else 0
        html += f"""
        <div class="chart-col" title="{val}">
            <div class="chart-value">{_format_number(val)}</div>
            <div class="bar-wrap"><div class="bar" style="background: {color_var}; height: {visible_height}%"></div></div>
            <div class="chart-label">{lab[5:]}</div>
        </div>
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
        safe_title = (
            title.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        )
        w = int((views / maximum) * 100)
        rank_class = f"rank-{index}" if index <= 3 else "rank-other"

        rows.append(
            f'<div class="list-item">'
            f'<div class="flex items-center"><div class="rank-badge {rank_class}">{index}</div>'
            f'<div><div style="font-weight:500; font-size:14px;">{safe_title}</div><div class="text-sm text-dim" style="margin-top:2px;">Kod: {code} • {_format_number(unique_views)} ta unique user</div></div></div>'
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
        ini = username[0].upper() if username else "U"
        hand = f"@{username}" if username else "anonymous"
        last_seen_text = format_local_timestamp(last_seen, "%H:%M")
        html += f"""
        <div class="list-item">
            <div class="flex items-center">
                <div style="width:36px; height:36px; border-radius:50%; background:linear-gradient(135deg, var(--secondary), var(--primary)); display:flex; align-items:center; justify-content:center; margin-right:12px; font-weight:600; color:#fff;">{ini}</div>
                <div>
                   <div style="font-weight:500; font-size:14px; max-width: 150px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;">{full_name}</div>
                   <div class="text-sm text-dim" style="max-width: 150px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;">{hand}</div>
                </div>
            </div>
            <div class="bg-success-light text-sm">{last_seen_text} {APP_TIMEZONE_LABEL}</div>
        </div>
        """
    return html


def _build_page() -> str:
    summary = _query_summary()
    daily = _query_daily_series(7)
    top_movies = _query_top_movies(5)
    recent_users = _query_recent_users(6)
    updated_at = f"{local_now_text()} {APP_TIMEZONE_LABEL}"

    total_req = summary["total_requests"] or 1
    completed_pct = int((summary["completed_requests"] / total_req) * 100)
    pending_pct = int((summary["pending_requests"] / total_req) * 100)
    rejected_pct = max(0, 100 - completed_pct - pending_pct)
    if (
        summary["completed_requests"]
        + summary["pending_requests"]
        + summary["rejected_requests"]
        == 0
    ):
        completed_pct, pending_pct, rejected_pct = 0, 0, 0

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
                <div class="metric-sub text-dim">Hozir botni bloklamagan foydalanuvchilar</div>
            </div>
            <div class="card metric-box">
                <div class="metric-title">Jami Kirganlar</div>
                <div class="metric-val">{all_time_users}</div>
                <div class="metric-sub text-dim">Bot ochilgandan beri kirgan barcha userlar</div>
            </div>
            <div class="card metric-box">
                <div class="metric-title">Bugun Kirganlar</div>
                <div class="metric-val c-success">{entered_today}</div>
                <div class="metric-sub text-dim">Bugun botga kirganlar, bloklaganlar ham kiradi</div>
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
                <div class="metric-sub text-dim">7 kun faol: <span style="color:var(--text-main)">{active_week}</span></div>
            </div>
        </div>

        <!-- Request Pipeline Bar -->
        <div class="card fade-in" style="animation-delay: 0.2s; margin-bottom: 24px;">
            <h2>📊 So'rovlar Volyumi (Pipeline)</h2>
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
        </div>

        <div class="grid grid-cols-2">
            <!-- Traffic Chart -->
            <div class="card fade-in" style="animation-delay: 0.3s;">
                <h2>📈 Ohirgi 7 kun Murojaatlar</h2>
                <div class="text-sm text-dim">So'rovlar (Requests) kunlik kelish grafikasi</div>
                <div class="chart">
                    {requests_bars}
                </div>
            </div>

            <!-- Views Chart -->
            <div class="card fade-in" style="animation-delay: 0.4s;">
                <h2>👁️ Kontent Faolligi</h2>
                <div class="text-sm text-dim">7 kun ichida kinolar ko'rilishi trendi</div>
                <div class="chart">
                    {views_bars}
                </div>
            </div>
            
            <!-- Top Movies List -->
            <div class="card fade-in" style="animation-delay: 0.5s;">
                <h2>🏆 Top Kinolar</h2>
                <div>
                    {top_movies_html}
                </div>
            </div>

            <!-- Recent Users List -->
            <div class="card fade-in" style="animation-delay: 0.6s;">
                <h2>👥 Yaqinda faol bo'lganlar</h2>
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


_cache = {"data": b"", "timestamp": 0}
CACHE_TTL = 30


class SimpleHandler(BaseHTTPRequestHandler):
    server_version = "Cinema"
    sys_version = ""

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
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

        if path not in {"/", "/index.html"}:
            self.send_error(404, "Not Found")
            return

        if not _stats_request_is_authorized(self, query):
            body = (
                _unauthorized_stats_page()
                if _is_local_stats_request(self)
                else _stats_auth_bootstrap_page()
            )
            _send_bytes(self, 403 if _is_local_stats_request(self) else 200, body, content_type="text/html; charset=utf-8")
            return

        global _cache
        current_time = time.time()

        if current_time - _cache["timestamp"] > CACHE_TTL:
            _cache["data"] = _build_page().encode("utf-8")
            _cache["timestamp"] = current_time

        body = _cache["data"]
        _send_bytes(self, 200, body, content_type="text/html; charset=utf-8")

    def log_message(self, format: str, *args: object) -> None:
        return


if __name__ == "__main__":
    pid_path = Path(__file__).resolve().with_name(".webapp.pid")
    pid_path.write_text(str(os.getpid()), encoding="utf-8")

    try:
        server_address = ("0.0.0.0", PORT)
        print(
            f"Web app server running on http://{server_address[0]}:{server_address[1]}"
        )
        print(
            "Expose this address with a tunnel and use the HTTPS URL as STATS_WEBAPP_URL."
        )
        ReusableHTTPServer(server_address, SimpleHandler).serve_forever()
    finally:
        with suppress(OSError):
            pid_path.unlink()
