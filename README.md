# 🎬 PrimeCinema Bot

Telegram orqali kino va seriallarni boshqarish, tarqatish va statistikasini yuritish boti.

## Imkoniyatlar

- 🔎 Kod orqali tezkor kino/serial qidiruv
- 📺 Serial qismlarini ketma-ket boshqarish
- ❤️ Sevimlilar va ko'rish tarixi
- 📝 Foydalanuvchi so'rovlari
- 📣 Reklama kampaniyalari (broadcast + avtomatik o'chirish)
- 📡 Majburiy obuna (force subscription)
- 📊 Statistika dashboard (PIL rasm + web panel)
- 👥 Ko'p adminli boshqaruv tizimi
- 🔗 Inline ulashish

## O'rnatish

### 1. Repozitoriyani klonlash

```bash
git clone https://github.com/XurshidshoX007/Cinema-bot.git
cd Cinema-bot
```

### 2. Virtual muhit va kutubxonalar

```bash
python -m venv .venv
source .venv/bin/activate   # Linux/macOS
# .venv\Scripts\activate    # Windows

pip install -r requirements.txt
```

### 3. Sozlamalar

```bash
cp .env.example .env
```

`.env` faylini oching va to'ldiring:
- `BOT_TOKEN` — @BotFather dan olingan token
- `ADMIN_ID` — Sizning Telegram User ID

### 4. Ishga tushirish

```bash
python main.py
```

## Docker bilan ishga tushirish

```bash
docker-compose up -d
```

## Loyiha tuzilmasi

```
Cinema-bot/
├── main.py                 # Bot entry point
├── config.py               # Sozlamalar (.env)
├── database.py             # SQLite ORM (aiosqlite)
├── keyboards.py            # Telegram keyboard layout'lar
├── advertising.py          # Reklama broadcast tizimi
├── dashboard.py            # PIL bilan statistika rasm
├── webapp.py               # Statistika web panel
│
├── handlers/               # Telegram handler'lar
│   ├── start.py            # /start buyruq
│   ├── user.py             # Foydalanuvchi funksiyalari
│   ├── admin.py            # Admin boshqaruvi
│   ├── admin_texts.py      # Admin matn generatsiya
│   ├── admin_ui.py         # Admin menyu routing
│   ├── admin_states.py     # FSM state'lar
│   ├── channel_fix.py      # Kanal boshqaruvi
│   ├── chat_member.py      # Block/unblock tracking
│   ├── inline.py           # Inline qidiruv
│   └── kino.py             # Sevimli toggle
│
├── middlewares/             # Middleware'lar
│   ├── forcesub.py         # Majburiy obuna
│   ├── security.py         # Input sanitizatsiya
│   └── throttling.py       # Anti-spam
│
├── services/               # Yordamchi servislar
│   ├── channel_service.py  # Kanal formatting
│   ├── legacy_media.py     # Eski bot media import
│   ├── stats_webapp_auth.py# HMAC URL signing
│   ├── telegram_context.py # User context sync
│   └── user_views.py       # Presentation helper
│
├── requirements.txt
├── Dockerfile
├── docker-compose.yml
└── .env.example
```

## Texnologiyalar

- **Python 3.11+**
- **aiogram 3.x** — Telegram Bot API
- **aiosqlite** — Async SQLite
- **Pillow** — Dashboard rasm generatsiya

## Litsenziya

MIT
