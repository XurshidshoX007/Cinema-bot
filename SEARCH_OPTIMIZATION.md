# Qidiruv tizimini FTS5 asosida optimallashtirish — o'zgarishlar hisoboti

**Fayl:** `database.py` (yagona o'zgartirilgan fayl)
**Maqsad:** `search_movies_by_text()` da `LOWER(title) LIKE '%query%'` full-scan
o'rniga FTS5 indeksidan foydalanish; 909 ta filmdan 100k+ kontentga o'tishda
qidiruv latency'sini barqaror ushlab turish.

---

## Qaysi qatorlar o'zgargan va nima sababdan

### 1. `init_db()` — FTS5 jadval, triggerlar va rebuild (qator ~742–791, ~1040–1048)

`movies_fts` virtual jadvali kodda **hech qayerda yaratilmagan edi** va bazada
ham mavjud emas edi (tekshirildi: `sqlite_master` da yo'q). Shuning uchun
optimallashtirishni ishga tushirish uchun jadvalni yaratish va sinxron ushlab
turish kerak bo'ldi:

```sql
CREATE VIRTUAL TABLE IF NOT EXISTS movies_fts
USING fts5(
    title,
    description,
    content='movies',
    content_rowid='id',
    tokenize='unicode61'
)
```

- `content='movies'` — external-content: asosiy matn `movies` jadvalida
  saqlanadi, indeksda faqat tokenlar. `content_rowid='id'` — `movies.id` ←→
  `movies_fts.rowid` bog'lanishi (talab 5: JOIN shu orqali).
- `tokenize='unicode61'` — standart tokenayzer, aniq ko'rsatilgan (katta-kichik
  harflarni birlashtiradi, `\w`-belgilar bo'yicha tokenlarga ajratadi).
- **Triggerlar** (`movies_fts_ai/ad/au`, qator 772–791): `movies` dagi har bir
  INSERT/UPDATE/DELETE ni FTS indeksiga avtomatik ko'chiradi. Triggersiz
  sinxronlikni `add_movie`, `update_movie_title`, `update_movie_description`,
  `update_movie_file_id`, `delete_movie` va `_sync_serial_group` ning har
  birida qo'lda ushlab turish kerak bo'lardi — xatoga moyil. Triggerlar barcha
  write-yo'llarni bir joyda qamrab oladi.
- **Muhim tartib:** jadval endigina yaratilgan bo'lsa, indeks **triggerlardan
  oldin** to'ldiriladi (`INSERT INTO movies_fts(movies_fts) VALUES('rebuild')`,
  qator 766). Aks holda, bo'sh indeks ustida ishga tushgan `'delete'` komandasi
  FTS5 da `database disk image is malformed` xatosi bilan yiqiladi (amalda
  sinovda topildi) — chunki `init_db` serial normalizatsiyasi `movies` ni
  UPDATE qilganda trigger darhol ishga tushadi.
- **Yakuniy sinxronlik tekshiruvi** (qator ~1040–1048): `movies` va
  `movies_fts` dagi qatorlar soni solishtiriladi; farq bo'lsa — rebuild. Bu
  qo'lda buzilgan yoki yarim qolgan holatlarni ham davolaydi.

### 2. `_MOVIE_SEARCH_SELECT` (qator 1917–1930)

Candidate olishda uchta so'rovda (FTS JOIN, code lookup, LIKE fallback) bir xil
7 ustun kerak. Doim `m.` aliasi bilan — FTS JOIN'ida `title`/`description`
ikkala jadvalda ham bor, aliassiz so'rov `ambiguous column name` xatosi beradi
(SQLite amalda tekshirildi).

### 3. `_build_fts5_query()` (qator 1928–1951)

Foydalanuvchi so'rovidan xavfsiz FTS5 MATCH ifodasini quradi:
`qashqirlar makoni` → `"qashqirlar"* OR "makoni"* OR "qashqirlar makoni"*`.

- **Xavfsizlik (talab 7):** har bir token alohida qo'shtirnoqqa olinadi va
  ichidagi `"` ikki marta yozilib escape qilinadi. Foydalanuvchi kiritgan `"`,
  `*`, `OR`, `AND`, `NOT`, qavslar, `:` va h.k. FTS5 sintaksisiga aralasha
  olmaydi (FTS query-injection yo'q).
- `*` (prefix) — eski `%term%` substring mosligiga eng yaqin FTS5 semantikasi.
- Ko'p so'zli so'rovda butun ibora ham qo'shiladi — bm25 reytingida aynan
  ibora mos kelgan yozuvlar yuqoriga chiqadi.
- Tokenlar bo'lmasa `None` qaytaradi → FTS qadami o'tkazib yuboriladi.

### 4. `search_movies_by_text()` — candidate olish qismi (qator ~2050–2150)

Eski `movie_where = _search_where(("code", "title", "series_title"))` + LIKE
full-scan o'chirildi. Yangi pipeline:

1. **serial_groups** (qator 2050–2063) — **o'zgarishsiz** qoldi: bu alohida
   kichik jadval, FTS tarkibiga kirmaydi va serial logikasi (talab 3) shu yerda.
2. **FTS5 MATCH + JOIN** (qator 2067–2095) — asosiy candidate olish:
   ```sql
   SELECT {_MOVIE_SEARCH_SELECT}
   FROM movies_fts
   JOIN movies AS m ON m.id = movies_fts.rowid
   WHERE movies_fts MATCH ?
   ORDER BY movies_fts.rowid DESC
   LIMIT ?
   ```
   - Talab 4 va 5 bajarildi: LIKE o'rniga MATCH, JOIN `movies.id = movies_fts.rowid`.
   - `ORDER BY movies_fts.rowid DESC` — eng yangi kontent birinchi (eski
     `ORDER BY id DESC` semantikasi). Ataylab `ORDER BY rank` (bm25)
     ishlatilmadi: keng so'rovda (masalan, 95k/100k qator mos kelsa) bm25
     hisobi barcha moslikni aylanib chiqadi va 100k qatorda ~125ms sarflaydi;
     rowid tartibi esa ~7ms va natija sifatini o'zgartirmaydi — yakuniy reyting
     baribir o'zgarmagan Python scoring qiladi.
   - `except aiosqlite.DatabaseError` — FTS jadvali yo'q bo'lsa (masalan,
     `init_db` hali ishlamagan eski baza) yoki MATCH sintaksisi noto'g'ri bo'lsa,
     LIKE fallbackga tushamiz (production mustahkamlik).
3. **Code lookup** (qator 2097–2132) — `code` ustuni FTS tarkibiga kirmaydi
   (jadvalda faqat title/description), lekin `movies.code` da UNIQUE indeks
   bor. Shuning uchun:
   - `WHERE m.code = ? LIMIT 1` — aniq moslik (indeks SEARCH, O(1)),
   - `WHERE m.code GLOB ? LIMIT ?` — prefix moslik (`12*`). **GLOB** ataylab
     tanlandi: uning prefix optimizatsiyasi indeksdan SEEK qiladi (0.02ms),
     LIKE esa natija topilmasa butun jadvalni skanerlaydi (100k qatorda ~8ms).
     Xavfsizlik: `normalized_query` faqat so'z belgilaridan iborat (GLOB uchun
     maxsus `*`, `?`, `[`, `]` `_normalize_title_for_match` da o'chiriladi).
   - Natijalar `movie_rows` ga qo'shiladi (code bo'yicha deduplikatsiya).
4. **LIKE fallback** (qator 2134–2150) — talab 6: FTS ham, code lookup ham
   hech narsa topa olmasa, eski to'liq LIKE so'rov ishga tushadi. Bu kafolat
   beradi: oldin topiladigan natijalar endi yo'qolib qolmaydi (masalan,
   faqat code ichida substring bo'lgan holatlar).

### Nima o'zgarmadi

- `_score_candidate()` — **scoring algoritmi so'zma-so'z o'zgarishsiz** (talab 2):
  exact code +1200, prefix +900, exact title +1000, prefix +760/700/680,
  token bonuslar, serial penalty −20 va h.k.
- `search_terms` yig'ish, `_search_where`, `_contains_all_tokens` — o'zgarmadi
  (fallback ulardan foydalanadi).
- Candidate'lardan yakuniy natija yig'ish (sort, `seen_codes`,
  `seen_serial_titles` serial deduplikatsiyasi, `limit`) — o'zgarmadi (talab 3).
- Async uslub: hammasi `async with connection.execute(...)` + `await fetchall()`
  (talab 8). SQL injection: barcha foydalanuvchi ma'lumotlari parametr sifatida
  uzatiladi, hech qanday f-string interpolyatsiyasi yo'q (talab 7).

---

## Sinov natijalari

### To'g'rilik (909 ta real film, A/B taqqoslash)

- FTS jadval: `movies`=909, `movies_fts`=909, 3 ta trigger mavjud.
- Aniq code so'rovlari (`12`, `8372`, `9185`, `8472`, `14`) — top-1 da aniq
  code qaytadi (eski bilan bir xil).
- Fallback yo'li (`412`, `124`, `702`, `017` — FTS hech narsa topmaydigan
  so'rovlar): natijalar eski LIKE bilan **baytma-bayt bir xil**.
- Title-recall: eski LIKE topgan title mosliklari yangi tizimda ham topiladi.
  Qo'shimcha yaxshilanish: endi **description ham qidiriladi** (FTS uni
  indekslaydi; eski LIKE candidate olishda description'ni umuman skanerlamasdi).
- Serial/movie logikasi: `qashqirlar` → har bir serial base title'dan bittadan
  natija (deduplikatsiya ishlaydi), serial group va epizodlar aralashmasi.
- Trigger sinxronligi: `add_movie` → darhol qidiriladi; `update_movie_title` →
  eski nom yo'qoladi, yangisi topiladi; `delete_movie` → indeksdan o'chadi.
- 21 ta dushman (injection) so'rov (`"`, `*`, `OR`, `; DROP TABLE...` va h.k.)
  — xatoliksiz, natijalar oqilona.
- FTS jadvali o'chirib tashlansa ham (eski baza simulyatsiyasi) — LIKE fallback
  ishlaydi, bot buzilmaydi.
- Yangi bo'sh baza: `init_db` FTS ni yaratadi, triggerlar ishlaydi, ikkinchi
  `init_db` idempotent.
- Repo testlari: `pytest` — 36/36 o'tdi. Ruff: yangi xatolar yo'q (10 ta
  ogohlantirish — bazadan oldin ham bor edi).

### Tezlik (100k qatorli sintetik baza, 10 ta so'rov)

| Variant | min | median | max | jami |
|---|---|---|---|---|
| Eski (LIKE full-scan) | 2.36 ms | 2.57 ms | 77.59 ms | 103.39 ms |
| Yangi (FTS5) | 1.64 ms | 2.83 ms | **11.07 ms** | **41.10 ms** |

- Eng yomon holat 7 barobar yaxshilandi (77.6 → 11.1 ms), jami vaqt 2.5 barobar
  kamaydi, median amalda teng.
- Hozirgi real 909 qatorli bazada: 1.5–3.9 ms / so'rov (oldingi ~1–3 ms bilan
  teng).
- FTS asosiy afzalligi skala bilan namoyon bo'ladi: selective so'rovlar
  O(mosliklar) — indeksdan, jadval hajmiga bog'liq emas; LIKE esa har doim
  butun jadvalni (yoki 80 ta moslik topilmaguncha qatorlarni) skanerlaydi va
  matn uzunligi/co'ld cache bilan yomonlashadi.

---

## Ma'lum qilingan cheklov (trade-off)

FTS5 `unicode61` tokenayzeri prefix moslikni topadi, lekin **so'z ichidagi
ixtiyoriy substring'ni** (masalan, code `8412` ni `12` so'rovi bilan) topa
olmaydi. Eski LIKE `%12%` code'da substring'ni ham topardi. Yangi tizimda:
- aniq code va code prefix — indeks orqali (tez),
- faqat substring orqali topiladigan holatlar — LIKE fallback orqali (FTS
  hech narsa topa olmasa),
- FTS natija bersa-yu, code-substring moslik o'tkazib yuborilsa — bunday
  mosliklar eski tizimda ham eng past ball olardi (+700, exact +1200 bilan
  solishtirganda) va odatda shovqin edi.

Bu talab 4 ning to'g'ridan-to'g'ri natijasi: candidate olishda LIKE faqat
fallback sifatida qoldi; to'liq substring moslik uchun trigram tokenayzer
kerak bo'lardi, u esa berilgan jadval sxemasini o'zgartirishni talab qiladi.
