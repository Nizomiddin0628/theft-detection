# Hushyor AI — do'kon kameralarida shubhali harakatni aniqlash

Kamera (kompyuter kamerasi, IP kamera yoki video fayl) orqali odamlarni kuzatadi. Kimdir mahsulotni
**cho'ntakka, kiyim ichiga yoki sumkaga yashirganga o'xshash** harakat qilsa, o'sha lahzaning qisqa
klipini avtomatik kesib, **Telegram botga** sabablari bilan yuboradi. Qo'riqchi videoni ko'rib
**✅ Tasdiqlash** yoki **❌ Xato signal** tugmasini bosadi. Tizim hech kimni ayblamaydi — u faqat
"tekshirib ko'ring" deydi, qarorni odam qiladi (Motive dashcam'dagi kabi "AI → klip → inson tekshiruvi").

---

## 1.8.0 versiyadagi o'zgarishlar (2-bosqich, 5-qadam: xavfsizlik va yakun — 2-bosqich yopildi)

* **Xavfsizlik tekshiruvi (OWASP Top 10)** — natija, tuzatilgan 15 ta muammo va qolgan xavflar:
  [`docs/SECURITY.md`](docs/SECURITY.md). Sinov: `python -m tests.test_security` (edge + bulut; PostgreSQL bilan
  ham). Asosiylari:
  * **Edge:** standart `admin123` bilan faqat shu kompyuterdan kiriladi, tarmoqdan — yo'q. Havoladan video yuklash
    lokal tarmoq va shu kompyuter manzillariga bormaydi (SSRF, DNS rebinding ham). CSRF qat'iylashdi. CSP qo'shildi.
    Kirishdan keyingi ochiq yo'naltirish yopildi.
  * **Bulut:** parol siyosati. Login cheklovi bir vaqtdagi so'rovlar bilan ham aylanib o'tilmaydi. Parol almashtirish
    va tiklash, "barcha qurilmalardan chiqish". Xodimni tahrirlash va qaytarish. Audit jurnali
    (**Xodimlar → Jurnal**). Panel CSP'sida `'unsafe-inline'` yo'q. Baza ishi threadpool'da, jonli oqimlar
    chegaralangan. nginx ortida faqat ishonchli proksining `X-Forwarded-For` i olinadi.
  * Kutubxonalarning minimal versiyalari ko'tarildi (`starlette>=1.3.1`, `python-multipart>=0.0.31`,
    `jinja2>=3.1.6`, `h11>=0.16`, `fastapi>=0.135`): `pip-audit` va `npm audit` — zaiflik yo'q.
* **Bulut serverini joylashtirish** — [`docs/DEPLOY.md`](docs/DEPLOY.md):
  * VPS: `deploy/install_vps.sh` — nginx + Let's Encrypt (HTTPS, HTTP/2, HSTS), systemd xizmati (cheklangan
    huquqlar), kundalik zaxira, SQLite yoki PostgreSQL.
  * Kompyuterda: `scripts/windows/install_cloud_autostart.ps1`.
  * `CLOUD_PUBLIC_URL`, `CLOUD_TRUSTED_PROXIES`, filial bo'yicha qurilma tokeni (1.6.0 dan).
* **Zaxira va disk** (`cloud/maintenance.py`):
  * Bazaning avtomatik zaxirasi: SQLite — onlayn backup, PostgreSQL — `pg_dump`. VAPID kaliti bilan, oxirgi N tasi
    saqlanadi.
  * Kliplarni saqlash muddati: 30 kun, tasdiqlanganlari 180 kun. Bulut soati bo'yicha hisoblanadi.
  * Diskda joy kam qolsa, eng eski tasdiqlanmagan kliplar o'chiriladi; oxirgi sutkadagilariga tegilmaydi.
  * `python -m cloud.maintenance status|backup|prune`. Panelda: **Sozlamalar → Bulut serveri**.
* **Windows o'rnatuvchisi:** `install_windows.bat` — Python topadi; `.venv` va kutubxonalarni o'rnatadi; panel
  parolini so'raydi (`admin123` qoldirilmaydi); ixtiyoriy Telegram va bulut sozlamasi; avtomatik start.
  **Yangilash:** `scripts\windows\update.ps1 -Zip <fayl>`:
  * ZIP avval tekshiriladi, zaxira olinadi.
  * Kutubxona o'rnatilmasa yoki panel ishga tushmasa, kod avtomatik eski holatga qaytadi.
* **72 soatlik barqarorlik sinovi** — `tests/soak.py`:
  * Alohida papkada edge + bulut + soxta Telegram + soxta IP kamera ishga tushadi. Jonli `data/` ga tegilmaydi.
  * Davriy nosozliklar: internet uzilishi (rad etish va "osilib qolish"), bitta 1 soatlik uzilish (darvoza), Telegram
    ishlamasligi (5xx, javobsiz, kechikkan javob), kamera uzilishi, edge va bulutni qayta ishga tushirish.
  * Hisobot (`hisobot.md` + grafikli `hisobot.html`) tekshiradi:
    * xotira oqishi;
    * oqim va dastaklar;
    * navbatlar bo'shashi;
    * har hodisa bulutga va Telegram'ga **aynan bir marta** yetgani;
    * har kamera uzilishiga bitta xabar;
    * soxta "aloqa uzildi" yo'qligi;
    * disk chegaralangani;
    * kutilmagan xatolar.
  * Jonli tizimning o'z ko'rsatkichlaridan hisobot: `python tests\soak.py --report-live data\hushyor.db --hours 72`.
* **Model darvozasi:** `python tests\model_gate.py`. Panelda "Aniqlik sinovi → Hammasini solishtirish" dan keyin
  yangi (ochiq) modellar F1 va recall bo'yicha YOLO11 dan kam emasligini tekshiradi. Kam ma'lumotda xulosa
  "taxminiy" deb belgilanadi.
* **Barqarorlik sinovi topgan muammolar** (qisqa sinov, 2 yadroli Linux):
  * Telegram yoki internet uzilgan paytdagi signal do'kon botida qayta yuborilmaydi: ~1 daqiqadan uzun uzilishda
    yo'qoladi. Bulut navbati va panel orqali yetadi.
  * Klip yuklash ~40 s dan uzoq ishlamasa, video yetmaydi.
  * Telegram javobi kechiksa, xabar ikki marta borishi mumkin.
  * Kamera signaldan keyingi bir necha soniyada uzilsa, klip yaratilmaydi.

  Bular 1.4.1 fayllarida (`app/telegram_bot.py`, `app/ai/worker.py`). Asl fayllar kelgach, ular ustidan tuzatiladi.
  Sizning kompyuteringizda `python tests\soak.py --hours 12` shu xatolarni o'z kodingizda ko'rsatadi.

## 1.7.0 versiyadagi o'zgarishlar (2-bosqich, 4-qadam: bulut paneli va bildirishnoma xizmati)

* **Bulut paneli** (`cloud/panel` → tayyor build `cloud/panel_dist`): bulut serverining o'zi `http://<server>:9000/`
  da beradi — kompyuteringizda **Node.js kerak emas**. Sahifalar:
  * **Bosh sahifa** — filiallar holati (aloqa, kameralar, tekshirilmagan) va "Tekshirish kerak" devori: har bir
    tekshirilmagan hodisa kadri, kamera, vaqt, ball. Yangi hodisa sahifa yangilanmasdan paydo bo'ladi (ovoz va xabarcha).
  * **Hodisa** — katta video, sabablar, ✅ **Tasdiqlash** (`T`), ❌ **Xato signal** (`X`), bekor qilish (`U`), `Esc`.
    "Navbat bilan tekshirish" — eng eskisidan boshlab, qarordan keyin keyingisiga o'tadi. Qaror do'kondagi
    kompyuterga va barcha Telegram xabarlariga yetadi.
  * **Hodisalar** (holat, filial, davr bo'yicha), **Filiallar** (yaratish — qurilma tokeni bir marta ko'rinadi,
    tokenni yangilash, kameralar, uzilishlar tarixi), **Ko'rsatkichlar** (soatlik grafiklar: odamlar, signallar,
    kameralar ishlagan vaqt, kompyuter yuklamasi, tahlil tezligi), **Xodimlar** (faqat egasi), **Sozlamalar**.
  * Telefonda ham ishlaydi (menyu pastda), qorong'i rejim tizimga qarab. Kirish — HttpOnly cookie, CSRF himoyasi,
    tashqi manba yo'q (CSP).
* **Bildirishnoma xizmati** (`cloud/notify.py`) — bulut o'zi yuboradi, har xodimga o'z sozlamasi bo'yicha:
  * **Telegram** — alohida bulut boti (`CLOUD_TELEGRAM_BOT_TOKEN`); xodim panelda "Telegram'ni ulash" ni bosadi →
    botda Start → bog'landi. Xabar ✅/❌ tugmalari bilan, klip kelgach video shu xabarga javob bo'lib keladi.
    Kim qayerda tekshirsa ham (panel, bulut boti, do'kon boti) xabar "✅ TASDIQLANDI — kim" ga o'zgaradi.
    Botda `/holat` va `/bugun`.
  * **Email** (SMTP) va **brauzer push** (panel yopiq bo'lsa ham ekranda; panel `https://` orqali ochilgan bo'lishi kerak).
  * Nima haqida: shubhali harakat (qo'riqchi, menejer — hammasi; egasi — ball ≥ 75), **javobsiz hodisa** (10 daqiqa
    hech kim tekshirmasa — menejer va egasiga), **kamera ishlamay qoldi/tiklandi**, **do'kon bilan aloqa uzildi/tiklandi**
    (5 daqiqa). Har xodim Sozlamalar'da o'zgartiradi, "Sinov xabarini yuborish" bilan tekshiradi.
  * Ishonchlilik: navbat (`notify_outbox`) va qayta urinish (5 s … 15 daqiqa), bloklangan bot / bekor qilingan push
    o'zi o'chiriladi, eski (navbatdan kelgan) hodisalar uchun xabar yuborilmaydi. Panelda yetkazish statistikasi
    (yetkazildi, xato, kechikish p50/p95).
* **Sinovlar** (2 yadroli Linux, sizning kompyuteringizda emas): `tests/test_notify.py` — 81 sinov SQLite'da va
  PostgreSQL 16 da (162/162): sinov Telegram serverida hodisadan xabargacha p50 ≈ 0.1–0.2 s, p95 ≈ 2.1 s (ataylab
  qo'yilgan Telegram xatolari va qayta urinishlar bilan). `tests/test_panel.py` — 62 sinov haqiqiy brauzerda
  (Chromium): kirish va yo'naltirish himoyasi, devor, jonli xabar (≈ 0.1 s), `T`/`X`/`U`, navbat, token bir marta,
  rollar, telefon ekrani, qorong'i rejim, konsolda xato yo'q. Panel build hajmi ≈ 1.2 MB.
* **Mustaqil tekshiruv** (alohida agent) 15 ta muammo topdi (7 o'rta, 8 past), hammasi tuzatildi va sinovga qo'shildi:
  login `next=` orqali boshqa saytga yo'naltirish, sinov xabari bilan navbatni to'ldirish, sekin kanal (SMTP, katta
  video) boshqa xabarlarni kechiktirishi, navbatda turganida tekshirilgan hodisaga eskirgan tugmali xabar, klip
  xabardan oldin kelsa yo'qolishi, katta klip yuklanayotganda "aloqa uzildi" xato xabari, umumiy kompyuterda push
  chiqishdan keyin ham kelishi, login CSRF, o'chirilgan xodimga xabar, do'kon soati farqi kamera xabarida.
* Yangi kutubxona: `pywebpush` (`pip install -r requirements.txt`). Sinov uchun: `pip install -r requirements-dev.txt`
  va `python -m playwright install chromium`.

**Ishga tushirish (sizning kompyuteringizda):**
```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
# .env: CLOUD_TELEGRAM_BOT_TOKEN=<@BotFather'dagi YANGI bot tokeni>  (do'kon botidan boshqa!)
.\.venv\Scripts\python.exe -m cloud.server --port 9000          # alohida oynada
# Brauzerda: http://127.0.0.1:9000/  → egasi email/paroli bilan kiring (1.6.0 da --init-owner bilan yaratilgan)
```

---

## 1.6.0 versiyadagi o'zgarishlar (2-bosqich, 3-qadam: bulut — kompaniya → filial → kamera, rollar)

* **Bulut bazasi** (`cloud/db.py`, SQLAlchemy): `CLOUD_DB_URL` bo'sh — SQLite (`cloud/data/cloud.db`, bitta
  kompyuter); VPS'da `postgresql+psycopg://…` — PostgreSQL, TimescaleDB bo'lsa `metrics_minute` hypertable.
  Bitta sxema, bitta `.env` qatori bilan ko'chiriladi. Sinov to'plami ikkala bazada bir xil o'tadi (84/84).
* **Ierarxiya va rollar:** kompaniya → filial (do'kon = bitta edge kompyuter, o'z **qurilma tokeni**) → kamera.
  Foydalanuvchilar: **egasi** (hamma filiallar, filial va foydalanuvchi yaratadi), **menejer** va **qo'riqchi**
  (faqat biriktirilgan filiallar). Parollar scrypt, sessiya tokenlari hash bilan saqlanadi, 10 xato login → blok.
* **Boshqaruv API v1** (`docs/SYNC_API.md`): login, filiallar (onlayn, kameralar, 24 soatlik hodisa), filial
  yaratish → qurilma tokeni bir marta ko'rsatiladi, foydalanuvchilar, hodisalar (filial/holat/vaqt), hodisa
  tafsiloti, kliplar, soatlik ko'rsatkichlar. 4-qadamdagi Next.js panel shu API ustiga quriladi.
* **Bulutda tekshirish → do'kon:** panelda ✅/❌ bosilsa edge'ga `review` buyrug'i ketadi (keyingi hello'da),
  do'kondagi hodisa va Telegram xabari yangilanadi, buyruq ack qilinadi. Takror yozilmaydi.
* **Sync API v1 o'zgarmagan.** Qurilma tokeni endi bazadagi filialdan. Eski usul — `CLOUD_EDGE_TOKENS` muhit
  tokeni — faqat bazada o'z tokeni bo'lmagan qurilmalar uchun ("Mening kompaniyam" filiali avtomatik, 20 tagacha);
  bulut endi `.env` dagi `EDGE_TOKEN` ni o'qimaydi (filial tokeni umumiy tokenga aylanmasin). Superadmin
  (`CLOUD_ADMIN_TOKEN`) barcha kompaniyalarni ko'radi.
* **Yangilash:** 1.5.0 dagi `cloud/data/cloud.db` sxemasi boshqacha — birinchi ishga tushishda avtomatik
  `cloud.db.v15-<vaqt>` nomiga chetga olinadi (ma'lumot o'chmaydi, yangi bazaga ko'chmaydi). Edge 1.5.0 bilan ham
  ishlaydi, lekin bulutdagi ✅/❌ do'konga faqat 1.6.0 edge'da yetadi.
* Yangi kutubxona: `sqlalchemy` (`pip install -r requirements.txt`); PostgreSQL uchun `pip install "psycopg[binary]"`.
  Sinov: `tests/test_cloud.py` — 89 sinov SQLite'da va PostgreSQL 16 da bir xil o'tdi (2 yadroli Linux).

**Bulutni ishga tushirish (sizning kompyuteringizda):**
```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m cloud.server --init-owner egasi@dokon.uz Parol12345 "Kompaniya nomi"
.\.venv\Scripts\python.exe -m cloud.server --port 9000          # alohida oynada
# Edge paneli: Tizim holati → Bulut → "Qurilma" ni ko'chirib oling, keyin filial yarating:
#   POST http://127.0.0.1:9000/api/v1/auth/login  {"email":"egasi@dokon.uz","password":"Parol12345"} → token
#   POST http://127.0.0.1:9000/api/v1/branches  (Authorization: Bearer <token>)  {"name":"Do'kon 1","device_id":"<qurilma>"}
#   → device_token ni edge .env ga: EDGE_TOKEN=<device_token>, CLOUD_URL=http://127.0.0.1:9000, keyin run.py qayta
```

## 1.5.0 versiyadagi o'zgarishlar (2-bosqich, 2-qadam: edge agent — internet uzilsa ma'lumot yo'qolmaydi)

* **Bulutga yuborish navbati (store-and-forward).** `.env` da `CLOUD_URL` va `EDGE_TOKEN` berilsa, har hodisa,
  uning yangilanishi (✅/❌, izoh), klip, kamera holati (uzildi/tiklandi) va daqiqalik ko'rsatkichlar avval shu
  kompyuterdagi `outbox` jadvaliga yoziladi, keyin tartib bilan serverga ketadi. Internet yo'q bo'lsa navbatda
  turadi (30 kungacha), ulanish tiklangach hammasi bir martadan yetkaziladi (server takrorni o'zi aniqlaydi).
  Bulut berilmasa hech narsa o'zgarmaydi — hamma narsa faqat shu kompyuterda.
* **Sync API v1** — edge ↔ bulut shartnomasi: `docs/SYNC_API.md` (hello, events, clip, metrics, health; xato
  kodlari, idempotentlik, tartib). Bulut keyin PostgreSQL'ga o'tganda edge tomoni o'zgarmaydi.
* **Bulut qabul qiluvchi (namuna):** `python -m cloud.server --port 9000` — FastAPI + SQLite, bir nechta do'kon
  (qurilma), tokenlar (`CLOUD_EDGE_TOKENS="tok1,dokon2:tok2"`), admin API (`CLOUD_ADMIN_TOKEN`), kliplar
  `cloud/data/clips/` da. Hozircha sizning kompyuteringizda yoki kichik VPS'da ishlaydi; 3-qadamda PostgreSQL +
  TimescaleDB va kompaniya → filial → kamera ierarxiyasi shu shartnoma ustiga quriladi.
* **Daqiqalik ko'rsatkichlar** (`metrics_minute`): har kamera uchun odamlar (o'rtacha/eng ko'p), AI tezligi,
  faol rejim ulushi, ishlagan vaqt ulushi, signallar; tizim uchun CPU, RAM, navbat. 90 kun saqlanadi,
  `GET /api/metrics?hours=24`. Odam sanash (3-bosqich) va hisobotlar (8-bosqich) shu poydevorda bo'ladi.
* **Panel:** *Tizim holati → Bulut (edge agent)* — holat, navbat (hodisa/klip/MB), oxirgi xato, soat farqi,
  "Ulanishni tekshirish", "Xatolarni qayta yuborish".
* Qabul mezoni sinovi (`tests/test_sync.py`, 41 sinov): bulut o'chiq paytida 20 hodisa + 5 klip + holat +
  ko'rsatkichlar navbatda saqlandi, bulut qaytgach hammasi aynan bir martadan yetdi (takror yo'q, hodisa klipidan
  oldin), jarayon qayta ishga tushganda navbat saqlanadi, token xatosida yuborish to'xtaydi, rad etilgan qator
  navbatni to'smaydi.

**Bulutni sozlash (bitta kompyuterda sinash):**
```powershell
# .env ga qo'shing (ikkala dastur bir xil tokenni ko'radi):
#   EDGE_TOKEN=<python -c "import secrets; print(secrets.token_hex(16))">
#   CLOUD_URL=http://127.0.0.1:9000
#   CLOUD_ADMIN_TOKEN=<yana bir token>       # ixtiyoriy: admin API uchun
.\.venv\Scripts\python.exe -m cloud.server --port 9000     # alohida oynada
.\.venv\Scripts\python.exe run.py
# Tekshirish: Tizim holati → Bulut → "Ulanishni tekshirish"; bulutda: http://127.0.0.1:9000/api/v1/admin/summary
```

## 1.4.1 versiyadagi o'zgarishlar (tuzatishlar — mustaqil kod tekshiruvi natijasi)

Yangi imkoniyat yo'q; 1.4.0 ni to'liq ko'rib chiqishda topilgan kamchiliklar tuzatildi:

* **Xavfsizlik.** Login sahifasidagi "standart parol" eslatmasi endi faqat shu kompyuterdan ochilganda ko'rinadi;
  5 ta xato paroldan keyin o'sha IP 1 daqiqaga (keyin ko'proq) bloklanadi; standart parol bilan panel tarmoqqa
  ochiq bo'lsa panelda qizil ogohlantirish. Boshqa saytdan yuborilgan POST so'rovlar rad etiladi (CSRF),
  xavfsizlik sarlavhalari qo'shildi, CSV eksportda formula bo'lib bajariladigan matn neytrallanadi, RTSP paroli
  kameralar jadvalida butunlay yashiriladi, Telegram ulanish kodini qayta-qayta tavakkal qilish bloklanadi.
* **Telegram.** Ustuvorlik haqiqatan ishlaydi: 6 ta bajaruvchi navbatdan o'zi oladi — kliplar yuklanayotganda ham
  yangi signal rasmi birinchi ketadi; tugma bosilishlariga alohida oqim javob beradi. Klip tarmoq xatosi bilan
  yetmasa 15 s dan keyin (2 martagacha) qayta yuboriladi; klip yaratilmagan bo'lsa xabarda "⏳ tayyorlanmoqda"
  o'rniga "⚠️ klip yaratilmadi" yoziladi. Dastur to'xtatilganda bot 2 daqiqalik video yuklashni kutib qolmaydi.
* **Modellar.** Model to'plamini almashtirish muvaffaqiyatsiz bo'lsa eski to'plam joyida qoladi (avval jimgina
  YOLO11 ga o'tib, sozlama "open" deb qolardi). Qo'lda yuklangan model fayli `models/` ga faqat tekshiruvdan
  o'tgach (ochiladi, kirish shakli to'g'ri) tushadi; hajm chegarasi 300 MB; `.onnx` ni to'g'ridan-to'g'ri yuklash
  mumkin; buzuq fayl topilsa `.broken` nomiga o'tkaziladi va qayta yuklab olish imkoni qaytadi. Statik batch'li
  RTMPose fayllari ham ishlaydi; `yolo11n.onnx` bo'lmasa dastur to'xtamaydi (buyum detektori o'chadi).
* **Kameralar.** Bitta kadrdagi AI xatosidan keyin kamera holati "xato"da qolib ketmaydi (avval Telegram'ga
  "kamera ishlamayapti" ketib, "tiklandi" hech qachon kelmas edi). RTSP uchun ulanish/o'qish vaqt chegarasi
  (8 s) — kamera elektrdan uzilsa o'quvchi osilib qolmaydi, qayta ulanadi.
* **Nazoratchi (`--supervise`).** Windows'da bola jarayon endi toza to'xtatiladi (HTTP buyruq → kameralar va
  kliplar yopiladi), `TerminateProcess` faqat zaxira. Sog'liq tekshiruvi Windows tizim proksisidan o'tmaydi.
  Ikkinchi nusxa ishga tushganda ishlayotgan nusxaning bazasiga tegmaydi (port avval tekshiriladi).
* **Boshqa.** `.env` Notepad'da BOM bilan saqlansa ham o'qiladi; `start_windows.bat` `requirements.txt`
  o'zgarganda kutubxonalarni o'zi yangilaydi; eski `notify_log` yozuvlari tozalanadi; ffmpeg osilib qolsa 120 s
  da to'xtatiladi; pythonw rejimida jurnal ikki faylga yozilmaydi. Yangi sinov: `python -m tests.test_fixes` (50).
* **Teskari proksi (nginx) orqasida** ishlatilsa, proksi `Host` va `X-Forwarded-For` sarlavhalarini uzatsin —
  shunda login himoyasi va CSRF tekshiruvi to'g'ri ishlaydi (`.env.example` ga qarang).

## 1.4.0 versiyadagi o'zgarishlar (2-bosqich, 1-qadam: litsenziyasi toza modellar)

* **Ko'rish modellari tanlanadi** (*AI model → Ko'rish modellari*): `yolo11` (hozirgi, AGPL-3.0),
  `open` — YOLOX-nano + RTMPose-t + YOLOX-tiny (Apache-2.0), `open-s` — YOLOX-tiny + RTMPose-s (aniqroq,
  og'irroq). Tanlash kameralarni to'xtatmaydi. Litsenziyalar: `models/LICENSES.md`.
* **OpenVINO** (ixtiyoriy): "Avto" rejimida dastur shu kompyuterda ONNX Runtime va OpenVINO ni o'lchaydi.
  OpenVINO faqat kamida 30% tez bo'lsa tanlanadi. Natija bir xil: OpenVINO doim FP32 da ishlaydi.
* **Aniqlik sinovi** model to'plamlarini bir xil videolarda solishtiradi ("Hammasini solishtirish"). Tarixda
  model va og'irlik (tahlil vaqti / video vaqti) ko'rsatiladi.
* O'lchov (2 yadroli sinov serveri, YOLO11 bilan bir xil sharoit): `open` skelet bosqichi deyarli bir xil tezlikda
  (3 odamli kadrda 50 ms vs 48 ms), umumiy ishda ~20% og'irroq (buyum modeli YOLOX-tiny og'irroq). Odam topishda
  YOLOX-nano YOLO11 topgan odamlarning ~3% ini o'tkazib yuboradi. Qaysi biri do'koningizda aniqroq — o'z
  videolaringizdagi aniqlik sinovi hal qiladi; shungacha standart `yolo11`.
* Ctrl+C bosilganda ochiq brauzer sahifalari bilan ham dastur 1–2 soniyada xatosiz to'xtaydi; `.gitattributes` —
  git'dagi CRLF ogohlantirishlari yo'q.

## 1.3.0 versiyadagi o'zgarishlar (1-bosqich: aniqlik va resurs)

* **Kamera faolligi — kuch faqat kerak joyga.** Har kamera o'z holatini o'zi aniqlaydi:
  *bo'sh* (odam yo'q) — model deyarli ishlamaydi (harakat tekshiruvi, 4 s da bir marta sug'urta);
  *tinch* (odam bor, qo'li hech narsaga uzatilmagan) — faqat skelet, `calm_fps` (standart 4) kadr/s;
  *faol* (qo'l polkaga uzatildi, buyum ushladi, yashirish joyiga bordi) — `ai_fps` kadr/s.
  Model navbati ustuvorlik bilan: faol kamera birinchi, keyin tinch, keyin bo'sh (hech kim abadiy kutmaydi).
  Video tahlili va aniqlik sinovi fon navbatida — jonli kameralarga xalaqit bermaydi.
* **Buyum faqat qo'l atrofida va faqat kerakda.** Butun kadrdagi buyum detektori olib tashlandi. Buyum faqat
  polkaga uzatilgan / buyum ushlagan / yashirish joyiga borgan qo'l atrofidagi kichik qismda qidiriladi
  (kadrda bitta chaqiruv). Polkadagi idishlar, yeng, soch endi ramkaga olinmaydi. Rasmda faqat qo'ldagi
  mahsulot: **"MAHSULOT: nom"**, yashirilsa **"nom: YASHIRILDI?"**.
  O'lchov (2 yadroli server, 9 kamera: 3 tasida odam): odamli kamera AI tezligi 0.8 → 2.9 kadr/s;
  3 kamerada bir xil natija uchun CPU 144% → 101%. Sintetik baholash (har holatga 100): ushlash 72% → 73%,
  xato signal 2.5% → 2.5% — aniqlik saqlangan, hisob-kitob ancha kam.
* **Kamera zonalari** (*Kameralar → Zonalar*): polka, kassa, kirish/chiqish, xodimlar joyi, e'tiborsiz —
  har birining o'z qoidasi va sezgirligi. "Kirish/chiqish": shubhali odam eshikka kelsa — alohida signal.
* **Aniqlik sinovi** (yangi bo'lim): o'z videolaringizni yuklab hodisalarni belgilang, tizim jonli
  kameradagidek tahlil qiladi va aniqlik, topish, soatiga xato signal, kechikishni hisoblaydi; tarix bilan solishtirish.
* **Videoga olish qo'llanmasi** — *Yo'riqnoma → Videoga olish*.
* **Telegram:** eski botdan qolgan pastki tugmalar, buyruqlar va menyu tugmasi tozalanadi (bir martalik).
* Klip o'zgaruvchan tezlikdagi buferdan ham real vaqtda o'ynaydi (12 kadr/s doimiy).

## 1.2.0 versiyadagi o'zgarishlar (0-bosqich: tezkor signal va barqarorlik)

* **Telegram — avval rasm, keyin klip.** Hodisa aniqlangan zahoti o'sha kadrning rasmi, sabablar va tugmalar
  yuboriladi (sinovda p95 0.3 s). Klip tayyor bo'lgach o'sha xabardagi rasm videoga almashadi (`editMessageMedia`).
  Obunachilarga parallel yuboriladi, video bir marta yuklanadi (`file_id`).
* **Yangi tugmalar:** 👀 Ko'ryapman (kim shug'ullanayotganini hammaga ko'rsatadi), ↩️ Qarorni o'zgartirish.
* **Signal tezligi o'lchovi:** har xabar `notify_log` jadvaliga yoziladi; Telegram sahifasida median, 95% va
  "5 s ichida" ulushi, kompyuter soati Telegram vaqtidan farq qilsa ogohlantirish. `/holat` buyrug'ida ham.
* **Web panel jonli:** yangi hodisa SSE orqali bir zumda keladi (4 s kutmasdan), ovozli signal (yon paneldagi
  "🔔 Ovoz" bilan o'chiriladi).
* **Kamera uzilsa xabar:** kamera 60 s (sozlanadi) ishlamasa Telegram'ga xabar, tiklansa — yana xabar.
* **Nazoratchi va avtomatik start:** `python run.py --supervise` dastur to'xtasa yoki qotsa qayta ishga
  tushiradi. Windows'ga kirganda avtomatik start:
  `powershell -ExecutionPolicy Bypass -File scripts\windows\install_autostart.ps1`
  (o'chirish: `scripts\windows\uninstall_autostart.ps1`).

## 1.1.0 versiyadagi o'zgarishlar

* **Tezlik:** kadr nisbatiga mos (to'rtburchak) model kirishi, AI oqimlari cheklangan va "aylanib turish"
  o'chirilgan, bo'sh kamerada modellar ishlamaydi, oldindan ko'rish rasmi faqat kimdir ko'rayotganda
  tayyorlanadi. Sinov kompyuterida (2 yadro) kadr vaqti 145 → 74 ms, bo'sh kamerada CPU 190% → 26%.
* **Klip:** shubhali harakatdan 10 s oldin va 10 s keyin. Klipda vaqt chizig'i, qizil "SHUBHALI LAHZA"
  belgisi; Telegram xabarida va hodisa sahifasida shubhali lahza vaqti va klipdagi o'rni ko'rsatiladi.
* **Aniqlash:** o'z telefonini cho'ntakka qaytarish endi signal bermaydi; bir-birini to'sgan odamlarning
  aralashgan skeleti tahlil qilinmaydi; bir vaqtda ikki odam yashirsa ikkalasi ham ushlanadi; ball dwell'dan
  keyin 2 s davomida qayta hisoblanadi; AI model og'irligi 20 → 10 (model hali faqat sintetik ma'lumotda).
* **Ma'lumot:** tekshirilgan hodisa o'chirilsa ham uning skelet izi va javobi `data/feedback/` ga saqlanadi
  (qayta o'qitish uchun). Jonli rejimda yuklangan video bir marta o'ynaydi — takroriy hodisa yo'q.

## 1. O'rnatish va ishga tushirish

### Windows
1. [python.org](https://www.python.org/downloads/) dan **Python 3.11 yoki 3.12** ni o'rnating.
   O'rnatishda **"Add python.exe to PATH"** belgisini albatta qo'ying.
2. ZIP faylni oching (masalan `C:\hushyor-ai`).
3. `install_windows.bat` faylini ikki marta bosing (1.8.0, tavsiya). U quyidagilarni qiladi:
   * kutubxonalarni o'rnatadi (3–10 daqiqa, internet kerak);
   * panel parolini so'raydi;
   * Windows'ga kirganda avtomatik ishga tushishni sozlaydi.

   Oddiy ishga tushirish uchun: `start_windows.bat`.
4. Brauzer o'zi ochiladi: **http://localhost:8000** — login `admin`. Parol o'rnatishda bergan parolingiz;
   `start_windows.bat` bilan esa `admin123`. Standart parol bilan faqat shu kompyuterning o'zidan kiriladi.

### macOS / Linux
```bash
cd hushyor-ai
chmod +x start_mac_linux.sh
./start_mac_linux.sh
```
macOS da kamera ishlashi uchun: *Tizim sozlamalari → Maxfiylik va xavfsizlik → Kamera* bo'limida
Terminal'ga ruxsat bering.

### Qo'lda (har qanday tizim)
```bash
python -m venv .venv
.venv\Scripts\activate          # Windows
source .venv/bin/activate       # macOS/Linux
pip install -r requirements.txt
python run.py
```

---

## 2. Birinchi sinov (5 daqiqa)

1. **Telegram:** panelda *Telegram bot* bo'limini oching → botni oching → 6 xonali kodni yuboring.
   Bot "✅ Ulandingiz!" deb javob beradi.
2. **Kamera:** *Jonli kuzatuv* → **Kompyuter kamerasi** tugmasi. Ekranda tanangiz ustida skelet chiziqlari paydo bo'ladi.
3. **Harakat:** kameradan 1–2 m uzoqroq turing (yelka va bel ko'rinsin). Telefon yoki shishani qo'lga olib,
   ko'tarib turing, keyin cho'ntakka / ko'ylak ichiga soling va qo'lni ~1 soniya ushlang.
4. **Natija:** 5–10 soniyada klip *Hodisalar* bo'limiga va Telegramga keladi.
   Signal kelmasa — yuqoridagi **Sinov** rejimini tanlang (eng sezgir).
5. **Video bilan:** *Video tahlili* bo'limiga internetdan olingan yoki telefonda yozilgan videoni yuklang.
   Tayyor namuna: `samples/dokon_namuna_20s.mp4` — oddiy xarid videosi (o'g'irlik yo'q):
   "Oddiy" rejimda signal bermasligi, "Sinov" rejimida esa 1 ta signal berishi kerak.

---

## 3. Web panel bo'limlari

| Bo'lim | Nima qiladi |
|---|---|
| Boshqaruv paneli | Bugungi statistika, faol kameralar, oxirgi hodisalar, soatlar bo'yicha diagramma |
| Jonli kuzatuv | Kameralar real vaqtda: skelet, buyumlar, xavf darajasi; sezgirlikni tez almashtirish |
| Video tahlili | Video yuklash (fayl yoki to'g'ridan-to'g'ri .mp4 havola), tez tahlil yoki jonli rejim |
| Hodisalar | Barcha signallar: filtr, klip, Tasdiq/Xato, izoh, CSV eksport |
| Kameralar | Veb-kamera, IP kamera (RTSP), video fayl qo'shish; avtomatik yoqish; zonalar |
| AI model | Model aniqligi, versiyalar, qo'riqchi javoblari va PoseLift bilan qayta o'qitish |
| Telegram bot | Ulanish kodi, obunachilar, sinov xabari, yuborish sozlamalari |
| Hisobotlar | Kunlar/soatlar/kameralar bo'yicha, signal aniqligi, javob vaqti |
| Aniqlik sinovi | O'z videolaringizda hodisalarni belgilash va aniqlik/topishni o'lchash |
| Sozlamalar | Sezgirlik rejimi, chegara, klip uzunligi, signal turlari, AI tezligi |
| Tizim holati | CPU/RAM, versiyalar, kameralar tezligi, jurnal |
| Yo'riqnoma | Qanday ishlaydi, maslahatlar, cheklovlar, maxfiylik |

---

## 4. Qanday ishlaydi

```
Kamera ─► halqali bufer (oxirgi ~14 s)
   │
   ├─► Kamera holati: bo'sh (modelsiz) | tinch (skelet, 4/s) | faol (skelet 8/s + buyum)
   ├─► YOLO11n-pose (ONNX): odamlar + 17 ta tana nuqtasi
   ├─► YOLO11n (ONNX): faqat faol qo'l atrofida — qo'ldagi buyum; odam atrofida — sumka
   ├─► Zonalar: polka (qo'l), kassa / eshik / xodim (oyoq), e'tiborsiz
   ├─► Kuzatuvchi: har odamga doimiy ID
   ├─► Qoidalar: qo'l faol holatdan → bel/cho'ntak | qorin (kiyim ichi) | sumka | kadrdan pastga
   │            + buyum qo'lda edi va yo'qoldi, atrofga qarash
   │            − yurib ketayotgan odam, orqa tomondan ko'rinish
   ├─► Xulq modeli (Gradient Boosting, 97 ta skelet belgisi, 2,5 s oyna)
   │
   └─► Ball ≥ chegara ─► klip (shubhadan 10 s oldin + 10 s keyin, shubhali lahza qizil belgilangan, H.264 MP4) ─► Telegram + web panel
                                   ─► qo'riqchi ✅/❌ ─► bazaga ─► modelni qayta o'qitish
```

* **PyTorch kerak emas** — modellar ONNX formatida, ONNX Runtime orqali CPU'da ishlaydi.
  NVIDIA GPU bo'lsa: `pip uninstall onnxruntime && pip install onnxruntime-gpu`.
* Bo'sh va tinch kameralar deyarli kuch sarflamaydi; bir vaqtda 3–4 tadan ko'p faol kamera bo'lsa
  ular sekinlashadi. Ko'p kamera uchun GPU li server kerak.

### Modelni o'qitish
* Boshlang'ich xulq modeli **sintetik** skelet harakatlarida o'qitilgan (sinov to'plamida aniqlik 97,5%,
  ROC-AUC 0,99). Bu raqamlar sintetik ma'lumotniki — haqiqiy do'kondagi aniqlik qo'riqchi javoblari bilan o'lchanadi.
* Har bir ✅/❌ javob saqlanadi. 50+ javob to'plangach *AI model → O'qitishni boshlash* ni bosing.
* Haqiqiy o'g'irlik skeletlari: [PoseLift datasetini](https://github.com/TeCSAR-UNCC/PoseLift) yuklab, papkasini
  *AI model* sahifasida ko'rsating. Buyruq qatoridan: `python -m app.ml.train --synthetic 3000 --poselift C:\data\PoseLift`

### Sinovlar
```bash
python -m tests.test_analyzer normal     # qoidalarni sintetik harakatlarda sinash
python -m tests.eval_synthetic 40 normal 1   # realistik baholash: 7.5 kadr/s, telefon, savat, ikki odam
python -m tests.eval_pipeline 40 normal 1    # to'liq quvur: kamera rejimlari + buyumni faqat kerakda qidirish
python -m tests.test_activity             # kamera rejimlari, model navbati, mahsulot belgisi
python -m tests.test_zones                # zonalar: API va qoidalar
python -m tests.test_eval                 # aniqlik sinovi: solishtirish va API
python -m tests.test_bot_menu             # eski bot menyusini tozalash
python -m tests.test_models               # ko'rish modellari: to'plamlar, ONNX Runtime / OpenVINO, API
python -m tests.test_fixes                # 1.4.1 tuzatishlari: login himoyasi, CSRF, model yuklash, Telegram navbati
python -m tests.test_sync                 # 1.5.0 edge agent: bulut navbati, uzilish, takrorlanmaslik, tartib
python -m tests.test_cloud                # 1.6.0 bulut: rollar, filial tokeni, edge ulanishi, review → edge (SQLite; CLOUD_TEST_PG_URL bilan PostgreSQL ham)
python -m tests.test_notify               # 1.7.0 bildirishnomalar: Telegram/email/push, rollar, eskalatsiya, aloqa uzilishi, SSE
python -m tests.test_panel                # 1.7.0 bulut paneli haqiqiy brauzerda (requirements-dev.txt + playwright install chromium)
python -m app.ai.model_zoo open           # ochiq modellarni yuklab olish (internet kerak)
python -m tests.test_latency 100          # signal tezligi: 100 hodisa, Telegram rasm/klip va web panel
python -m tests.test_health               # kamera uzilishi va tiklanishi xabarlari
python -m tests.test_web video.mp4        # to'liq sinov: web + AI + soxta Telegram API
python -m tests.test_security             # 1.8.0 xavfsizlik (OWASP): edge + bulut (CLOUD_TEST_PG_URL bilan PostgreSQL ham)
python tests/soak.py --hours 72           # 1.8.0 barqarorlik sinovi (alohida papkada; qisqasi: --hours 1 --cycle-min 15)
python tests/model_gate.py                # 1.8.0 model darvozasi (Aniqlik sinovi natijalaridan)
```

---

## 5. Sozlash (`.env` fayli)

| O'zgaruvchi | Ma'nosi |
|---|---|
| `TELEGRAM_BOT_TOKEN` | Bot tokeni (BotFather). Almashtirsangiz — dasturni qayta ishga tushiring |
| `ADMIN_USER`, `ADMIN_PASSWORD` | Web panel logini va paroli — **parolni o'zgartiring** |
| `PORT` | Web panel porti (standart 8000) |
| `PUBLIC_URL` | Panel internetda ochiq bo'lsa — Telegram xabarida "Panelda ochish" tugmasi chiqadi |
| `AI_THREADS` | AI uchun CPU oqimlari (standart: yadrolar − 2, ko'pi bilan 4). Kompyuter qotsa — kamaytiring |

Bitta token bilan faqat bitta dastur ishlashi mumkin (Telegram cheklovi). Agar bot "Conflict" xatosini
ko'rsatsa — shu tokenni ishlatayotgan boshqa dasturni to'xtating.

---

## 6. Cheklovlar va mas'uliyat

* Bu prototip. Xato signallar bo'ladi — shuning uchun har bir signalni odam tekshiradi.
* Standart model polkadagi ko'p mahsulotlarni (paket, quti) "buyum" deb tanimaydi; tizim asosan qo'l harakatiga tayanadi.
* Kassa/self-checkout firibgarligi (skanerlanmagan mahsulot) bu versiyada yo'q — POS integratsiyasi kerak.
* Yuz tanish ishlatilmaydi. Videolar faqat shu kompyuterda saqlanadi.
* Do'konga "Videokuzatuv olib boriladi" ogohlantirishini qo'ying; shaxsga doir ma'lumotlar bo'yicha yurist bilan maslahatlashing.
* YOLO11 modellari Ultralytics'niki (AGPL-3.0). Ichki foydalanish uchun muammo yo'q; mahsulotni **sotish**
  rejasi bo'lsa — Ultralytics Enterprise litsenziyasi yoki Apache-2.0 modellar (RTMPose, RT-DETR) ga o'tish kerak.

## 7. Papkalar
```
app/            dastur kodi (web, AI, Telegram, bulut navbati — sync.py, ko'rsatkichlar — metrics.py)
app/ai/         detektor, kuzatuvchi, tahlilchi, klip kesish, kamera oqimlari
cloud/          bulut serveri: db.py (SQLAlchemy sxema, rollar), server.py (Sync API v1 + boshqaruv API v1),
                notify.py (Telegram/email/push), live.py (jonli oqim), service.py; cloud/data/ — SQLite baza va kliplar
cloud/panel/    bulut paneli manbasi (Next.js); cloud/panel_dist/ — tayyor build (server shuni beradi)
docs/           SYNC_API.md — edge ↔ bulut shartnomasi
app/ml/         xulq modeli: belgilar, sintetik ma'lumot, PoseLift, o'qitish
models/         ONNX modellar va o'qitilgan xulq modeli
data/           baza, kliplar, yuklangan videolar, jurnal (avtomatik yaratiladi)
data/eval/      aniqlik sinovi videolari;  data/snapshots/ — zona chizish uchun kamera kadrlari
tests/          sinovlar va soxta Telegram server
samples/        namunaviy do'kon videosi (CC BY 4.0)
```
