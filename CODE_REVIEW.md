# 🔍 Cinema-bot — Senior Developer Code Review

## Loyiha haqida qisqacha
Bu Telegram Cinema boti bo'lib, `aiogram 3.26` frameworki va `aiosqlite` bilan ishlaydi. Kinolar/seriallar boshqarish, reklama tizimi, force-subscription, statistika dashboard (PIL bilan rasm, webapp bilan web sahifa) va admin boshqaruv funksiyalarini o'z ichiga oladi.

---

## 📁 1. ARXITEKTURA VA LOYIHA TUZILISHI

### ❌ Muammolar

#### 1.1 Monolitik `database.py` — 3296 qator
```
database.py — 3296 qator kod
```
Bu fayl **barcha** database logikasini o'z ichiga oladi: init, migration, CRUD, caching, ad management, user management, content management, stats — hammasi bitta faylda. Bu **SOLID** tamoyillarining **Single Responsibility** qoidasini buzadi.

**To'g'rilash:** `database/` papkasiga bo'lish:
- `database/connection.py` — ulanish va init
- `database/migrations.py` — sxema o'zgarishlari
- `database/users.py` — user CRUD
- `database/content.py` — movie/serial CRUD
- `database/ads.py` — reklama CRUD
- `database/stats.py` — statistika
- `database/cache.py` — cache boshqaruvi

#### 1.2 Circular Import xavfi va Monkey-patching
`handlers/admin_text_refined.py` faylida **monkey-patching** qilinadi:
```python
admin._request_text = _request_text
admin._request_added_text = _request_added_text
admin._show_stats_webapp = _show_stats_webapp_restored
admin._show_serial_mode_picker = _show_serial_mode_picker
# ... 20+ ta override
```
Bu juda xavfli pattern — runtime'da modul attributlari almashtiriladi. Debug qilish, testing va kodni tushunish juda qiyinlashadi. Bunday "hot-swap" pattern production loyihalarda **qat'iyan tavsiya etilmaydi**.

**To'g'rilash:** Template Method pattern yoki Strategy pattern, yoki oddiy meros (inheritance) ishlatish kerak.

#### 1.3 Duplikat funksiyalar
`admin.py` da `_serial_video_prompt_text` funksiyasi **ikki marta** aniqlangan:
```python
def _serial_video_prompt_text(title: str, episode_number: int) -> str:
    return (...)  # birinchi versiya

def _serial_video_prompt_text(title: str, episode_number: int) -> str:
    return (...)  # ikkinchi versiya — bu birinchisini override qiladi
```
Python bu haqda xato bermaydi, lekin birinchi funksiya **hech qachon** ishlatilmaydi.

#### 1.4 `repositories/` qatlami faqat proxy
```python
# repositories/users.py
from database import (
    get_admin_permissions,
    has_feature_trial_used,
    is_admin_user,
    ...
)
```
Repository pattern nazarda tutilgan, lekin haqiqatda bu faqat re-export qiladi. Hech qanday qo'shimcha logika yo'q. Bu **keraksiz abstraksiya** yoki to'liq implement qilinmagan pattern.

#### 1.5 `handlers/admin.__codex_backup__.bak` — backup fayl repozitoriyada
Bu `.bak` fayl `.gitignore`ga qo'shilmagan va repo'da saqlangan.

---

## 🔐 2. XAVFSIZLIK

### ❌ Kritik muammolar

#### 2.1 BOT_TOKEN ochiq kodda ishlatiladi
`services/legacy_media.py`:
```python
def _legacy_file_url(file_path: str) -> str:
    token = (LEGACY_BOT_TOKEN or "").strip()
    return f"https://api.telegram.org/file/bot{token}/{file_path.lstrip('/')}"

def _current_file_url(token: str, file_path: str) -> str:
    return f"https://api.telegram.org/file/bot{token}/{file_path.lstrip('/')}"
```
Token URL'ga qo'yiladi, bu logga tushishi mumkin. `promote_file_id_to_video` da `current_bot.token` ishlatiladi — agar log'lansa, token sizishi mumkin.

#### 2.2 SQL Injection xavfi — f-string bilan SQL
`database.py` da:
```python
async with connection.execute(f"PRAGMA table_info({table_name})") as cursor:
```
`table_name` parametrizatsiya qilinmagan. Garchi hozirda ichki qiymatlar kelsa-da, bu pattern xavfli.

#### 2.3 `webapp.py` da DELETE operatsiyasi autentifikatsiya bilan, lekin CSRF himoyasi yo'q
```python
if action_key == "delete-blocked-users":
    with sqlite3.connect(DB_PATH, timeout=5, check_same_thread=False) as conn:
        conn.execute("DELETE FROM users WHERE COALESCE(is_blocked, 0) = 1")
        conn.commit()
```
POST endpoint mavjud, lekin **CSRF token** yo'q. Agar webapp Telegram WebApp sifatida ochilsa, yaxshisi `initData` tekshiruvi yoki CSRF token qo'shish kerak.

#### 2.4 `security.py` middlewares **ishlatilmayapti**
`middlewares/security.py` da 3 ta middleware bor:
- `InputSanitizationMiddleware`
- `CommandAllowlistMiddleware`
- `CallbackSignatureMiddleware`

Lekin `main.py` da faqat `AntiSpamMiddleware` qo'shilgan:
```python
dispatcher.message.middleware(AntiSpamMiddleware(rate_limit=0.5))
dispatcher.callback_query.middleware(AntiSpamMiddleware(rate_limit=0.5))
```
Security middlewarelar **hech qachon** ro'yxatdan o'tkazilmagan va **ishlamaydi**!

#### 2.5 `movies.db` repozitoriyada saqlangan
`.gitignore` da `!movies.db` istisno bor — ya'ni database Git'ga qo'shilgan. Agar unda foydalanuvchi ma'lumotlari bo'lsa, bu **privacy breach**.

#### 2.6 `ForceSubMiddleware` bo'sh
```python
class ForceSubMiddleware(BaseMiddleware):
    async def __call__(self, handler, event, data) -> Any:
        return await handler(event, data)  # Hech narsa qilmaydi!
```
Bu middleware nomlangan, lekin aslida hech qanday tekshiruv qilmaydi.

#### 2.7 `_ensure_message_access` ruxsatsiz holatda **hech narsa qaytarmaydi**
```python
async def _ensure_message_access(message, *, permission=None, owner_only=False) -> bool:
    user_id = message.from_user.id
    if owner_only:
        return _is_owner(user_id)
    ...
```
Return `False` bo'lganda **foydalanuvchiga hech qanday xabar bermaydi**. Foydalanuvchi nimaga ruxsati yo'qligini bilmaydi.

---

## 🐛 3. BUGLAR VA LOGIK XATOLAR

### ❌ Topilgan buglar

#### 3.1 `touch_user` `None` `from_user` bilan crash
`services/telegram_context.py`:
```python
async def touch_message_user(message: types.Message) -> None:
    await touch_user(
        message.from_user.id,   # from_user None bo'lishi mumkin!
        message.from_user.username,
        message.from_user.full_name,
    )
```
Agar `message.from_user` `None` bo'lsa (channel post va boshqa holatlar), `AttributeError` chiqadi.

#### 3.2 `receive_new_channel` da `message.text` `None` bo'lishi mumkin
```python
@router.message(AdminChannelState.waiting_for_channel)
async def receive_new_channel(message: types.Message, state: FSMContext) -> None:
    if message.text in ADMIN_ACTIONS + USER_ACTIONS:
        ...
    parts = message.text.split(maxsplit=2)  # message.text None bo'lishi mumkin!
```
Agar foydalanuvchi rasm yoki sticker yuborsa, `AttributeError` chiqadi.

#### 3.3 `dashboard.py` da Windows-ga bog'langan font yo'llari
```python
FONT_DIR = Path("C:/Windows/Fonts")
FONT_REGULAR = [FONT_DIR / "segoeui.ttf", FONT_DIR / "arial.ttf"]
FONT_BOLD = [FONT_DIR / "seguisb.ttf", FONT_DIR / "segoeuib.ttf", FONT_DIR / "arialbd.ttf"]
```
Linux/macOS serverda bu fontlar **topilmaydi**. `_font()` funksiyasi `ImageFont.load_default()` ga fallback qiladi, lekin natija **juda yomon** ko'rinadi.

#### 3.4 `_utc_now()` — timezone ma'lumotini olib tashlaydi
```python
def _utc_now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None, microsecond=0)
```
UTC timezone'ni oladi, keyin `tzinfo=None` qiladi. Bu **naive datetime** yaratadi va boshqa joyda timezone-aware datetime bilan solishtirganida xato chiqishi mumkin.

#### 3.5 `callback.message.text = CHANNELS_BUTTON` — Telegram object mutation
```python
# admin.py
callback.message.text = CHANNELS_BUTTON
old_from = callback.message.from_user
callback.message.from_user = callback.from_user
await admin_channels_menu(callback.message, state)
callback.message.from_user = old_from
```
Telegram message objectini **mutatsiya** qilish juda xavfli. Bu aiogram'ning ichki cache'ini buzishi mumkin.

#### 3.6 Global `db` o'zgaruvchi — concurrency muammosi
```python
db: aiosqlite.Connection | None = None
```
Global o'zgaruvchi sifatida database connection saqlash, agar connection uzilsa yoki context manager ishlatilmasa, **dangling connection** muammosiga olib keladi.

#### 3.7 `AntiSpamMiddleware` — dict tozalash race condition
```python
if len(self.users) > 5000:
    cutoff = now - self.rate_limit
    self.users = {k: v for k, v in self.users.items() if v > cutoff}
```
Dict'ni qayta yaratish vaqtida boshqa coroutine eski dict'ga yozishi mumkin. Garchi Python GIL bu muammoni amalda kamaytirsa-da, yaxshiroq `asyncio.Lock` ishlatish kerak.

#### 3.8 `_read_runtime_stats_webapp_url` har safar fayl o'qiydi
```python
def _read_runtime_stats_webapp_url() -> str:
    runtime_url_path = BASE_DIR / ".stats_webapp_url"
    with suppress(OSError):
        runtime_url = runtime_url_path.read_text(encoding="utf-8").strip()
```
Har bir chaqiruvda disk I/O — bu statik qiymat bo'lishi kerak yoki cache qo'yish lozim.

---

## ⚡ 4. PERFORMANCE MUAMMOLARI

#### 4.1 `get_all_movies()` har safar barcha kinolarni yuklaydi
Kontent ro'yxati, delete panel, content list, movie list — hamma joyda **barcha kinolar** bazadan yuklanadi:
```python
movies = await get_all_movies()
movie_items = [movie for movie in movies if movie[2] == "movie"]
serial_items = [movie for movie in movies if movie[2] == "serial"]
```
Agar 10000 ta kino bo'lsa, har bir admin sahifasi uchun **10000 ta row** yuklash kerak. Bu **juda sekin**.

**To'g'rilash:** Sahifalash (pagination) SQL darajasida qilish:
```sql
SELECT code, title, content_kind FROM movies WHERE content_kind = ? LIMIT ? OFFSET ?
```

#### 4.2 Cache invalidation strategiyasi yo'q
```python
movie_cache: dict[str, tuple[str, str, str, str]] = {}
serial_group_cache: dict[str, tuple[str, str, str, str]] = {}
fav_cache: dict[int, set[str]] = {}
```
Cachelar mavjud, lekin:
- TTL (Time-to-Live) yo'q
- Cache hajmi chegaralanmagan — **memory leak** xavfi
- Invalidation **manual** va ba'zan yo'q

#### 4.3 `webapp.py` — sinxron SQLite va HTTP server
```python
class SimpleHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        ...
        with sqlite3.connect(DB_PATH, timeout=5) as conn:
            ...
```
Webapp `http.server` ishlatadi — bu **sinxron, single-threaded** server. Har bir so'rov bazadan sinxron o'qiydi. Production uchun **aiohttp** yoki **FastAPI** ishlatish kerak.

#### 4.4 Dashboard rasm har safar qayta yaratiladi
```python
def render_dashboard(panel: str, payload: dict) -> bytes:
    image = _create_background()  # Har safar 1600x1000 rasm yaratiladi
    ...
```
PIL bilan har safar yangi rasm yaratish CPU-intensive. Cache qo'yish kerak.

#### 4.5 `_ensure_users_tracking_columns` — har safar schema tekshiradi
```python
async def _ensure_users_tracking_columns(connection) -> bool:
    user_columns = await _get_table_columns(connection, "users")
    if "is_blocked" not in user_columns:
        await connection.execute("ALTER TABLE ...")
```
Har bir ad recipient olishda `PRAGMA table_info` ishga tushadi. Bu bir marta tekshirilishi va natijasi saqlanishi kerak.

---

## 🧪 5. TEST VA SIFAT NAZORATI

### ❌ Kritik yo'qliklar

#### 5.1 **Hech qanday test yo'q**
- Unit testlar — yo'q
- Integration testlar — yo'q
- `pytest`, `unittest` — konfiguratsiyasi yo'q
- `tests/` papkasi — yo'q

#### 5.2 Type checking konfiguratsiyasi yo'q
- `mypy.ini` yoki `pyproject.toml` da mypy konfiguratsiyasi yo'q
- Type annotationlar ba'zi joylarda bor, lekin tekshirilmaydi

#### 5.3 Linting konfiguratsiyasi yo'q
- `ruff.toml`, `flake8`, `pylint` — hech biri yo'q
- Kod standarti hujjatlashtirilmagan

#### 5.4 CI/CD yo'q
- GitHub Actions — yo'q
- Avtomatik deploy — yo'q

---

## 📦 6. DEPLOYMENT VA INFRATUZILMA

#### 6.1 Faqat Windows uchun tayyorlangan
- `start_bot.bat`, `stop_bot.bat` — faqat Windows
- `runtime_manager.py` da `ctypes.windll`, `taskkill`, PowerShell ishlatiladi
- `stats_tunnel.py` da `cloudflared.exe`, Windows path'lar
- `dashboard.py` da `C:/Windows/Fonts` yo'li

Linux server'da deploy qilib bo'lmaydi (yoki juda ko'p muammolar chiqadi).

**To'g'rilash:**
- Dockerfile qo'shish
- `systemd` service fayli qo'shish
- Cross-platform support qo'shish

#### 6.2 Docker support yo'q
- `Dockerfile` — yo'q
- `docker-compose.yml` — yo'q
- Secret management faqat `.env` fayl bilan

#### 6.3 `requirements.txt` — pinning bor, lekin hash yo'q
```
aiogram==3.26.0
aiosqlite==0.22.1
python-dotenv==1.2.2
pillow==12.1.1
```
Hash-based pinning (`--hash`) yo'q — supply chain attack xavfi.

#### 6.4 `README.md` yo'q
Loyihani tushunish, o'rnatish va ishga tushirish uchun hech qanday hujjat yo'q.

---

## 🔧 7. KOD SIFATI VA BEST PRACTICES

#### 7.1 Exception handling juda keng
```python
except Exception:
    await message.answer_video(
        video=file_id, caption=review_text, reply_markup=keyboard
    )
```
`except Exception` — barcha xatolarni tutib oladi. Aniq exception turlarini ishlatish kerak.

#### 7.2 Magic numberlar
```python
SQLITE_BUSY_TIMEOUT_MS = 5000
SQLITE_CACHE_SIZE_KIB = 32768
SQLITE_MMAP_SIZE = 268435456
MAX_HISTORY_ITEMS = 50
USER_TOUCH_TTL_SECONDS = 300
```
Bu qiymatlar `database.py` da aniqlangan, lekin ularning izohsiz qoldirilganlari ham bor:
```python
for _attempt in range(6):  # Nima uchun 6?
for _attempt in range(8):  # Nima uchun 8?
```

#### 7.3 `__all__` to'g'ri ishlatilmayapti
`admin_runtime_helpers.py` da `__all__` aniqlangan, lekin **private** funksiyalar (underscore bilan boshlanuvchi) export qilinmoqda:
```python
__all__ = [
    "_ad_duration_prompt",
    "_build_ads_panel_text",
    ...
]
```
Private funksiyalarni public API sifatida export qilish — anti-pattern.

#### 7.4 Import tartibsizligi
`admin.py` faylining oxirida import:
```python
from .admin_runtime_helpers import (
    _ad_duration_prompt,
    _build_ads_panel_text,
    ...
)
```
Fayl oxirida import — circular import muammosini hal qilish uchun qilingan. Bu arxitektura muammosining belgisi.

#### 7.5 Hardcoded matnlar
Barcha Telegram xabar matnlari kodda hardcoded. Lokalizatsiya (i18n) mumkin emas. Agar boshqa tilga o'girish kerak bo'lsa, barcha fayllarni o'zgartirish zarur.

---

## 🏗️ 8. DATABASE DIZAYNI

#### 8.1 Migration tizimi yo'q
Schema o'zgarishlari `ALTER TABLE` bilan `init_db()` ichida qilinadi:
```python
if "is_blocked" not in user_columns:
    await connection.execute("ALTER TABLE users ADD COLUMN is_blocked ...")
```
Bu yondashuv kichik loyiha uchun ishlaydigan bo'lsa-da, **Alembic** yoki shunga o'xshash migration tizim kerak.

#### 8.2 Foreign key constraints yo'q
SQL jadvallarida `FOREIGN KEY` aniqlashlari ko'rinmaydi. Data integrity database darajasida ta'minlanmaydi.

#### 8.3 Transaction management zaifligi
Ko'p joylarda `await connection.commit()` manually chaqiriladi. Agar operatsiya orasida exception chiqsa, **partial commit** bo'lishi mumkin.

---

## 📊 XULOSA — USTUVORLIK BO'YICHA

### 🔴 KRITIK (darhol tuzatish kerak)
| # | Muammo | Fayl |
|---|--------|------|
| 1 | Security middlewarelar ishlamayapti | `main.py` |
| 2 | `from_user` None tekshiruvi yo'q | `telegram_context.py` |
| 3 | `movies.db` repoda saqlangan | `.gitignore` |
| 4 | Test yo'q | — |
| 5 | `message.text` None handling | `admin.py`, `channel_fix.py` |

### 🟠 YUQORI (bir hafta ichida)
| # | Muammo | Fayl |
|---|--------|------|
| 6 | Monkey-patching pattern | `admin_text_refined.py` |
| 7 | `database.py` monoliti | `database.py` |
| 8 | Dashboard font Windows-only | `dashboard.py` |
| 9 | `webapp.py` sinxron server | `webapp.py` |
| 10 | Cache memory leak xavfi | `database.py` |

### 🟡 O'RTA (bir oy ichida)
| # | Muammo | Fayl |
|---|--------|------|
| 11 | Docker support yo'q | — |
| 12 | CI/CD yo'q | — |
| 13 | README yo'q | — |
| 14 | SQL pagination yo'q | `admin_runtime_helpers.py` |
| 15 | Migration tizimi yo'q | `database.py` |

### 🟢 PAST (rejalashtirilgan refactoring)
| # | Muammo | Fayl |
|---|--------|------|
| 16 | Hardcoded matnlar (i18n) | Hammasi |
| 17 | Repository pattern to'liq emas | `repositories/` |
| 18 | Linting/mypy config | — |
| 19 | `__all__` private export | `admin_runtime_helpers.py` |
| 20 | Duplikat funksiyalar | `admin.py` |

---

### ✅ Ijobiy tomonlar
1. **Yaxshi structured FSM** — `StatesGroup` yordamida state management to'g'ri qilingan
2. **Single instance lock** — bir vaqtda ikkita bot ishlamasligini ta'minlaydi
3. **Graceful shutdown** — signallarni to'g'ri ushlaydi
4. **Anti-spam middleware** — rate limiting mavjud
5. **Ad lifecycle management** — reklama yaratish, tarqatish, o'chirish to'g'ri ketma-ketlikda
6. **Sponsor channel obuna tekshiruvi** — force subscription ishlaydi
7. **HMAC-based URL signing** — webapp URL'lari signed
8. **View tracking** — kontent ko'rishlar statistikasi
9. **Retry logic** — Telegram API xatolari uchun qayta urinish mavjud
10. **`config.py` da secret file support** — Docker/K8s secrets qo'llab-quvvatlanadi
