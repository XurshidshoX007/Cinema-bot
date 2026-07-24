import logging
import os
import json
from contextlib import suppress
from pathlib import Path

logger = logging.getLogger(__name__)
from urllib.parse import urlsplit

try:
    from dotenv import load_dotenv
except ImportError:
    load_dotenv = None

BASE_DIR = Path(__file__).resolve().parent
ENV_PATH = BASE_DIR / ".env"


def _load_env_file(env_path: Path) -> None:
    if not env_path.exists():
        return

    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()

        if not line or line.startswith("#"):
            continue

        if line.startswith("export "):
            line = line[7:].strip()

        if "=" not in line:
            continue

        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")

        os.environ.setdefault(key, value)


def _get_secret_from_file(key: str) -> str | None:
    """
    Try to read secret from a file, e.g., Docker/Kubernetes secret mounted at
    /run/secrets/<KEY> or /var/secrets/<KEY>. The key is converted to lowercase
    and underscores to hyphens? We'll just use the key as filename.
    """
    # Common locations for secret files
    paths = [
        Path(f"/run/secrets/{key}"),
        Path(f"/var/secrets/{key}"),
        Path(f"/etc/secrets/{key}"),
    ]
    for p in paths:
        if p.is_file():
            try:
                return p.read_text(encoding="utf-8").strip()
            except (OSError, IOError):
                continue
    return None


def _get_env(name: str) -> str:
    # First, try to get from environment (could be set directly or via secret file)
    value = os.getenv(name)
    if value is not None:
        value = value.strip()
        if value:
            return value

    # Fallback to secret file
    secret = _get_secret_from_file(name)
    if secret is not None:
        return secret

    # Finally, load from .env file (only for local development)
    if load_dotenv is not None:
        load_dotenv(dotenv_path=ENV_PATH)
    else:
        _load_env_file(ENV_PATH)

    value = os.getenv(name)
    if value is None:
        raise ValueError(
            f"{name} topilmadi. Uni environment'da yoki {ENV_PATH} faylida belgilang."
        )
    value = value.strip()
    if not value:
        raise ValueError(
            f"{name} bosh. Uni environment'da yoki {ENV_PATH} faylida to'ldiring."
        )
    return value


def _get_env_optional(name: str, default: str = "") -> str:
    # Similar precedence: env, secret file, .env, then default
    value = os.getenv(name)
    if value is not None:
        value = value.strip()
        if value:
            return value

    secret = _get_secret_from_file(name)
    if secret is not None:
        return secret

    if load_dotenv is not None:
        load_dotenv(dotenv_path=ENV_PATH)
    else:
        _load_env_file(ENV_PATH)

    value = os.getenv(name)
    if value is None:
        return default
    return value.strip()


BOT_TOKEN = _get_env("BOT_TOKEN")
ADMIN_ID = int(_get_env("ADMIN_ID"))
LEGACY_BOT_TOKEN = _get_env_optional("LEGACY_BOT_TOKEN")
LEGACY_MEDIA_BRIDGE_CHAT_ID = _get_env_optional("LEGACY_MEDIA_BRIDGE_CHAT_ID")
WEBAPP_SIGNING_SECRET = _get_env_optional("WEBAPP_SIGNING_SECRET")


def _read_runtime_stats_webapp_url() -> str:
    runtime_url_path = BASE_DIR / ".stats_webapp_url"
    with suppress(OSError):
        runtime_url = runtime_url_path.read_text(encoding="utf-8").strip()
        if runtime_url.startswith("https://"):
            return runtime_url

    return ""


def get_stats_webapp_url() -> str:
    runtime_url = _read_runtime_stats_webapp_url()
    if runtime_url:
        return runtime_url

    fallback_url = _get_env_optional("STATS_WEBAPP_URL").strip()
    if fallback_url.startswith("https://"):
        return fallback_url
    return ""


def is_telegram_compatible_stats_webapp_url(url: str) -> bool:
    raw_url = (url or "").strip()
    if not raw_url.startswith("https://"):
        return False

    host = urlsplit(raw_url).netloc.strip().lower()
    if host.endswith(".ngrok-free.dev"):
        return False
    return bool(host)


STATS_WEBAPP_URL = get_stats_webapp_url()


def _parse_sponsor_channels() -> list[dict]:
    raw = _get_env_optional("SPONSOR_CHANNELS", "[]")
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, list):
            return parsed
    except Exception:
        pass
    return []


SPONSOR_CHANNELS = _parse_sponsor_channels()
