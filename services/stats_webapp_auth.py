import logging

logger = logging.getLogger(__name__)

import hashlib
import hmac
import json
import time
from collections.abc import Mapping, Sequence
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from config import BOT_TOKEN, WEBAPP_SIGNING_SECRET

DEFAULT_STATS_LINK_TTL_SECONDS = 600
DEFAULT_TELEGRAM_INIT_DATA_TTL_SECONDS = 300
DEFAULT_SHARE_LINK_TTL_SECONDS = 300
_STATS_SCOPE = "stats-webapp"
_SHARE_PAGE_SCOPE = "share-page"
_SHARE_MEDIA_SCOPE = "share-media"


def _secret_key() -> bytes:
    raw_secret = (WEBAPP_SIGNING_SECRET or "").strip() or (BOT_TOKEN or "").strip()
    return raw_secret.encode("utf-8")


def _signed_scope_payload(scope: str, *parts: object) -> bytes:
    normalized_parts = [scope.strip()] + [str(part).strip() for part in parts]
    return ":".join(normalized_parts).encode("utf-8")


def _signature_payload(user_id: int, expires_at: int) -> bytes:
    return _signed_scope_payload(_STATS_SCOPE, int(user_id), int(expires_at))


def _sign_payload(user_id: int, expires_at: int) -> str:
    return hmac.new(
        _secret_key(),
        _signature_payload(user_id, expires_at),
        hashlib.sha256,
    ).hexdigest()


def _telegram_webapp_secret_key() -> bytes:
    return hmac.new(b"WebAppData", _secret_key(), hashlib.sha256).digest()


def build_signed_stats_webapp_url(
    base_url: str,
    user_id: int,
    *,
    ttl_seconds: int = DEFAULT_STATS_LINK_TTL_SECONDS,
) -> str:
    raw_url = (base_url or "").strip()
    if not raw_url:
        return raw_url

    expires_at = int(time.time()) + max(30, int(ttl_seconds))
    parts = urlsplit(raw_url)
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    query.update(
        {
            "uid": str(int(user_id)),
            "exp": str(expires_at),
            "sig": _sign_payload(user_id, expires_at),
        }
    )

    return urlunsplit(
        (
            parts.scheme,
            parts.netloc,
            parts.path or "/",
            urlencode(query),
            parts.fragment,
        )
    )


def verify_signed_stats_webapp_request(
    query: Mapping[str, Sequence[str] | str],
    *,
    now: int | None = None,
) -> bool:
    def _query_value(key: str) -> str:
        raw_value = query.get(key)
        if isinstance(raw_value, str):
            return raw_value.strip()
        if isinstance(raw_value, Sequence) and raw_value:
            first_value = raw_value[0]
            return str(first_value).strip()
        return ""

    uid_text = _query_value("uid")
    exp_text = _query_value("exp")
    signature = _query_value("sig")

    if not uid_text or not exp_text or not signature:
        return False

    try:
        user_id = int(uid_text)
        expires_at = int(exp_text)
    except ValueError:
        return False

    current_time = int(time.time()) if now is None else int(now)
    if expires_at < current_time:
        return False

    expected_signature = _sign_payload(user_id, expires_at)
    return hmac.compare_digest(expected_signature, signature)


def _share_scope(media: bool) -> str:
    return _SHARE_MEDIA_SCOPE if media else _SHARE_PAGE_SCOPE


def _sign_share_payload(code: str, expires_at: int, *, media: bool) -> str:
    normalized_code = (code or "").strip()
    return hmac.new(
        _secret_key(),
        _signed_scope_payload(_share_scope(media), normalized_code, int(expires_at)),
        hashlib.sha256,
    ).hexdigest()


def build_signed_share_query(
    code: str,
    *,
    media: bool = False,
    ttl_seconds: int = DEFAULT_SHARE_LINK_TTL_SECONDS,
) -> dict[str, str]:
    normalized_code = (code or "").strip()
    if not normalized_code:
        return {}

    expires_at = int(time.time()) + max(30, int(ttl_seconds))
    return {
        "exp": str(expires_at),
        "sig": _sign_share_payload(normalized_code, expires_at, media=media),
    }


def verify_signed_share_request(
    code: str,
    query: Mapping[str, Sequence[str] | str],
    *,
    media: bool = False,
    now: int | None = None,
) -> bool:
    normalized_code = (code or "").strip()
    if not normalized_code:
        return False

    def _query_value(key: str) -> str:
        raw_value = query.get(key)
        if isinstance(raw_value, str):
            return raw_value.strip()
        if isinstance(raw_value, Sequence) and raw_value:
            return str(raw_value[0]).strip()
        return ""

    exp_text = _query_value("exp")
    signature = _query_value("sig")
    if not exp_text or not signature:
        return False

    try:
        expires_at = int(exp_text)
    except ValueError:
        return False

    current_time = int(time.time()) if now is None else int(now)
    if expires_at < current_time:
        return False

    expected_signature = _sign_share_payload(
        normalized_code,
        expires_at,
        media=media,
    )
    return hmac.compare_digest(expected_signature, signature)


def verify_telegram_webapp_init_data(
    init_data: str,
    *,
    now: int | None = None,
    max_age_seconds: int = DEFAULT_TELEGRAM_INIT_DATA_TTL_SECONDS,
) -> dict[str, object] | None:
    raw_init_data = (init_data or "").strip()
    if not raw_init_data:
        return None

    pairs = parse_qsl(raw_init_data, keep_blank_values=True)
    if not pairs:
        return None

    fields = dict(pairs)
    received_hash = fields.pop("hash", "").strip()
    auth_date_text = fields.get("auth_date", "").strip()
    if not received_hash or not auth_date_text:
        return None

    try:
        auth_date = int(auth_date_text)
    except ValueError:
        return None

    current_time = int(time.time()) if now is None else int(now)
    max_age = max(30, int(max_age_seconds))
    if auth_date > current_time + 30:
        return None
    if current_time - auth_date > max_age:
        return None

    data_check_string = "\n".join(
        f"{key}={value}" for key, value in sorted(fields.items())
    )
    expected_hash = hmac.new(
        _telegram_webapp_secret_key(),
        data_check_string.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(expected_hash, received_hash):
        return None

    user_payload: dict[str, object] = {}
    user_text = fields.get("user", "").strip()
    if user_text:
        try:
            parsed_user = json.loads(user_text)
        except json.JSONDecodeError:
            return None
        if not isinstance(parsed_user, dict):
            return None
        user_payload = parsed_user

    user_id = int(user_payload.get("id") or 0)
    if user_id <= 0:
        return None

    return {
        "auth_date": auth_date,
        "fields": fields,
        "user": user_payload,
        "user_id": user_id,
    }
