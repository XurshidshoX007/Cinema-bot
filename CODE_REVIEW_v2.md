# 🔍 Cinema-bot — Ikkinchi tekshiruv (hali qolgan kamchiliklar)

> Birinchi tekshiruvda tuzatilgan: `from_user` None (asosiy joylar), duplikat funksiya, Telegram object mutation, security middleware, `.gitignore`, dashboard fontlar, BOM  
> **Bu hujjat:** Birinchi tekshiruvdan keyin hali qolgan muammolar

---

## 🔴 CRASH BUGLAR (bot ishdan chiqadi)

### 1. `USER_ACTIONS` import qilinmagan — `NameError` crash
**Fayl:** `handlers/admin.py`, 4023-qator  
**Muammo:** `receive_new_channel` ichida `USER_ACTIONS` ishlatilgan, lekin import ro'yxatida yo'q  
**Natija:** Admin kanal qo'shganda bot `NameError: name 'USER_ACTIONS' is not defined` bilan crash qiladi
```python
# 4023-qator:
if message.text in ADMIN_ACTIONS + USER_ACTIONS:  # ← USER_ACTIONS import qilinmagan!
```
Import ro'yxatida faqat `ADMIN_ACTIONS` bor, `USER_ACTIONS` yo'q.

---

### 2. `handlers/user.py` — 7 ta callback handler'da `from_user` None crash
**Muammo:** Oldingi tuzatishda faqat `message` handler'lar himoyalangan, callback handler'larda hali qolgan:

| Qator | Handler | Xavfli kod |
|-------|---------|-----------|
| 1614 | `share_content_callback` | `await is_admin_user(callback.from_user.id)` |
| 1643 | `send_serial_share_to_channel` | `await is_admin_user(callback.from_user.id)` |
| 1753 | `favorites_page` | `callback.from_user.id` |
| 1770 | `history_page` | `callback.from_user.id` |
| 1815 | `serial_hub_page` | `callback.from_user.id` |
| 1892 | `serial_episode_open` | `callback.from_user.id` |
| 1901 | `serial_episode_open` | `callback.from_user.id` |

`touch_callback_user` endi `None` da return qiladi, lekin handler davom etib `callback.from_user.id` da crash qiladi.

---

### 3. `handlers/kino.py:100` — hali bitta `except Exception` qolgan
**Muammo:** `toggle_serial_favorite` oxiridagi katta try/except blokida:
```python
    except Exception:  # Qaysi exception? Nima yeyilmoqda?
        pass
```

---

## 🟠 DIZAYN MUAMMOLARI (bot ishlaydi, lekin noto'g'ri)

### 4. 24 ta Monkey-patching hali turadi
**Fayl:** `handlers/admin_text_refined.py`, oxirgi 24 qator  
**Muammo:** Runtime'da 24 ta funksiya almashtiriladi:
```python
admin._request_text = _request_text
admin._show_stats_webapp = _show_stats_webapp_restored
admin._show_content_list = _show_content_list
# ... yana 21 ta
```
**Xavfi:**
- Debug qilganda qaysi funksiya ishlayotganini tushunish qiyin
- Test yozib bo'lmaydi — import vaqtida funksiya boshqasi
- Bitta faylni o'zgartirish boshqa faylga ta'sir qiladi

---

### 5. 15 ta funksiya 3-4 marta aniqlangan (duplikat)
**Fayllar:** `admin.py`, `admin_runtime_helpers.py`, `admin_text_refined.py`

| Funksiya | Necha marta | Qaysi versiya ishlaydi |
|----------|-------------|----------------------|
| `_request_text` | 3x | `admin_text_refined` (monkey-patch) |
| `_render_content_section_page` | 4x | `admin_text_refined` (monkey-patch) |
| `_render_content_picker_text` | 4x | `admin_text_refined` (monkey-patch) |
| `_show_content_list` | 4x | `admin_text_refined` (monkey-patch) |
| `_show_serial_continue_prompt` | 4x | `admin_text_refined` (monkey-patch) |
| `show_requests` | 3x | `admin_text_refined` (monkey-patch) |
| `send_request_review` | 3x | `admin_text_refined` (monkey-patch) |
| ... va yana 8 ta | 3x har biri | |

Har bir funksiyaning 3-4 ta nusxasi bor. Faqat oxirgi monkey-patch qilingan versiya ishlaydi. Qolganlari o'lik kod.

---

### 6. `except Exception` — 17 ta joy (11 ta faylda)

| Fayl | Soni | Eng xavflisi |
|------|------|-------------|
| `advertising.py` | 4 | Reklama broadcast xatolarini yutib yuboradi |
| `handlers/admin.py` | 1 | Admin panel xatolarini yashiradi |
| `handlers/admin_text_refined.py` | 2 | Request review xatolarini yashiradi |
| `handlers/admin_runtime_helpers.py` | 1 | Request review xatolarini yashiradi |
| `handlers/kino.py` | 1 | Serial favorite xatolarini yashiradi |
| `middlewares/forcesub.py` | 1 | Obuna tekshiruv xatolarini yashiradi |
| `middlewares/throttling.py` | 1 | AntiSpam xatolarini yashiradi |
| `main.py` | 1 | Polling crash'ini yashiradi |
| `config.py` | 1 | JSON parse xatolarini yashiradi |
| `runtime_manager.py` | 2 | PID fayl xatolarini yashiradi |
| `stats_tunnel.py` | 2 | Tunnel xatolarini yashiradi |

**Xavfi:** Haqiqiy xatolar log'ga yozilmaydi, bug'larni topish imkonsiz bo'ladi.

---

## 🟡 SIFAT VA INFRATUZILMA MUAMMOLARI

### 7. 12 ta faylda `logger` yo'q
Handler'lar, database, config, keyboards — hech birida `logger = logging.getLogger(__name__)` yo'q.  
Xatolar faqat `except Exception: pass` bilan yutiladi, hech qayerga yozilmaydi.

| Fayl | Qatorlar | Logger bor? |
|------|----------|------------|
| `handlers/admin.py` | 4123 | ❌ |
| `database.py` | 3308 | ❌ |
| `handlers/admin_ui.py` | 159 | ❌ |
| `handlers/admin_runtime_helpers.py` | 955 | ❌ |
| `handlers/admin_text_refined.py` | 1024 | ❌ |
| `handlers/kino.py` | 101 | ❌ |
| `handlers/start.py` | 61 | ❌ |
| `handlers/channel_fix.py` | 116 | ❌ |
| `handlers/chat_member.py` | 28 | ❌ |
| `advertising.py` | 371 | ❌ |
| `config.py` | 168 | ❌ |
| `keyboards.py` | 859 | ❌ |

---

### 8. Cache tizimi — chegarasiz, TTL yo'q, memory leak xavfi
**Fayl:** `database.py`

| Cache nomi | Turi | Chegarasi | TTL | Xavf |
|-----------|------|-----------|-----|------|
| `movie_cache` | `dict[str, tuple]` | ❌ yo'q | ❌ yo'q | 100K kino = ~50MB |
| `serial_group_cache` | `dict[str, tuple]` | ❌ yo'q | ❌ yo'q | cheksiz o'sadi |
| `fav_cache` | `dict[int, set[str]]` | ❌ yo'q | ❌ yo'q | 100K user × 50 fav = katta |
| `user_activity_cache` | `dict[int, tuple]` | ❌ yo'q | ❌ yo'q | cheksiz o'sadi |
| `view_tracking_exclusion_cache` | `dict[int, bool]` | ❌ yo'q | ❌ yo'q | cheksiz o'sadi |

`user_activity_cache` faqat `AntiSpamMiddleware` kabi vaqtincha tozalanadi, lekin chegarasi yo'q.

---

### 9. Database: 33 ta `commit()` — hech biri `try/finally` ichida emas
**Fayl:** `database.py`  
**Muammo:** Agar 2 ta SQL buyruq orasida xato chiqsa, birinchi buyruq commit bo'lib, ikkinchisi bo'lmaydi — **partial commit** (yarim yozilgan data):
```python
await connection.execute("UPDATE ads SET delivered_total = ...")
await connection.execute("UPDATE ads SET failed_total = ...")
await connection.commit()  # ↑ birinchi UPDATE ishladi, ikkinchisi xato — nima bo'ladi?
```
`try/finally` yoki `async with connection:` context manager ishlatilishi kerak.

---

### 10. `webapp.py` — sinxron server, 13 ta sinxron DB chaqiruv
**Fayl:** `webapp.py` (3448 qator)

- `http.server.BaseHTTPRequestHandler` — **single-threaded**, bir vaqtda faqat 1 ta so'rov
- 13 ta `sqlite3.connect()` — har bir HTTP so'rov uchun yangi sinxron connection
- `0.0.0.0:8080` ga bind — tashqi tarmoqdan to'g'ridan-to'g'ri accessible
- POST `/actions/delete-blocked-users` — CSRF himoyasi yo'q

---

### 11. `admin.py` fayl oxirida import (circular dependency belgisi)
```python
# 4096-qator — fayl OXIRIDA import
from .admin_runtime_helpers import (
    _ad_duration_prompt,
    _build_ads_panel_text,
    ...
    send_request_review,
    show_requests,
)
```
Bu circular import muammosini "yashirish" uchun qilingan. Arxitektura muammosining belgisi — `admin.py` va `admin_runtime_helpers.py` bir-biriga bog'liq.

---

## 📊 UMUMIY RAQAMLAR

```
Loyiha holati:
├── 39 ta Python fayl, 20,526 qator
├── Eng katta fayllar:
│   ├── admin.py        — 4,123 qator
│   ├── webapp.py       — 3,448 qator
│   └── database.py     — 3,308 qator
│
├── 🔴 Crash buglar:        8 ta
├── 🟠 except Exception:   17 ta
├── 🟠 Monkey-patch:       24 ta
├── 🟠 Duplikat funksiya:  15 ta (3-4x)
├── 🟡 Logger yo'q:        12 ta fayl
├── 🟡 Cache chegarasiz:    5 ta dict
├── 🟡 commit() himoyasiz: 33 ta
└── 🟡 webapp sinxron:     13 ta DB call
```

---

## 🎯 ENG BIRINCHI TUZATISH KERAK

| # | Muammo | Qiyinligi | Crash qiladimi? |
|---|--------|-----------|----------------|
| 1 | `USER_ACTIONS` import qo'shish | 1 qator | ✅ Ha |
| 2 | `user.py` 7 ta callback `from_user` guard | 7 ta if/return | ✅ Ha |
| 3 | `kino.py` oxirgi `except Exception` | 1 qator almashtirish | ⚠️ Xato yashiriladi |
