# 📋 Cinema-bot — Optimallashtirish Rejasi

> **Tayyorlagan:** Senior Developer Code Review  
> **Sana:** 2026-07-24  
> **Loyiha:** Cinema-bot (aiogram 3.26 + aiosqlite + Pillow)  
> **Jami fayllar:** ~35 ta Python fayl, ~20,400 qator kod

---

## 🗺️ UMUMIY STRATEGIYA

Reja **4 fazaga** bo'linadi. Har bir faza oldingi fazaga bog'liq emas — parallel ishlash mumkin.
Har bir faza ichida vazifalar **ustuvorlik tartibida**.

```
┌─────────────────────────────────────────────────────┐
│  FAZA 1: Favqulodda tuzatishlar       (1-3 kun)    │
│  ├─ Crashlar va xavfsizlik teshiklari               │
│  └─ Bot ishdan chiqishini to'xtatish                 │
├─────────────────────────────────────────────────────┤
│  FAZA 2: Arxitektura tozalash          (1-2 hafta)  │
│  ├─ database.py parchalash                           │
│  ├─ Monkey-patching yo'q qilish                      │
│  └─ Handler tuzilishini qayta qurish                 │
├─────────────────────────────────────────────────────┤
│  FAZA 3: Performance & Infratuzilma   (2-3 hafta)   │
│  ├─ SQL pagination, cache tizimi                     │
│  ├─ Webapp async migratsiya                          │
│  └─ Docker, CI/CD, testlar                           │
├─────────────────────────────────────────────────────┤
│  FAZA 4: Sifat va kengaytirish         (joriy)      │
│  ├─ i18n, monitoring, logging                        │
│  └─ Feature flag, A/B test infra                     │
└─────────────────────────────────────────────────────┘
```

---

---

# 🔴 FAZA 1 — FAVQULODDA TUZATISHLAR (1-3 kun)

> **Maqsad:** Botni buzib qo'yishi mumkin bo'lgan crashlarni va xavfsizlik teshiklarini darhol yopish.  
> **Risk:** Bu tuzatishlarsiz bot istalgan payt ishdan chiqishi mumkin.

---

## 1.1 `from_user` None crash himoyasi
**Fayl:** `services/telegram_context.py`  
**Muammo:** `message.from_user` `None` bo'lganda `AttributeError` crash  
**Kuchi:** 30+ joyda `from_user.id`, `from_user.username` tekshiruvsiz ishlatiladi  
**Vaqt:** ~2 soat

### Qilinadigan ish:

```python
# HOZIRGI (xavfli):
async def touch_message_user(message: types.Message) -> None:
    await touch_user(
        message.from_user.id,
        message.from_user.username,
        message.from_user.full_name,
    )

# KERAKLI:
async def touch_message_user(message: types.Message) -> None:
    user = message.from_user
    if user is None:
        return
    await touch_user(user.id, user.username, user.full_name)

async def touch_callback_user(callback: types.CallbackQuery) -> None:
    user = callback.from_user
    if user is None:
        return
    await touch_user(user.id, user.username, user.full_name)
```

### Boshqa fayllar (tekshiruv qo'shish):
| Fayl | Qator | Xatarli joy |
|------|-------|-------------|
| `handlers/admin.py` | 192, 206 | `_ensure_message_access`, `_ensure_callback_access` |
| `handlers/admin.py` | 2248-2256 | `admin_global_handler` |
| `handlers/admin_ui.py` | 48-49 | `admin_global_ui_handler` |
| `handlers/start.py` | 49 | `start_handler` |
| `handlers/user.py` | 134 | `user_global_handler` |
| `handlers/kino.py` | 23, 57 | `toggle_favorite`, `toggle_serial_favorite` |
| `handlers/chat_member.py` | 13 | `sync_private_chat_membership` |

---

## 1.2 `message.text` None himoyasi
**Fayl:** `handlers/admin.py` (receive_new_channel), `handlers/channel_fix.py`  
**Muammo:** Foydalanuvchi matn o'rniga rasm/sticker yuborganda crash  
**Vaqt:** ~1 soat

### Qilinadigan ish:

```python
# HOZIRGI (xavfli):
@router.message(AdminChannelState.waiting_for_channel)
async def receive_new_channel(message, state):
    if message.text in ADMIN_ACTIONS + USER_ACTIONS:
        ...
    parts = message.text.split(maxsplit=2)  # CRASH!

# KERAKLI:
@router.message(AdminChannelState.waiting_for_channel)
async def receive_new_channel(message, state):
    if not message.text:
        await message.answer("Iltimos, matn yuboring.")
        return
    if message.text in ADMIN_ACTIONS + USER_ACTIONS:
        ...
    parts = message.text.split(maxsplit=2)
```

### Tekshirish kerak bo'lgan joylar:
- `handlers/admin.py` — barcha `StateFilter` handler'larda
- `handlers/user.py` — `receive_movie_code`, `collection_code_search`
- `handlers/channel_fix.py` — `receive_new_channel`
- `handlers/user_search_prompt.py` — `search_prompt` (F.text filtri bor — OK)

---

## 1.3 Security middleware'larni yoqish
**Fayl:** `main.py`  
**Muammo:** `InputSanitizationMiddleware` yozilgan, lekin ro'yxatdan o'tkazilmagan  
**Vaqt:** ~1 soat

### Qilinadigan ish:

```python
# main.py - create_dispatcher() ichiga qo'shish:
from middlewares.security import InputSanitizationMiddleware

def create_dispatcher() -> Dispatcher:
    dispatcher = Dispatcher()
    
    # Xavfsizlik (birinchi bo'lib ishlashi kerak)
    dispatcher.message.middleware(InputSanitizationMiddleware())
    
    # Anti-spam
    dispatcher.message.middleware(AntiSpamMiddleware(rate_limit=0.5))
    dispatcher.callback_query.middleware(AntiSpamMiddleware(rate_limit=0.5))
    
    for router in ROUTERS:
        dispatcher.include_router(router)
    return dispatcher
```

> ⚠️ **Eslatma:** `CommandAllowlistMiddleware` ni hozircha **qo'shmang** — u barcha oddiy matn xabarlarni bloklaydi va foydalanuvchilar kino kodi yubora olmay qoladi. Bu middleware ni qayta yozish kerak (faqat `/` boshlanuvchi noma'lum commandlarni bloklash uchun).

> ⚠️ **Eslatma:** `CallbackSignatureMiddleware` ni **qo'shmang** — hozirgi callback_data formatiga mos emas (`payload|signature` format kerak bo'ladi).

---

## 1.4 `movies.db` ni Git'dan chiqarish
**Fayl:** `.gitignore`  
**Muammo:** Foydalanuvchi ma'lumotlari Git tarixida saqlanadi  
**Vaqt:** ~30 daqiqa

### Qilinadigan ish:

```bash
# 1. .gitignore dan !movies.db qatorini olib tashlash
# HOZIRGI:
*.db
!movies.db

# KERAKLI:
*.db

# 2. Git tracking'dan chiqarish
git rm --cached movies.db movies.db-shm movies.db-wal
git commit -m "chore: remove movies.db from tracking"

# 3. (ixtiyoriy) Git tarixidan ham tozalash
git filter-branch --force --index-filter \
  'git rm --cached --ignore-unmatch movies.db movies.db-shm movies.db-wal' \
  HEAD
```

---

## 1.5 Bo'sh `ForceSubMiddleware` ni tuzatish yoki olib tashlash
**Fayl:** `middlewares/forcesub.py`  
**Muammo:** Hech narsa qilmaydigan middleware — chalkash  
**Vaqt:** ~15 daqiqa

### Qilinadigan ish:
Middleware'ni olib tashlash (force-sub logikasi `ensure_feature_access` funksiyalarida allaqachon amalga oshirilgan):

```python
# O'CHIRISH:
class ForceSubMiddleware(BaseMiddleware):
    async def __call__(self, handler, event, data) -> Any:
        return await handler(event, data)
```

---

## 1.6 Duplikat `_serial_video_prompt_text` ni o'chirish
**Fayl:** `handlers/admin.py`  
**Muammo:** Bir xil nomdagi funksiya 2 marta aniqlangan, birinchisi hech qachon ishlamaydi  
**Vaqt:** ~10 daqiqa

### Qilinadigan ish:
Birinchi aniqlashni o'chirish, ikkinchisini (to'liqroq) qoldirish.

---

## 1.7 `.bak` faylni o'chirish
**Fayl:** `handlers/admin.__codex_backup__.bak`  
**Vaqt:** ~5 daqiqa

```bash
git rm handlers/admin.__codex_backup__.bak
echo "*.bak" >> .gitignore
```

---

### 📊 FAZA 1 XULOSA

| Vazifa | Vaqt | Ta'sir |
|--------|------|--------|
| `from_user` None himoyasi | 2 soat | Crashni to'xtatadi |
| `message.text` None himoyasi | 1 soat | Crashni to'xtatadi |
| Security middleware yoqish | 1 soat | Input sanitization ishlaydi |
| `movies.db` Git'dan chiqarish | 30 daq | Privacy breach yopiladi |
| Bo'sh middleware o'chirish | 15 daq | Kod tozalanadi |
| Duplikat funksiya o'chirish | 10 daq | Bug potensiali yo'qoladi |
| `.bak` fayl o'chirish | 5 daq | Repo tozalanadi |
| **JAMI** | **~5 soat** | **Bot barqarorligi** |

---

---

# 🟠 FAZA 2 — ARXITEKTURA TOZALASH (1-2 hafta)

> **Maqsad:** Kod bazasini maintainable va testable qilish.  
> **Risk:** Bu qilinmasa, har bir yangi feature qo'shish 3-5x ko'proq vaqt oladi.

---

## 2.1 `database.py` ni parchalash (3296 → 7 fayl)
**Vaqt:** 2-3 kun

### Yangi tuzilma:

```
database/
├── __init__.py          # Public API re-export (backward compatible)
├── connection.py        # DB_PATH, init_db(), close_db(), _get_db()
├── migrations.py        # _ensure_*_columns(), schema versioning
├── cache.py             # movie_cache, fav_cache, TTL logikasi
├── users.py             # touch_user(), mark_user_blocked(), admin funksiyalar
├── content.py           # get_movie(), add_movie(), serial funksiyalar  
├── ads.py               # create_ad_campaign(), ad delivery/cleanup
├── stats.py             # get_dashboard_summary(), daily_metric_series
└── helpers.py           # _format_timestamp(), _placeholders(), utility
```

### Ishchi reja:

**1-qadam:** `database/__init__.py` yaratish — hozirgi barcha public funksiyalarni re-export qilish:
```python
# database/__init__.py
from .connection import DB_PATH, init_db, close_db
from .users import touch_user, is_admin_user, mark_user_blocked, ...
from .content import get_movie, add_movie, get_all_movies, ...
from .ads import create_ad_campaign, get_ad_campaign, ...
from .stats import get_dashboard_summary, get_daily_metric_series, ...
```
Bu qadamda **hech bir boshqa fayl o'zgarmaydi** — `from database import X` hali ham ishlaydi.

**2-qadam:** Funksiyalarni bir-bir ko'chirish (har birida test):
- `helpers.py` ← `_utc_now`, `_format_timestamp`, `_placeholders`, `local_now`, constants
- `connection.py` ← `db` global, `init_db`, `close_db`, `_get_db`, `_prepare_runtime_db`
- `cache.py` ← `movie_cache`, `serial_group_cache`, `fav_cache`, cache funksiyalar
- `migrations.py` ← `_ensure_users_tracking_columns`, `_ensure_movie_views_columns`, `_ensure_stats_event_tables`
- `users.py` ← user-related CRUD
- `content.py` ← movie/serial CRUD
- `ads.py` ← ad kampaniya CRUD
- `stats.py` ← statistika funksiyalar

**3-qadam:** Eski `database.py` faylni o'chirish

---

## 2.2 Monkey-patching ni yo'q qilish (22 ta override)
**Fayl:** `handlers/admin_text_refined.py` → `handlers/admin.py`  
**Vaqt:** 2-3 kun

### Hozirgi muammo:
```
admin.py ─── 4095 qator ───┐
                            │ admin_text_refined.py import qiladi
admin_text_refined.py ──────┤ va 22 ta funksiyani runtime'da almashtiradi
                            │
admin_runtime_helpers.py ───┘ admin.py oxirida import qilinadi (circular)
```

### Yangi arxitektura — **Protocol + DI (Dependency Injection)**:

```
handlers/
├── admin/
│   ├── __init__.py              # Router va public API
│   ├── router.py                # Barcha handler'lar
│   ├── permissions.py           # _is_admin, _ensure_access
│   ├── text.py                  # Barcha matn funksiyalar (bitta joy)
│   ├── content_management.py    # Kino/serial CRUD handlerlar
│   ├── stats_views.py           # Statistika handlerlar
│   ├── ads_views.py             # Reklama handlerlar
│   ├── helper_admins.py         # Yordamchi admin handlerlar
│   ├── channels.py              # Kanal handlerlar
│   └── states.py                # FSM states (hozirgi admin_states.py)
```

### Ishchi reja:

**1-qadam:** `handlers/admin/text.py` yaratish — **barcha** matn funksiyalarni bitta joyga yig'ish:
```python
# handlers/admin/text.py
def request_text(request_id, user_id, text): ...
def request_added_text(code, content_kind): ...
def request_rejected_text(request_text, request_id): ...
def content_list_filter_label(filter_key): ...
def render_helper_admins_panel(helper_admins): ...
# ... barcha 22 ta funksiya
```

**2-qadam:** `admin.py` ni bo'laklarga ajratish (bitta vaqtda bitta handler guruhini ko'chirish)

**3-qadam:** `admin_text_refined.py`, `admin_runtime_helpers.py` ni o'chirish

**4-qadam:** `admin_facade.py` ni yangilash yoki o'chirish (agar kerak bo'lmasa)

---

## 2.3 `handlers/admin.py` ni parchalash (4095 qator)
**Vaqt:** 2.2 bilan birgalikda — 2-3 kun

### Bo'linish jadvali:

| Yangi fayl | Tarkib | ~qator |
|------------|--------|--------|
| `admin/content_management.py` | Kino/serial qo'shish, tahrirlash, o'chirish | ~1200 |
| `admin/stats_views.py` | Dashboard, sparkline, statistika | ~400 |
| `admin/ads_views.py` | Reklama yaratish, boshqarish | ~500 |
| `admin/helper_admins.py` | Admin qo'shish, ruxsatlar | ~300 |
| `admin/channels.py` | Kanal boshqaruvi (hozirgi channel_fix.py bilan birlashtirish) | ~200 |
| `admin/permissions.py` | `_is_admin`, `_ensure_access` | ~100 |
| `admin/text.py` | Barcha matn generatsiya funksiyalar | ~600 |
| `admin/router.py` | Router va callback routing | ~300 |

---

## 2.4 Telegram object mutation ni to'xtatish
**Fayl:** `handlers/admin.py` (del_channel callback)  
**Vaqt:** ~2 soat

### Hozirgi (xavfli):
```python
callback.message.text = CHANNELS_BUTTON
old_from = callback.message.from_user
callback.message.from_user = callback.from_user
await admin_channels_menu(callback.message, state)
callback.message.from_user = old_from
```

### To'g'ri yo'l:
```python
# Funksiyani message'ga bog'lamaslik, 
# kerakli parametrlarni alohida uzatish:
await _refresh_channels_panel(
    message=callback.message,
    user_id=callback.from_user.id,
    state=state,
    edit=True,
)
```

---

## 2.5 Exception handling ni aniqlashtirish
**Barcha fayllar:** 19 ta `except Exception` topilgan  
**Vaqt:** ~3 soat

### Har bir holat uchun:

```python
# HOZIRGI:
except Exception:
    await message.answer_video(video=file_id, ...)

# KERAKLI:
except TelegramBadRequest:
    await message.answer_video(video=file_id, ...)
except TelegramForbiddenError:
    logger.warning("Foydalanuvchi botni bloklagan: %s", user_id)
```

### Tekshirish jadvali:
| Fayl | Qator | Holat |
|------|-------|-------|
| `advertising.py` | 120 | `TelegramBadRequest` ga almashtirish |
| `handlers/admin.py` | 3965 | `TelegramBadRequest` |
| `handlers/kino.py` | 33, 43, 93 | `TelegramBadRequest` |
| `handlers/admin_runtime_helpers.py` | 922 | `TelegramBadRequest` |
| `middlewares/forcesub.py` | 100 | `TelegramBadRequest, TelegramForbiddenError` |
| `middlewares/throttling.py` | 31 | `TelegramBadRequest` |

---

### 📊 FAZA 2 XULOSA

| Vazifa | Vaqt | Ta'sir |
|--------|------|--------|
| `database.py` parchalash | 2-3 kun | Maintainability 10x yaxshilanadi |
| Monkey-patching yo'q qilish | 2-3 kun | Debug qilish osonlashadi |
| `admin.py` parchalash | 2-3 kun | Kod navigatsiyasi yaxshilanadi |
| Object mutation tuzatish | 2 soat | Race condition xavfi yo'qoladi |
| Exception handling | 3 soat | Xatolarni aniq ushlash |
| **JAMI** | **~8-11 kun** | **Kod bazasi professional darajaga chiqadi** |

---

---

# 🟡 FAZA 3 — PERFORMANCE & INFRATUZILMA (2-3 hafta)

> **Maqsad:** Botni 10,000+ foydalanuvchiga tayyorlash va production-grade infratuzilma qurish.

---

## 3.1 SQL darajasida Pagination
**Fayl:** `database.py` (yangi `database/content.py`)  
**Muammo:** `get_all_movies()` 11 joyda ishlatiladi — har safar HAMMASI yuklanadi  
**Vaqt:** 1-2 kun

### Yangi funksiyalar:

```python
# database/content.py

async def count_movies_by_kind(content_kind: str) -> int:
    """SELECT COUNT(*) FROM movies WHERE content_kind = ?"""

async def get_movies_page(
    content_kind: str,
    page: int,
    page_size: int = 8,
) -> list[tuple[str, str, str]]:
    """SELECT code, title, content_kind FROM movies 
       WHERE content_kind = ? 
       ORDER BY code 
       LIMIT ? OFFSET ?"""

async def get_all_movie_codes() -> list[str]:
    """Faqat kodlarni qaytaradi — ro'yxat uchun"""
```

### O'zgartirish kerak bo'lgan joylar:
| Fayl | Funksiya | O'zgartirish |
|------|----------|-------------|
| `admin_runtime_helpers.py` | `_show_content_list` | `get_movies_page()` ishlatish |
| `admin_runtime_helpers.py` | `_show_delete_panel` | `get_movies_page()` ishlatish |
| `admin_text_refined.py` | `_show_content_list` | `get_movies_page()` ishlatish |
| `admin_text_refined.py` | `_show_delete_panel` | `get_movies_page()` ishlatish |
| `admin.py` | `movie_list` | `get_movies_page()` ishlatish |
| `main.py` | `main()` | `count_movies_by_kind()` ishlatish |

---

## 3.2 Cache tizimini qayta qurish
**Fayl:** `database.py` → yangi `database/cache.py`  
**Vaqt:** 1-2 kun

### Yangi `LRUCache` klassi:

```python
# database/cache.py
import time
from collections import OrderedDict

class TTLCache:
    """Thread-safe, hajm chegarali, TTL-li cache"""
    
    def __init__(self, maxsize: int = 2000, ttl_seconds: int = 300):
        self._store: OrderedDict[str, tuple[float, object]] = OrderedDict()
        self._maxsize = maxsize
        self._ttl = ttl_seconds
    
    def get(self, key: str) -> object | None:
        entry = self._store.get(key)
        if entry is None:
            return None
        timestamp, value = entry
        if time.monotonic() - timestamp > self._ttl:
            del self._store[key]
            return None
        self._store.move_to_end(key)
        return value
    
    def set(self, key: str, value: object) -> None:
        if key in self._store:
            self._store.move_to_end(key)
        self._store[key] = (time.monotonic(), value)
        while len(self._store) > self._maxsize:
            self._store.popitem(last=False)
    
    def invalidate(self, key: str) -> None:
        self._store.pop(key, None)
    
    def clear(self) -> None:
        self._store.clear()

# Cache instansiyalari
movie_cache = TTLCache(maxsize=5000, ttl_seconds=600)
serial_cache = TTLCache(maxsize=1000, ttl_seconds=600)
fav_cache = TTLCache(maxsize=10000, ttl_seconds=300)
user_cache = TTLCache(maxsize=5000, ttl_seconds=300)
schema_check_done: bool = False  # migration bir marta tekshiriladi
```

---

## 3.3 `_ensure_users_tracking_columns` ni bir marta ishlating
**Fayl:** `database.py`  
**Muammo:** Har safar `PRAGMA table_info` ishga tushadi  
**Vaqt:** ~2 soat

```python
# HOZIRGI: har safar tekshiriladi
async def get_pending_ad_recipients(ad_id, limit):
    has_block_tracking = await _ensure_users_tracking_columns(connection)
    ...

# KERAKLI: bir marta tekshiriladi
_schema_validated: bool = False

async def init_db():
    ...
    await _ensure_users_tracking_columns(connection)
    await _ensure_movie_views_columns(connection)
    await _ensure_stats_event_tables(connection)
    global _schema_validated
    _schema_validated = True

async def get_pending_ad_recipients(ad_id, limit):
    # _schema_validated allaqachon True — tekshirish kerak emas
    ...
```

---

## 3.4 Dashboard font cross-platform
**Fayl:** `dashboard.py`  
**Vaqt:** ~3 soat

```python
# HOZIRGI:
FONT_DIR = Path("C:/Windows/Fonts")

# KERAKLI:
import platform

def _discover_font_paths() -> tuple[list[Path], list[Path]]:
    """OS ga qarab font yo'llarini aniqlash"""
    system = platform.system()
    
    if system == "Windows":
        base = Path("C:/Windows/Fonts")
        regular = [base / "segoeui.ttf", base / "arial.ttf"]
        bold = [base / "seguisb.ttf", base / "segoeuib.ttf", base / "arialbd.ttf"]
    elif system == "Darwin":  # macOS
        base = Path("/System/Library/Fonts")
        regular = [base / "Helvetica.ttc", base / "SFNSText.ttf"]
        bold = [base / "Helvetica-Bold.ttf", base / "SFNSText-Bold.ttf"]
    else:  # Linux
        regular = [
            Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
            Path("/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf"),
            Path("/usr/share/fonts/TTF/DejaVuSans.ttf"),
        ]
        bold = [
            Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
            Path("/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf"),
            Path("/usr/share/fonts/TTF/DejaVuSans-Bold.ttf"),
        ]
    
    # Loyiha ichidagi fontlar — eng ishonchli
    local = Path(__file__).resolve().parent / "fonts"
    if local.is_dir():
        local_regular = list(local.glob("*-Regular.ttf")) + list(local.glob("*Regular.ttf"))
        local_bold = list(local.glob("*-Bold.ttf")) + list(local.glob("*Bold.ttf"))
        regular = local_regular + regular
        bold = local_bold + bold
    
    return regular, bold

FONT_REGULAR, FONT_BOLD = _discover_font_paths()
```

### Yaxshiroq yo'l: loyihaga font qo'shish
```bash
mkdir fonts/
# DejaVuSans.ttf va DejaVuSans-Bold.ttf ni fonts/ ga qo'yish
```

---

## 3.5 `webapp.py` ni async qilish
**Fayl:** `webapp.py` (3448 qator)  
**Vaqt:** 3-5 kun

### Migratsiya rejasi:

```python
# HOZIRGI: sinxron http.server
from http.server import HTTPServer, BaseHTTPRequestHandler

class SimpleHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        with sqlite3.connect(DB_PATH) as conn:
            ...

# KERAKLI: aiohttp
from aiohttp import web

async def handle_dashboard(request: web.Request) -> web.Response:
    async with aiosqlite.connect(DB_PATH) as db:
        ...
    return web.Response(body=html, content_type="text/html")

app = web.Application()
app.router.add_get("/", handle_dashboard)
app.router.add_get("/details/{metric}", handle_detail)
app.router.add_post("/auth-bootstrap", handle_auth)
```

### Qo'shimcha dependency:
```
# requirements.txt ga qo'shish:
aiohttp==3.11.18
```

---

## 3.6 Test infratuzilmasini qurish
**Vaqt:** 3-5 kun (asosiy testlar)

### Tuzilma:

```
tests/
├── conftest.py              # pytest fixtures
├── test_config.py           # Config yuklash testlari
├── test_database/
│   ├── test_users.py        # User CRUD testlari
│   ├── test_content.py      # Movie/serial testlari
│   └── test_ads.py          # Ad testlari
├── test_handlers/
│   ├── test_start.py        # /start handler test
│   ├── test_user.py         # User handler testlar
│   └── test_admin.py        # Admin handler testlar
├── test_middlewares/
│   ├── test_throttling.py   # AntiSpam test
│   └── test_forcesub.py     # Force sub testlar
└── test_services/
    ├── test_auth.py          # HMAC signing testlar
    └── test_legacy_media.py  # Media repair testlar
```

### `conftest.py` namunasi:

```python
import pytest
import aiosqlite
from pathlib import Path

@pytest.fixture
async def test_db(tmp_path):
    """Har bir test uchun toza database"""
    db_path = tmp_path / "test.db"
    # init_db() ni test database bilan chaqirish
    import database
    database.DB_PATH = db_path
    await database.init_db()
    yield database.db
    await database.close_db()

@pytest.fixture
def mock_bot():
    """aiogram Bot mock"""
    from unittest.mock import AsyncMock, MagicMock
    bot = MagicMock()
    bot.send_message = AsyncMock()
    bot.send_video = AsyncMock()
    return bot
```

### `requirements-dev.txt`:
```
pytest==8.3.5
pytest-asyncio==0.25.3
pytest-cov==6.1.1
ruff==0.11.12
mypy==1.15.0
```

---

## 3.7 Docker setup
**Vaqt:** 1-2 kun

### `Dockerfile`:

```dockerfile
FROM python:3.12-slim

WORKDIR /app

# Font o'rnatish (dashboard uchun)
RUN apt-get update && \
    apt-get install -y --no-install-recommends fonts-dejavu-core && \
    rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Database volume
VOLUME ["/app/data"]
ENV DB_PATH=/app/data/movies.db

CMD ["python", "main.py"]
```

### `docker-compose.yml`:

```yaml
version: "3.9"
services:
  bot:
    build: .
    env_file: .env
    volumes:
      - ./data:/app/data
    restart: unless-stopped

  webapp:
    build: .
    command: python webapp.py
    env_file: .env
    ports:
      - "8080:8080"
    volumes:
      - ./data:/app/data
    restart: unless-stopped
```

---

## 3.8 CI/CD (GitHub Actions)
**Vaqt:** ~1 kun

### `.github/workflows/ci.yml`:

```yaml
name: CI

on:
  push:
    branches: [main]
  pull_request:
    branches: [main]

jobs:
  lint:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - run: pip install ruff mypy
      - run: ruff check .
      - run: mypy --ignore-missing-imports .

  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - run: pip install -r requirements.txt -r requirements-dev.txt
      - run: pytest --cov=. --cov-report=term-missing
```

---

## 3.9 Dashboard render cache
**Fayl:** `dashboard.py`  
**Vaqt:** ~2 soat

```python
# dashboard.py oxiriga qo'shish:
_dashboard_cache: dict[str, tuple[float, bytes]] = {}
DASHBOARD_CACHE_TTL = 30  # sekund

def render_dashboard_cached(panel: str, payload: dict) -> bytes:
    """Bir xil payload uchun 30 soniya cache"""
    import hashlib, json, time
    
    key = hashlib.md5(
        f"{panel}:{json.dumps(payload, sort_keys=True)}".encode()
    ).hexdigest()
    
    cached = _dashboard_cache.get(key)
    if cached and time.monotonic() - cached[0] < DASHBOARD_CACHE_TTL:
        return cached[1]
    
    result = render_dashboard(panel, payload)
    _dashboard_cache[key] = (time.monotonic(), result)
    
    # Eski cache'larni tozalash
    if len(_dashboard_cache) > 20:
        cutoff = time.monotonic() - DASHBOARD_CACHE_TTL * 2
        _dashboard_cache.clear()
    
    return result
```

---

### 📊 FAZA 3 XULOSA

| Vazifa | Vaqt | Ta'sir |
|--------|------|--------|
| SQL pagination | 1-2 kun | 10x tezroq admin panel |
| Cache tizimi | 1-2 kun | Memory leak yo'qoladi |
| Schema check bir marta | 2 soat | DB query kamayadi |
| Cross-platform fonts | 3 soat | Linux'da ishlaydi |
| Webapp async | 3-5 kun | Concurrent requestlar |
| Test infratuzilma | 3-5 kun | Regression testlar |
| Docker setup | 1-2 kun | 1 buyruqda deploy |
| CI/CD | 1 kun | Avtomatik tekshiruv |
| Dashboard cache | 2 soat | CPU kamayadi |
| **JAMI** | **~14-20 kun** | **Production-ready bot** |

---

---

# 🟢 FAZA 4 — SIFAT VA KENGAYTIRISH (joriy ish)

> **Maqsad:** Loyihani uzun muddatga tayyor qilish va yangi imkoniyatlar qo'shish uchun infra qurish.

---

## 4.1 Migration tizimi
**Vaqt:** 2-3 kun

### Oddiy versioned migration:

```python
# database/migrations.py

MIGRATIONS = [
    # v1: boshlang'ich schema
    (1, """
        CREATE TABLE IF NOT EXISTS movies (...)
        CREATE TABLE IF NOT EXISTS users (...)
    """),
    # v2: is_blocked ustuni
    (2, """
        ALTER TABLE users ADD COLUMN is_blocked INTEGER NOT NULL DEFAULT 0;
        ALTER TABLE users ADD COLUMN blocked_at TEXT;
        CREATE INDEX IF NOT EXISTS idx_users_blocked ON users(is_blocked);
    """),
    # v3: content_view_events
    (3, """
        CREATE TABLE IF NOT EXISTS content_view_events (...);
    """),
]

async def run_migrations(connection):
    current = await _get_schema_version(connection)
    for version, sql in MIGRATIONS:
        if version > current:
            for statement in sql.strip().split(";"):
                if statement.strip():
                    await connection.execute(statement)
            await _set_schema_version(connection, version)
    await connection.commit()
```

---

## 4.2 Lokalizatsiya (i18n) infratuzilmasi
**Vaqt:** 3-5 kun

### Tuzilma:

```
locales/
├── uz.py     # O'zbek (default)
├── ru.py     # Rus
└── en.py     # Ingliz

# Har bir fayl:
# locales/uz.py
TEXTS = {
    "start_greeting": "👋 Assalomu alaykum!\n✨ PrimeCinema botiga xush kelibsiz!",
    "search_prompt": "🎬 Film kodini yuboring!",
    "not_found": "❌ Kontent topilmadi.",
    "favorites_empty": "❤️ Sevimlilar ro'yxati bo'sh.",
    ...
}
```

```python
# services/i18n.py
from locales import uz, ru

_LANGUAGES = {"uz": uz.TEXTS, "ru": ru.TEXTS}

def t(key: str, lang: str = "uz", **kwargs) -> str:
    texts = _LANGUAGES.get(lang, _LANGUAGES["uz"])
    template = texts.get(key, _LANGUAGES["uz"].get(key, key))
    return template.format(**kwargs) if kwargs else template
```

---

## 4.3 Structured logging
**Vaqt:** 1-2 kun

```python
# HOZIRGI:
logger.info("Bot ishga tushdi")
logger.warning("Telegram bilan aloqa uzildi: %s", error)

# KERAKLI (JSON structured):
import json
import logging

class JSONFormatter(logging.Formatter):
    def format(self, record):
        log_data = {
            "ts": self.formatTime(record),
            "level": record.levelname,
            "msg": record.getMessage(),
            "module": record.module,
            "func": record.funcName,
        }
        if record.exc_info:
            log_data["exception"] = self.formatException(record.exc_info)
        return json.dumps(log_data, ensure_ascii=False)
```

---

## 4.4 README.md yaratish
**Vaqt:** ~3 soat

```markdown
# 🎬 PrimeCinema Bot

Telegram kino/serial bot.

## O'rnatish
1. `cp .env.example .env` — sozlamalarni to'ldiring
2. `pip install -r requirements.txt`
3. `python main.py`

## Docker bilan
```bash
docker-compose up -d
```

## Arxitektura
...
```

---

## 4.5 Monitoring va healthcheck
**Vaqt:** 1-2 kun

```python
# healthcheck.py
async def check_health() -> dict:
    return {
        "status": "ok",
        "db_connected": db is not None,
        "uptime_seconds": time.monotonic() - start_time,
        "cache_sizes": {
            "movie": len(movie_cache),
            "fav": len(fav_cache),
        },
        "active_ads": len(_broadcasting_ads),
    }
```

---

## 4.6 Repository pattern to'liq implement
**Vaqt:** 2-3 kun

```python
# repositories/users.py — KERAKLI:
class UserRepository:
    def __init__(self, db: aiosqlite.Connection):
        self._db = db
    
    async def find_by_id(self, user_id: int) -> dict | None: ...
    async def touch(self, user_id: int, username: str, full_name: str): ...
    async def mark_blocked(self, user_id: int): ...
    async def is_admin(self, user_id: int) -> bool: ...
    async def get_active_count(self, days: int = 7) -> int: ...
```

---

### 📊 FAZA 4 XULOSA

| Vazifa | Vaqt | Ta'sir |
|--------|------|--------|
| Migration tizimi | 2-3 kun | Schema management |
| i18n infra | 3-5 kun | Ko'p tilli support |
| Structured logging | 1-2 kun | Monitoring qulayligi |
| README | 3 soat | Onboarding tezlashadi |
| Healthcheck | 1-2 kun | Uptime monitoring |
| Repository pattern | 2-3 kun | Clean architecture |
| **JAMI** | **~11-16 kun** | **Enterprise-grade kod** |

---

---

# 📅 UMUMIY VAQT JADVALI

```
Hafta 1 (1-3 kun):
├── ✅ FAZA 1 — Favqulodda tuzatishlar
│   ├── from_user None himoya
│   ├── message.text None himoya
│   ├── Security middleware yoqish
│   ├── movies.db Git'dan chiqarish
│   └── Cleanup (duplikat, .bak, bo'sh middleware)
│
Hafta 2-3:
├── 🔧 FAZA 2 — Arxitektura tozalash
│   ├── database.py → database/ paketi
│   ├── admin.py → admin/ paketi
│   ├── Monkey-patching yo'q qilish
│   └── Exception handling
│
Hafta 4-6:
├── ⚡ FAZA 3 — Performance & Infra
│   ├── SQL pagination
│   ├── Cache tizimi
│   ├── Docker + CI/CD
│   ├── Test infratuzilma
│   └── Webapp async migratsiya
│
Hafta 7+ (joriy):
└── 🌟 FAZA 4 — Sifat va kengaytirish
    ├── Migration tizimi
    ├── i18n
    ├── Monitoring
    └── Repository pattern
```

---

# 📐 METRIKALAR — MUVAFFAQIYAT KO'RSATKICHLARI

| Metrika | Hozirgi | Faza 1 dan keyin | Faza 3 dan keyin |
|---------|---------|------------------|------------------|
| Crash chastotasi | Noma'lum | ~0 critical | ~0 |
| Test coverage | 0% | 0% | 60%+ |
| Eng katta fayl | 4095 qator | 4095 qator | <500 qator |
| Docker support | ❌ | ❌ | ✅ |
| CI/CD | ❌ | ❌ | ✅ |
| Linux deploy | ❌ | ❌ | ✅ |
| Memory leak xavfi | Yuqori | O'rta | Past |
| `except Exception` soni | 19 | 19 | 0-3 |
| Monkey-patch | 22 ta | 22 ta | 0 |

---

# 🎯 BIRINCHI QADAM

**Bugun boshlang: FAZA 1.1 — `from_user` None himoyasi.** Bu eng ko'p crash keltiradigan bug. 2 soatda tuzatiladi, bot barqarorligi darhol oshadi.
