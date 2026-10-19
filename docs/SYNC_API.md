# Hushyor AI — Sync API v1 (edge ↔ bulut shartnomasi)

Do'kon kompyuteridagi **edge agent** (`app/sync.py`) va markaziy **bulut** (`cloud/server.py` + `cloud/db.py`:
SQLite yoki PostgreSQL/TimescaleDB, kompaniya → filial → kamera, rollar) orasidagi protokol. Versiya URL'da: `/api/v1/...`.
Edge tomoni o'zgarmasdan bulutni almashtirish mumkin — shartnoma shu hujjat.

## Umumiy qoidalar

| Qoida | Tafsilot |
|---|---|
| Transport | HTTPS (VPS'da) yoki HTTP (bitta kompyuter / lokal tarmoq). JSON `utf-8`. |
| Autentifikatsiya | Har so'rovda `Authorization: Bearer <EDGE_TOKEN>`, `X-Device-Id: <device_id>`, `X-Api-Version: 1`. Token xato → **401**; qurilma identifikatori `[A-Za-z0-9_-]{1,64}` bo'lmasa → **400**. Token — filialning **qurilma tokeni** (bazada hash; `POST /api/v1/branches` da bir marta ko'rsatiladi), faqat o'sha `device_id` bilan ishlaydi. Eski usul `CLOUD_EDGE_TOKENS` (muhit) — faqat bazada o'z tokeni bo'lmagan qurilmalar uchun (avtomatik "env" filial, ko'pi bilan 20 ta); bazadagi filialni muhit tokeni bilan egallab bo'lmaydi. Superadmin — alohida `CLOUD_ADMIN_TOKEN`. |
| Idempotentlik | Har yozuvda `key` (edge beradi). Takror **xato emas**: hodisa `uid` bo'yicha upsert (`updated_at` eskiroq bo'lsa ustiga yozilmaydi), ko'rsatkich `(device, minute, source)` bo'yicha almashtiriladi, kamera holati kaliti bo'yicha `INSERT OR IGNORE`, klip — hodisada fayl bo'lsa `duplicate: true`. Server qo'shimcha `received` jurnalini (qurilma, tur, kalit; 7 kun) tekshirish uchun yuritadi. |
| Tartib | Edge faqat bitta tartibni kafolatlaydi: `clip:N` o'z hodisasi `ev:N` yetgandan keyin. Boshqa qatorlar bir-biriga bog'liq emas — kutayotgan qator (masalan, katta klip) boshqalarni to'smaydi. Klip hodisadan oldin kelsa server **409** qaytaradi, edge hodisani qayta navbatga qo'yib klipni orqasiga ko'chiradi (ko'pi bilan 3 marta). |
| Xatolar | **401/403** — edge yuborishni to'xtatadi (panelda "token xato"), 5 daqiqada bir qayta tekshiradi. **400/413/415/422** — yozuv `failed` (rad etilgan) deb belgilanadi, navbat davom etadi; partiya 413 bo'lsa avval ikkiga bo'linadi. **5xx / 429 / 404 / 405 / tarmoq** — vaqtinchalik: 5 s → 5 daqiqa eksponensial kutish bilan qaytadan, cheksiz (klip — 50 urinishgacha, keyin `failed`). Yetkazilmagan qator 30 kundan keyin tashlanadi. |
| Javob shakli | Muvaffaqiyat: `{"ok": true, ...}`. Xato: FastAPI `{"detail": "..."}` (4xx) yoki `{"ok": false, "detail": "..."}` (5xx). |
| Hajm | Bitta so'rovda ≤ 500 yozuv (edge 50 tadan yuboradi), JSON ≤ 8 MB, bitta hodisa ≤ 200 KB. Klip ≤ 60 MB, eskiz ≤ 5 MB. `Content-Length` shart (chunked tana → **411**); chegaradan katta → **413** tana o'qilmasdan. Teskari proksi (nginx) bo'lsa: `client_max_body_size 70m;`. |
| Vaqt | Hamma vaqt — unix soniya (float). `hello` javobidagi `server_time` bilan edge soat farqini o'lchaydi. |

## `POST /api/v1/edge/hello` — heartbeat (har 60 s va har ulanishda)

So'rov:
```json
{"device_id": "a1b2c3d4e5f6", "name": "Korzinka Chilonzor", "version": "1.5.0", "time": 1760400000.12,
 "cameras": [{"id": 1, "name": "Kassa 1", "kind": "rtsp", "state": "ishlayapti", "running": true}],
 "pending": 12}
```
Javob: `{"ok": true, "server_time": 1760400000.40, "device": {"id": "a1b2c3d4e5f6"}, "commands": []}`

`commands` — bulut → edge buyruqlari. Hozir bitta tur: **`review`** — bulut panelida ✅/❌ bosildi:
`{"id": 7, "type": "review", "local_id": 12, "uid": "...:12", "status": "rad_etilgan", "note": "…", "by": "Bulut: Qorovul", "t": …}`.
Edge lokal hodisani yangilaydi (Telegram xabari ham), keyin **`POST /api/v1/edge/commands/ack`** `{"ids": [7]}` yuboradi.
Ack kelmasa server buyruqni 2 daqiqadan keyin yana beradi; edge takrorni holat bo'yicha o'zi aniqlaydi, va lokal
qaror buyruqdan **yangiroq** bo'lsa (qo'riqchi Telegram'da keyinroq bosgan) buyruqni qo'llamaydi. 1.5.0 edge
buyruqlarni o'qimaydi — bulutdagi tekshirish do'konga yetmaydi, buyruqlar 7 kun saqlanib tozalanadi.
`device.branch_id` va `device.name` — bulutdagi filial.

## `POST /api/v1/edge/events` — hodisalar (yangi va yangilangan)

```json
{"device_id": "a1b2c3d4e5f6", "items": [
  {"key": "ev:12", "uid": "a1b2c3d4e5f6:12", "local_id": 12, "device_id": "a1b2c3d4e5f6", "updated_at": 1760400001.5,
   "created_at": 1760399990.2, "t_detect": 1760399990.2, "source_id": 1, "source_name": "Kassa 1",
   "kind": "pocket", "score": 72.0, "level": "o'rta", "status": "yangi", "reasons": ["..."], "breakdown": {...},
   "reviewed_by": null, "reviewed_at": null, "note": null, "claimed_by": null,
   "clip_path": "ev00012_20261014_101530.mp4", "thumb_path": "...jpg", "has_clip": true, "clip_error": 0,
   "susp_start": 1760399988.1, "susp_end": 1760399990.2, "clip_start": 1760399978.1, "ml_prob": 0.61, ...}
]}
```
Javob: `{"ok": true, "accepted": ["ev:12"], "rejected": [{"key": "ev:13", "reason": "uid noto'g'ri"}]}`

`kind` (2.2.0): o'g'irlik — `pocket`, `torso`, `bag`, `offscreen`, `exit`; xavfsizlik — `fall`, `lying`, `night`, `crowd`,
`blocked`, `fire`, `fight` (2.3.0) (ball doim 100; bulut ularni egasining `min_score` chegarasiga qaramay yuboradi, matnida "ayblov
emas" yozuvi yo'q — docs/SAFETY.md). Yangi API yoki `features` kerak emas.

Qoidalar: `uid` = `<device_id>:<local_id>` va shu qurilmaniki bo'lishi shart. Bitta hodisa holati o'zgarganda
(✅/❌, izoh, klip tayyor) **o'sha `key` bilan** to'liq yozuv qayta yuboriladi — server upsert qiladi.

## `POST /api/v1/edge/events/{uid}/clip` — klip fayli (multipart)

Maydonlar: `meta` (JSON matn: `{"device_id", "local_id"}`), `clip` (video/mp4), `thumb` (image/jpeg, ixtiyoriy).
Javob: `{"ok": true, "bytes": 1834021}`; takror bo'lsa `{"ok": true, "duplicate": true}`.
Xatolar: **409** hodisa hali yo'q; **413** katta fayl; **400** bo'sh fayl / `uid` noto'g'ri.

## `POST /api/v1/edge/metrics` — daqiqalik ko'rsatkichlar

```json
{"device_id": "...", "items": [
  {"key": "m:1760400000:1", "minute": 1760400000, "source_id": 1, "source_name": "Kassa 1",
   "persons_avg": 1.4, "persons_max": 3, "fps_ai": 6.8, "active_pct": 0.35, "up_pct": 1.0, "alerts": 0},
  {"key": "m:1760400000:0", "minute": 1760400000, "source_id": 0, "source_name": "tizim",
   "cpu": 41.0, "ram": 63.5, "extra": "{\"outbox\": 0, \"rss_mb\": 812.3}"}
]}
```
`source_id = 0` — tizim qatori. Server `(device_id, minute, source_id)` bo'yicha almashtiradi.

**1.9.0 — odam sanash.** Kamerada sanash chizig'i bo'lsa, shu daqiqada o'tish bo'lgan kamera qatorida qo'shimcha
maydonlar keladi (o'tish bo'lmagan daqiqada — maydonlar yo'q, eski bulut ularni e'tiborsiz qoldiradi):

| Maydon | Ma'nosi |
|---|---|
| `in`, `out` | xaridorlar: kirdi / chiqdi (xodim va ish vaqtidan tashqari o'tishlar kirmaydi) |
| `staff_in`, `staff_out` | xodim deb aniqlanganlar (xodim zonasida turgan yoki keyin xodim bo'lib chiqqan iz) |
| `offhours` | ish vaqtidan tashqari o'tishlar (xodim emas), jami |
| `off_in`, `off_out` | ... yo'nalish bo'yicha — "hozir ichkarida" zaxira hisobi uchun (yopilgandan keyin chiqqanlar) |

Odam o'tgandan keyin xodim deb aniqlansa (masalan, peshtaxta ortiga o'tdi), o'tish daqiqa yopilgandan keyin yozilsa
(AI navbati kechikdi) yoki edge daqiqa o'rtasida to'xtagan bo'lsa (keyingi ishga tushishda), edge o'sha **o'tgan
daqiqa** qatorini qayta hisoblab, xuddi shu `key` bilan yana yuboradi — server almashtiradi, yig'indi ikki marta
sanalmaydi. Bulut sanash maydonlarini alohida `visits_minute` jadvaliga ham yozadi (hisobotlar SQL'da yig'iladi);
sanashsiz qayta kelgan qator u yerdan o'chiriladi.

`POST /api/v1/edge/hello` ga 1.9.0 da `"occupancy": {"inside", "since", "day0", "in", "out"}` qo'shildi (sanash
chizig'i bo'lsa): edge'dagi "hozir ichkarida" (qo'lda "0 qilish" va do'kon yopiq payti hisobga olingan). Bulut uni
10 daqiqa ichida kelgan bo'lsa ko'rsatadi, aks holda — bugungi (kirdi + tashqari kirdi) − (chiqdi + tashqari chiqdi).

## 2.0.0 — kassa navbati

**Imkoniyatlar (`features`).** 2.0.0 bulut hello javobiga `"features": ["queues"]` qo'shadi. Edge buni
`settings.cloud_features` ga yozadi va navbat yozuvlarini (`queue`, `qalert`) **faqat shundan keyin** outbox'ga
qo'yadi. Sabab: outbox tartib bo'yicha yuboriladi — eski bulut bilmagan yo'lga (404) yozuv qo'yilsa, uning ortidagi
hodisalar ham to'xtab qolardi. Eski bulut bilan navbat ma'lumoti faqat do'kon kompyuterida qoladi (Navbat sahifasi
va Telegram ishlayveradi).

**Hello.** Navbat zonasi bo'lsa: `"queues": [{"zone_id", "name", "source_name", "length", "max_wait", "cashier",
"state", "live"}]` — har kassa hozir. `state`: `navbat` (≥ me'yor) | `band` | `bosh` | `yopiq` (kassir yo'q, navbat
yo'q) | `kassir_yoq` (navbat bor, kassir yo'q) | `nomalum` (kamera ishlamayapti, `live: false`). `cashier`: `true` /
`false` / `null` (kassir joyi chizilmagan). Bulut `branches.queues = {"at", "items"}` ga yozadi (≤ 64 ta, qiymatlar
tozalanadi), 3 daqiqadan eskisi "aloqa yo'q" deb ko'rsatiladi.

### `POST /api/v1/edge/queues` — daqiqalik yig'indi (har kassa)
```json
{"device_id": "...", "items": [
  {"key": "qm:1760400000:11", "minute": 1760400000, "zone_id": 11, "zone_name": "1-kassa", "source_id": 3,
   "t_obs": 60.0, "len_sum": 182.5, "len_max": 5, "long_sec": 31.0, "busy_sec": 60.0, "cashier_sec": 60.0,
   "cashier_known": 1, "served": 2, "wait_sum": 241.5, "wait_n": 2, "wait_max": 151.0}
]}
```
`t_obs` — kuzatilgan soniyalar; `len_sum` — uzunlik × soniya (o'rtacha = `len_sum / t_obs`); `long_sec` — navbat ≥
me'yor (`queue_max_len`); `busy_sec` — navbatda ≥ 1 kishi; `served`, `wait_*` — shu daqiqada navbatdan chiqqanlar
(≥ 5 s kutgan). Kechikkan qism (odam 8 s ko'rinmay ketgani keyin aniqlandi) edge'da o'sha daqiqaga qo'shilib, butun
qator **xuddi shu `key` bilan** qayta yuboriladi — server `(branch, minute, zone_id)` bo'yicha almashtiradi.

### `POST /api/v1/edge/queue_alerts` — qoida signallari (1-4)
```json
{"device_id": "...", "items": [
  {"key": "qa:17", "local_id": 17, "t": 1760400030.5, "rule": "R1", "level": "warning", "source_id": 3,
   "source_name": "Kassa", "zone_id": 11, "zone_name": "1-kassa", "title": "Qo'shimcha kassa oching",
   "text": "1-kassa: navbatda <b>5 kishi</b> (40 s dan beri) …", "data": {"reason": "len", "length": 5}}
]}
```
`rule`: `R1` qo'shimcha kassa oching | `R2` kassani tekshiring | `R3` qo'shimcha kassir kerak emas | `R4` kassir jadvali.
Server `(device_id, local_id)` bo'yicha bir marta yozadi; matndagi teglar olib tashlanadi (oddiy matn). Yangi va
10 daqiqadan eski bo'lmagan signal — bildirishnoma (`queue` sozlamasi: menejer va ega — yoqilgan, qo'riqchi —
o'chirilgan; R3 — `queue_low`, sukut bo'yicha hech kimga). **R5 — tarmoq signali** bulutda: 15 daqiqa ichida
kompaniyaning ≥ 2 filialida R1/R2 bo'lsa — egalarga bitta xabar (30 daqiqada ko'pi bilan bir marta).

### `GET /api/v1/queues?days=14&branch_id=` (panel)
Filiallar: `live` (hello), `today` (o'tdi, o'rtacha/eng uzoq kutish, eng uzun navbat, uzun navbat daqiqalari, o'rtacha
uzunlik), `hours_len`, `hours_served`; `days` — kunlar bo'yicha; `alerts` — oxirgi 50 ta (filial doirasida; tarmoq
signali faqat egaga). Bulut boti: `/navbat`.

## 2.1.0 — oqim va heatmap

**Imkoniyat.** 2.1.0 bulut hello javobida `"features": ["queues", "flow"]`. Edge `flow` yozuvlarini faqat shundan
keyin outbox'ga qo'yadi (eski bulut bilan navbat to'xtamaydi).

**Hello.** Do'kon rejasi bo'lsa: `"flow_plans": [{"id", "name", "w_m", "h_m", "cell", "nx", "ny", "grid",
"sections": [{"id", "name", "color", "pts": [[x, y], ...]}]}]` — metrda (x o'ngga, y pastga; rejaning chap yuqori
burchagidan). Reja rasmi yuborilmaydi. Bulut `branches.flow_plans = {"at", "items"}` (≤ 5 reja, ≤ 60 bo'lim).

### `POST /api/v1/edge/flow` — soatlik yig'indi (har reja)

`{"items": [{"key": "fh:<reja>:<soat>", "plan_id", "hour" (unix, soat boshi), "grid": "40x24@0.5", "nx", "ny",
"cell", "obs" (kamera-soniya), "visits": {"<bo'lim id>": n}, "moves": [[dan, ga, n], ...],
"occ": {"<katak>": odam·soniya}, "pas": {"<katak>": o'tgan odamlar}}]}`. Katak = `y·nx + x`. Edge joriy soatni har
daqiqada to'liq qayta yuboradi; bulut `(branch_id, plan_id, hour)` bo'yicha almashtiradi, faqat yangisi kattaroq
bo'lsa (`visits` va `obs` kamaymaydi — kechikib kelgan eski nusxa ustiga yozilmaydi). Soat boshi bo'lmagan `hour`,
noto'g'ri `grid` — rad etiladi (`rejected`), navbat davom etadi. Edge bitta soatda ko'pi bilan 5000 katak yuboradi.

### `GET /api/v1/flow?days=7&branch_id=&plan_id=` (panel)

Filial rejasi, bo'limlar bo'yicha tashriflar (davr), yo'nalishlar (eng ko'p 20), bugun soatlar bo'yicha, kunlar bo'yicha,
heatmap (`occ`, `pas`: `{"max", "total", "data": base64 uint8 (ny × nx, 0..255)}`) — faqat joriy `grid` dagi soatlar
(reja masshtabi o'zgarsa eski kataklar aralashmaydi). Rol doirasi boshqa API'lar kabi.

## 2.5.0 — tayyorlik va pilot (hello)

Edge hello'ga `"readiness": {"ok", "total", "bad": [nomlar], "warn": [nomlar], "at", "pilot": {"since", "until",
"days"}}` qo'shiladi (app/pilot.py, 10 daqiqa kesh; eski bulut e'tiborsiz qoldiradi). Bulut tozalaydi (≤ 8 ta nom,
har biri ≤ 80 belgi, sonlar chegarada) va `branches.readiness` ga `{..., "at": <bulut vaqti>}` qilib yozadi; maydonsiz
hello oxirgi holatni o'chirmaydi. `GET /api/v1/branches` — har filialda `readiness` (yoki `null`).

## 2.4.0 — taqqoslash va haftalik xulosa

Edge yangi yozuv yubormaydi — hammasi mavjud `metrics` (tashriflar va har daqiqadagi tizim qatori `source_id = 0`),
`queues` va `events` dan hisoblanadi.

### `GET /api/v1/compare?period=week|day&date=YYYY-MM-DD` (panel: egasi, menejer)

Davr `CLOUD_TZ_OFFSET` bo'yicha (hafta — dushanba–yakshanba); `date` berilmasa — oxirgi tugagan kun / hafta;
kelajakdagi davr yoki noto'g'ri sana — **400**; qo'riqchi — **403**. Javob: `period`, `t0`, `t1`, `label`,
`partial` (davr hali tugamagan), `branches` — har filial `{"id", "name", "version", "online", "visits": {"in",
"prev_in", "change_pct"}, "queue": {"served", "avg_wait", "max_wait", "long_sec"} | null, "theft": {"total",
"confirmed", "rejected", "unreviewed", "false_share"}, "safety": {"total", "unreviewed"}, "uptime": 0..1 | null}`
(tashrif bo'yicha kamayish tartibida; solishtirish — bir hafta oldingi xuddi shu davr; video tahlili hodisalari
kirmaydi), `total`, `recs` — `[{"key", "level": "warn"|"info", "text"}]`. Batafsil: docs/REPORTS.md.

**Haftalik xulosa** — bildirishnoma turi `digest` (sozlama `digest`: sukut egasi va menejerda yoqilgan, qo'riqchida
yo'q): dushanba 09:00 dan keyin (yoki seshanba) haftasiga bir marta, Telegram va email manzillariga, panel havolasi
`/compare/`.

## `POST /api/v1/edge/health` — kamera holati

```json
{"device_id": "...", "items": [
  {"key": "h:1:1760400050", "t": 1760400050.0, "source_id": 1, "source_name": "Kassa 1",
   "state": "down", "reason": "kameradan kadr kelmayapti", "since": 1760399990.0}
]}
```
`state`: `down` | `up`. Server kalit bo'yicha `INSERT OR IGNORE`.

## Boshqaruv API v1 (bulut paneli `cloud/panel` va boshqa mijozlar)

Autentifikatsiya: `POST /api/v1/auth/login` `{"email","password"}` → `{"token", "user", "company"}` va **cookie**
`hc_session` (HttpOnly, SameSite=Strict; Secure — faqat HTTPS so'rovda yoki `X-Forwarded-Proto: https`). Panel
cookie bilan ishlaydi (token JS'ga ko'rinmaydi); API mijozlari `Authorization: Bearer <token>`. Sessiya 30 kun;
`POST /api/v1/auth/logout` sessiyani va cookie'ni o'chiradi. Xato login cheklovi (10 daqiqada, 1.8.0): bitta IP + bitta
email — 10, bitta IP — 20, bitta email turli IP'lardan — 30 → **429**. Har kirish, xato kirish va chiqish audit jurnaliga yoziladi.
Cookie bilan kelgan holat o'zgartiruvchi so'rov (POST/PUT), shuningdek login va logout, boshqa saytdan bo'lsa
(`Sec-Fetch-Site: cross-site`/`same-site`, `Origin: null` yoki `Origin` xosti `Host`/`CLOUD_PUBLIC_URL` ga mos emas)
→ **403** (CSRF). Panel loginda `"session": "cookie"` yuboradi — javobda `token` bo'lmaydi (faqat cookie).
`GET /api/v1/auth/status` — sessiya bormi (sessiyasiz ham **200** `{"authenticated": false}`; bor bo'lsa `/me` bilan
bir xil ma'lumot).
Rollar: **owner** — kompaniyaning hamma filiallari, foydalanuvchi va filial yaratadi; **manager** / **guard** — faqat
biriktirilgan filiallar (`branch_ids`). Boshqa filialga so'rov → **403**.

| Metod | Yo'l | Kim | Nima |
|---|---|---|---|
| GET | `/api/v1/me` | hamma | foydalanuvchi, kompaniya, filial identifikatorlari |
| GET | `/api/v1/branches` | hamma (doirasida) | filiallar: onlayn (oxirgi edge so'rovi 3 daqiqa ichida — hello, hodisa, klip, ko'rsatkich), versiya, kameralar, navbat, 24 soatlik hodisa, tekshirilmagan; 1.9.0: `visits_today` `{in,out,inside}` (sanash bo'lmasa `null`) |
| POST | `/api/v1/branches` | owner | `{"name","device_id","address"}` → `device_token` (**bir marta**; edge `.env` → `EDGE_TOKEN`) |
| POST | `/api/v1/branches/{id}/token` | owner | tokenni almashtirish (eski darhol ishlamaydi) |
| GET | `/api/v1/branches/{id}/cameras` | doirasida | kameralar (oxirgi hello) va holat jurnali |
| GET | `/api/v1/branches/{id}/metrics?hours=24&source_id=` | doirasida | soatlik yig'indi (odamlar, AI tezligi, faol ulushi, ishlagan vaqt, signallar, 1.9.0: kirdi/chiqdi; tizim: CPU/RAM) |
| GET | `/api/v1/visits?days=14&branch_id=` | doirasida | 1.9.0 tashriflar: `days[]` (`day` — filial vaqtida kun boshi, `in`, `out`, `branches{id: {in,out,staff}}`), `branches[]` (`today_in`, `today_out`, `inside`, `hourly[24]`, `busiest_hour`, `total_in`), `tz_offset`, `day0`. `days` 1–90; boshqa filial → **403** |
| GET/POST | `/api/v1/users`, `/api/v1/users/{id}/deactivate` | owner | `{"email","password"(parol siyosati — pastda),"name","role","branch_ids"}` |
| POST | `/api/v1/users/{id}/activate` | owner | o'chirilgan xodimni qaytarish (1.8.0) |
| POST | `/api/v1/users/{id}/update` | owner | `{"name","role","branch_ids"}` (1.8.0; o'z rolini o'zgartirib bo'lmaydi) |
| POST | `/api/v1/users/{id}/password` | owner | `{"password"}` — xodimga yangi parol, uning barcha sessiyalari yopiladi (1.8.0) |
| POST | `/api/v1/me/password` | hamma | `{"current","new"}` — o'z paroli; boshqa qurilmalardagi sessiyalar yopiladi; 5 xato → 10 daqiqa **429** (1.8.0) |
| POST | `/api/v1/auth/logout-all` | hamma | barcha qurilmalardan chiqish (telefon yo'qolsa) (1.8.0) |
| GET | `/api/v1/audit?limit&before_id&action&user_id` | owner | audit jurnali: kirish, parol, xodim, filial, token, tekshirish, klip ko'rish, Telegram ulash (1.8.0; 400 kun) |
| GET | `/api/v1/system` | owner | server holati: versiya, baza, disk, kliplar, oxirgi zaxira, kanallar, jonli ulanishlar (1.8.0) |
| GET | `/api/v1/events?branch_id&status&since&until&limit` | doirasida | hodisalar (yangi → eski, ≤ 500) |
| GET | `/api/v1/events/{uid}` | doirasida | to'liq hodisa (`data` — edge yuborgan JSON) |
| POST | `/api/v1/events/{uid}/review` | doirasida | `{"status","note"}` → bazada yangilanadi + edge'ga `review` buyrug'i |
| GET | `/api/v1/clips/{device_id}/{name}` | doirasida | klip yoki rasm |
| GET/PUT | `/api/v1/me/notifications` | hamma | o'z bildirishnomalari: `prefs` (`event`, `min_score` 0–100, `camera`, `offline`, `escalation`), rol bo'yicha `defaults`, manzillar (`targets`), qaysi kanal sozlangan (`channels`) |
| POST | `/api/v1/me/telegram/link` | hamma | bulut botiga ulanish: `{"link": "https://t.me/<bot>?start=<kod>", "code", "expires_in": 600}` (kod bir martalik) |
| POST | `/api/v1/me/email` | hamma | `{"enabled": true/false}` — xabarlar o'z login email'iga |
| GET | `/api/v1/push/key` | hamma | VAPID ochiq kaliti (brauzer push obunasi uchun) |
| POST | `/api/v1/me/push` | hamma | `PushSubscription.toJSON()` (`endpoint` — FCM/Mozilla/Apple/Microsoft push xostlari) |
| POST | `/api/v1/me/targets/{id}/delete` | hamma | o'z manzilini uzish |
| POST | `/api/v1/me/notifications/test` | hamma | barcha yoqilgan manzillarga sinov xabari |
| GET | `/api/v1/notify/stats?hours=24` | owner, manager | kanal bo'yicha yetkazildi/xato/navbatda, kechikish p50/p95 (navbatdan va hodisadan) |
| GET | `/api/v1/stream` | hamma | jonli oqim (SSE, pastda) |

**Parol siyosati (1.8.0):** 8–200 belgi; keng tarqalgan parollar (`12345678`, `password1`, `admin123`, `parol123` …)
va email bilan bir xil parol rad etiladi (**400**). Parol scrypt bilan saqlanadi. **Jonli oqim:** bitta xodimga ≤ 8
bir vaqtdagi SSE ulanish (eng eskisi yopiladi), serverga ≤ 1000. **Xato javoblari** ichki tafsilotsiz (`500` —
faqat "ichki xato", to'liq matn jurnalda).

### Bildirishnomalar (1.7.0)

Bulut bildirishnomani **o'zi** yuboradi (do'kon boti — edge `TELEGRAM_BOT_TOKEN` — o'z ishini davom ettiradi).
Kanallar: **Telegram** (alohida bot, `CLOUD_TELEGRAM_BOT_TOKEN`; do'kon boti tokeni bilan bir xil bo'lsa o'chadi),
**email** (SMTP, `CLOUD_SMTP_*`), **brauzer push** (Web Push, VAPID kaliti `cloud/data/vapid_private.pem` da o'zi
yaratiladi). Har xabar `notify_outbox` navbatiga yoziladi va har kanal o'z oqimida yuboriladi (Telegram xabarlari va
videolari, email, push alohida — sekin SMTP yoki katta video hodisa xabarini kechiktirmaydi; hodisa, eskalatsiya va
tahrir birinchi). Xato bo'lsa 5 s, 20 s, 1, 5, 15 daqiqadan keyin qayta; 6 urinishdan keyin `failed`. Bot
bloklangan (403), push obunasi bekor (404/410) yoki push kaliti mos emas (401/403) — manzil o'chiriladi. Xabar
navbatda turganida hodisa tekshirilsa, eskirgan ✅/❌ tugmali xabar yuborilmaydi (`skipped`); klip xabardan oldin
kelsa, xabar yuborilgach video javob bo'lib keladi. O'chirilgan xodimning manzillari o'chadi. Cheklovlar: bitta
kanalda xodimga ≤ 10 manzil, sinov xabari 20 soniyada bir marta (**429**). Bitta brauzer push manzili — bitta xodimda
(boshqasi ulasa, oldingisidan uziladi); panelda "Chiqish" shu brauzer obunasini ham uzadi
(`POST /api/v1/auth/logout` `{"push_endpoint"}`).

| Hodisa | Kimga (sukut) | Qachon |
|---|---|---|
| `event` — shubhali harakat | qo'riqchi va menejer: hammasi; egasi: ball ≥ 75 | edge'dan yangi `yangi` holatli hodisa kelganda (10 daqiqadan eski — navbatdan kelgan, backfill — va video tahlili hodisalari uchun xabar yo'q). Telegram'da ✅/❌ tugmalar, klip kelgach shu xabarga javob sifatida video |
| `escalation` — javobsiz hodisa | menejer, egasi | `CLOUD_ESCALATE_MIN` (10) daqiqa ichida hech kim tekshirmasa, bir marta |
| `camera` — kamera ishlamay qoldi/tiklandi | menejer, egasi | `edge/health` dagi yangi `down`/`up` |
| `offline` / `online` — do'kon aloqasi | menejer, egasi | oxirgi edge so'rovi `CLOUD_OFFLINE_MIN` (5) daqiqadan eski bo'lsa (bulut serveri ishga tushgach birinchi 5 + 5 daqiqa tekshirilmaydi — edge qayta ulanib ulgursin); tiklanganda — qancha uzilgani, bitta xabar |

Tekshirish qayerda bo'lmasin (panel, bulut boti, do'kon boti), bulut Telegram xabarlari tahrirlanadi ("✅ TASDIQLANDI —
kim") va boshqa xodimlarga takror yuborilmaydi. Bulut botida: `/holat` — filiallar, `/bugun` — bugungi hodisalar.

### Jonli oqim — `GET /api/v1/stream` (Server-Sent Events)

Cookie yoki Bearer bilan. Har 15 s `: ping`, har 60 s sessiya va filial doirasi qayta tekshiriladi (sessiya tugasa
`event: logout`). Faqat foydalanuvchi ko'ra oladigan filiallar xabari keladi:

| `event:` | `data:` |
|---|---|
| `event_new` | `{"uid","branch_id","branch_name","score","kind","source_name","status","created_at","alert"}` — `alert: false` — eski (navbatdan/backfill), video tahlili yoki tekshirilgan hodisa: panel ovozsiz qo'shadi |
| `event_update` | `{"uid","status","reviewed_by"}` yoki `{"uid","clip": true}` (klip keldi) |
| `branch` | `{"branch_id","online": true/false}` |
| `health` | `{"branch_id","source_id","source_name","state": "down"/"up"}` |

### Bulut paneli

`cloud/panel` — Next.js (statik eksport), tayyor build `cloud/panel_dist` da (repoda), uni bulut serverining o'zi
`/` da beradi: Node.js faqat panelni o'zgartiruvchi dasturchiga kerak (`cd cloud/panel && npm ci && npm run build`).
Sahifalar: kirish, bosh sahifa (filiallar holati + "Tekshirish kerak" devori), hodisalar va hodisa (video, ✅ `T` /
❌ `X` / bekor qilish `U` / `Esc`, navbat bilan tekshirish), filiallar (yaratish — token bir marta, tokenni
yangilash, kameralar va uzilishlar tarixi), ko'rsatkichlar (soatlik grafiklar), taqqoslash (2.4.0, egasi va
menejer), xodimlar (egasi), sozlamalar
(Telegram/email/brauzer, nima haqida xabar, sinov xabari, yetkazish statistikasi). Panel sahifalarida CSP
(`default-src 'self'`, tashqi manba yo'q), `X-Frame-Options: DENY`.

Baza: `CLOUD_DB_URL` — SQLite (sukut) yoki `postgresql+psycopg://…` (VPS; TimescaleDB bo'lsa `metrics_minute` hypertable).
Birinchi egasi: `python -m cloud.server --init-owner EMAIL PAROL "Kompaniya"`. 1.5.0 dan qolgan `cloud/data/cloud.db`
(filialsiz sxema) topilsa avtomatik `cloud.db.v15-<vaqt>` nomiga chetga olinadi va toza baza ochiladi (eski ma'lumot
o'chmaydi, lekin yangi bazaga ko'chirilmaydi). Bulutdagi tekshirish `updated_at` ni edge soati doirasida yozadi
(`clock_skew` hisobga olinadi) — do'kondan keyin kelgan haqiqiy yangilanishlar tashlanmaydi.

## Ko'rish (superadmin — `CLOUD_ADMIN_TOKEN` bilan, barcha kompaniyalar; berilmasa o'chirilgan)

* `GET /api/v1/admin/summary` — qurilmalar, har birida hodisa/klip/ko'rsatkich/holat soni, oxirgi ko'rinish.
* `GET /api/v1/admin/events?device_id=&limit=` — oxirgi hodisalar.
* `GET /api/v1/admin/clips/{device_id}/{name}` — klip yoki rasm fayli.
* `GET /api/v1/admin/leads?status=&limit=` (2.6.0) — saytdagi so'rovlar, holatlar soni, 10-bosqich darvozasi
  (`gate`: ≥ 10 so'rov, ≥ 3 pilot) va xabar kanallari.
* `POST /api/v1/admin/leads/{id}` `{"status": "yangi|boglanildi|pilot|rad", "note": "..."}` — holat.
* `GET /healthz` — server tirikmi.

## 2.6.0 — sayt va so'rovlar (ochiq, kirishsiz)

* `GET /sayt/` — landing sayt (`site/`), qat'iy CSP (ichki skript yo'q). `GET /sayt/sozlama.js` — `.env` dagi
  `CLOUD_SITE_PHONE`, `CLOUD_SITE_TELEGRAM`, `CLOUD_SITE_EMAIL`, `CLOUD_SITE_ADDRESS`, `CLOUD_SITE_PRICE`.
* `POST /api/v1/leads` — faqat `Content-Type: application/json`, tana ≤ 16 KB:
  `{"name", "phone", "store"?, "city"?, "branches"?, "message"?, "source"?, "elapsed", "hp_extra"}`. Faqat aniq
  `application/json` va o'z saytidan (`Sec-Fetch-Site` / `Origin` boshqa sayt bo'lsa — 403).
  `elapsed` — forma ochilganidan beri soniya (yo'q yoki cheksiz — 400; < 3 — bot), `hp_extra` — honeypot (to'ldirilsa — bot). Bot
  belgisi bo'lsa ham `200 {"ok": true, "id": null}` (bazaga yozilmaydi). Telefon `998XXXXXXXXX` ko'rinishiga
  keltiriladi (9 xonali yozilsa 998 qo'shiladi). Javoblar: `200 {"ok": true, "id": n, "repeat"?: true}` (shu telefon
  24 soat ichida — mavjudiga qo'shildi), `400` maydon xato, `415` JSON emas, `413` katta, `429` chegara (IP: soatiga
  5 / kuniga 20, IPv6 — /64 bo'yicha; kuniga 1000 dan keyin). Kuniga 300 dan keyingilari saqlanadi, lekin xabar
  yuborilmaydi (hujumda guruh to'lmasin).
* `GET /api/v1/auth/status` (demo serverda, `CLOUD_DEMO=1`): kirmagan bo'lsa `demo: true` va `demo_login`;
  `GET /api/v1/me` — `demo` belgisi (panel "DEMO" yozuvini ko'rsatadi).

## 3.0.0 — Video sinovi (bulut paneli → edge tahlili)

Bulut panelida yuklangan video filialning edge kompyuterida tahlil qilinadi (bulutda AI yo'q). Kod: `cloud/video_tests.py`,
`app/cloud_tests.py`. Edge 3.0.0 dan eski bo'lsa bulut yuklashni rad etadi (409) — eski edge bu buyruqni tushunmaydi.

* **Buyruq** (hello javobidagi `commands`, oddiy ack bilan):
  `{"id": 17, "type": "video_test", "test_id": 5, "filename": "kassa.mp4", "size": 1048576, "sha256": "<64 hex>",
  "zone_source_id": 3}` — edge jobs jadvaliga `cloud_test_id=5` bilan qator yozadi ("yuklanmoqda") va ack qiladi.
  Bekor qilish: `{"type": "video_test_cancel", "test_id": 5}`.
* `GET /api/v1/edge/video-tests/{test_id}/file` (edge tokeni bilan) — videoning o'zi. 410 — bekor qilingan/tugagan.
  Edge `.part` faylga yozadi, hajm va sha256 mos kelmasa — 10 s / 60 s / 300 s dan keyin qayta, keyin "xato".
* `GET /api/v1/edge/poll` → `{"ok": true, "pending": N}` — yetkazilmagan buyruqlar soni. Edge har ~15 s so'raydi
  (bulut hello javobida `features` ichida `video_tests` bo'lsa); N > 0 — darhol hello.
* **Holat** — hello so'rovida (har hello'da takror, idempotent; sinov ishlayotganda hello har 15 s):
  `"video_tests": [{"id": 5, "job_id": 42, "status": "ishlanmoqda", "progress": 37.5, "events": 1,
  "duration": null, "error": ""}]`. Edge holatlari: yuklanmoqda | navbatda | ishlanmoqda | tayyor | xato | toxtatildi
  (bulutda — "bekor"). Tugagan holat bulutda qaytib "faol"ga o'tmaydi.
* **Natija** — odatdagi `/edge/events` va `/edge/events/{uid}/clip` (hodisada `job_id`); bulut sinov hodisalarini
  `branch_id` + `data.job_id` bo'yicha topadi. Video tahlili hodisalari uchun bildirishnoma yuborilmaydi
  (3.1.0 dan: bulut sinovi signallari va yakuniy natija — sinovni yuklagan xodimga, pastda).
* Edge tahlil paytida qayta ishga tushsa — sinov yangi vazifa (yangi `job_id`) bilan qaytadan tahlil qilinadi.

Panel API: `GET /api/v1/video-tests` (sinovlar, filiallar: onlayn/versiya/kameralar, cheklovlar),
`POST /api/v1/video-tests?branch_id=&name=[&zone_source_id=]` (tana — videoning o'zi, `Content-Length` bilan;
owner/manager), `GET /api/v1/video-tests/{id}` (sinov + hodisalari), `POST /api/v1/video-tests/{id}/cancel`,
`DELETE /api/v1/video-tests/{id}` (faqat tugagan).

## 3.1.0 — toza klip, zarar summasi, Video sinovida zonalar va o'lchovlar

* **Toza klip:** `POST /api/v1/edge/events/{uid}/clip` — ixtiyoriy `clean` fayl maydoni (chizmalarsiz mp4, `ftyp`
  imzosi). Bulut uni `events.clip_clean` ga saqlaydi (`<dev>/<uid>_clean.mp4`); bulut boti Telegram'ga shuni
  yuboradi. Klip allaqachon bor bo'lsa ham (takror yuklash) toza nusxa yo'q bo'lsa — saqlanadi. Eski bulut maydonni
  e'tiborsiz qoldiradi.
* **Zarar summasi:** edge hodisasida `loss_sum` (butun so'm yoki null), `loss_by`, `loss_at` — bulut `events.loss_sum`
  ga yozadi (faqat `job_id` yo'q va xavfsizlik turi bo'lmagan hodisalarda). Summa o'zgarsa (holat o'zgarmasa ham)
  bulut botidagi xabarlar yangilanadi. Bulutda kiritilganda edge'ga buyruq:
  `{"type": "loss", "local_id": 12, "uid": "<dev>:12", "loss_sum": 150000, "by": "Bulut: Ali", "t": <bulut vaqti>}` —
  edge lokal summa undan yangiroq bo'lsa (loss_at) qo'llamaydi; eski edge buyruqni tushunmaydi va ack qiladi.
  Panel API: `POST /api/v1/events/{uid}/loss` `{"loss_sum": "150 ming"}` (bo'sh — olib tashlash; 409 — tasdiqlanmagan,
  xavfsizlik yoki video sinovi hodisasi), `POST /api/v1/events/{uid}/review` ga ixtiyoriy `loss_sum`,
  `GET /api/v1/losses` → `{"today": {sum, n}, "month": ..., "total": ..., "missing": N, "branches": [...]}`;
  `/api/v1/compare` da `theft.loss`, `theft.loss_n`, `total.loss`. Hodisalar ro'yxatida `loss_sum`, `clip_clean`.
* **Video sinovi signallari:** edge hodisasida `cloud_test_id` (job — bulut sinovi bo'lsa). Bulut bunday hodisani
  faqat sinovni yuklagan xodimga yuboradi (sozlamadan qat'i nazar); sinov tugaganda (tayyor/xato) — bir marta
  `vtest` xabari (signallar, o'lchovlar qisqacha).
* **Zonalar:** `POST /api/v1/video-tests?...&zones=<JSON>` — `[{"kind": "navbat"|"kassir"|"chiziq"|"polka"|"kassa"|
  "eshik"|"xodim"|"etiborsiz", "name": "1-kassa", "pts": [[x, y], ...]}]`, nuqtalar 0..1, ≤ 12 zona, ko'pburchak 3–16
  nuqta, chiziq — 2 nuqta (a→b ning o'ng tomoni — ichkari), JSON ≤ 4000 belgi. Berilsa `zone_source_id` e'tiborsiz.
  Edge 3.1.0 dan eski bo'lsa — 409. Buyruqda: `"zones": [...]`.
* **O'lchovlar:** hello'dagi `video_tests[]` elementida (holat `tayyor`, tugaganiga 6 soatdan oshmagan) `metrics`:
  `{"duration", "zones", "persons_max", "queue": [{"name", "max_len", "avg_len", "busy_pct", "served", "avg_wait",
  "max_wait", "series": [[t, uzunlik], ...]}], "lines": [{"name", "in", "out", "staff"}], "areas": [{"name", "kind",
  "visitors", "avg_dwell", "reaches"}]}` (≤ 20 KB). Bulut "tayyor" bilan birga yoki keyinroq (yakuniy sinovga) yozadi;
  panel API'da `test.zones`, `test.metrics`.

## Edge tomonidagi kafolatlar (qabul mezoni: internet 1 soat uzilsa ma'lumot yo'qolmaydi)

* Hamma yozuv avval lokal `outbox` jadvaliga (SQLite, WAL) tushadi — jarayon o'lsa ham saqlanadi.
* Yetkazilgan qator o'chadi; yetkazilmagani 30 kun saqlanadi (keyin disk uchun tashlanadi). Server rad etgan
  (400/413/422) qatorlar `failed` bo'lib panelda ko'rinadi, "Xatolarni qayta yuborish" bilan qaytariladi.
* Hodisa bir necha marta yangilansa, yuborilmagan qator ustiga yoziladi — navbat o'smaydi. Yuborish paytida kelgan
  yangilanish yangi qator bo'ladi (eskisi yo'qolmaydi).
* Hodisa lokal bazadan o'chirilsa (muddat yoki qo'lda), uning yuborilmagan qatorlari ham olib tashlanadi.
* Hajm bahosi: hodisa ≈ 2–4 KB, klip ≈ 1–3 MB, ko'rsatkich ≈ 0.3 KB/daqiqa/kamera. 1 soat uzilish, 4 kamera,
  10 hodisa → ≈ 30 MB.
* Bulut sozlanmagan bo'lsa (`CLOUD_URL` bo'sh) navbatga yozilmaydi; sozlangach (yoki manzil o'zgargach) oxirgi
  7 kunlik hodisalar bir marta navbatga qo'yiladi (backfill, har manzil uchun alohida).
