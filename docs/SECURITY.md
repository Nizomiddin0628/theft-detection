# Hushyor AI — xavfsizlik tekshiruvi (OWASP Top 10, 1.8.0)

**Qamrov:** bulut serveri va paneli (`cloud/`), do'kon kompyuteridagi panel (`app/web.py`) va o'rnatish fayllari (`deploy/`, `scripts/windows/`).

**Usul:**

1. Kod qo'lda ko'rib chiqildi.
2. Har topilma uchun avtomatik sinov yozildi: `tests/test_security.py`. Bulut uchun SQLite va PostgreSQL'da, edge uchun alohida.
3. Kutubxonalar tekshirildi: `pip-audit` va `npm audit`.
4. nginx konfiguratsiyasi haqiqiy nginx bilan sinab ko'rildi: HSTS, Secure cookie, soxta `X-Forwarded-For`, SSE, 413, login cheklovi.

**Sana:** 2026-10-15.

Ishga tushirish:

```
python -m tests.test_security                                                    # edge + bulut (SQLite)
CLOUD_TEST_PG_URL=postgresql+psycopg://u:p@127.0.0.1/hushyor_test python -m tests.test_security   # + PostgreSQL
```

## Natija qisqacha

| OWASP | Bulut | Edge (do'kon) | 1.8.0 da nima qilindi |
|---|---|---|---|
| A01 Kirish nazorati | ✅ | ✅ | Har API yo'li kirishsiz yopiqligi sinovda tekshiriladi (bulut va edge). Boshqa kompaniya/filial hodisasi, klipi va xodimiga so'rov → 403/404 (IDOR). Egasi o'z rolini o'zgartira olmaydi, o'zini o'chira olmaydi. `/system` javobida server fayl yo'li chiqmaydi |
| A02 Kriptografiya | ✅ | ⚠️ | Parol scrypt bilan saqlanadi. Sessiya va qurilma tokenlari bazada faqat sha256 ko'rinishida. Bulut cookie: HttpOnly + SameSite=Strict + Secure (HTTPS ortida). nginx: TLS + HSTS. Edge lokal tarmoqda HTTP — pastga qarang |
| A03 In'ektsiya / XSS | ✅ | ✅ | SQL faqat parametrli. Panel (React) va edge shablonlari (Jinja2 autoescape) HTML'ni escape qiladi — sinov `<script>` bilan tekshiradi. CSV eksportida `= + - @` bilan boshlangan qiymat neytrallanadi. Telegram HTML matni escape qilinadi |
| A04 Xavfli dizayn | ✅ | ✅ | Login cheklovi: bitta IP+email 10 urinish, bitta IP 20, bitta email 30 (10 daqiqada). Parol almashtirishda 5 xato urinish. Jonli oqim: bir xodimga ≤ 8, serverga ≤ 1000. Klip ≤ 60 MB, so'rov tanasi chegaralangan. "Barcha qurilmalardan chiqish" bor |
| A05 Noto'g'ri sozlama | ✅ | ✅ | Barcha sahifalarda CSP, X-Frame-Options, nosniff, Referrer-Policy, Permissions-Policy. Shaxsiy ma'lumotga `Cache-Control: no-store`. Xato javobida stack/SQL chiqmaydi. `/docs` o'chiq. Ishga tushishda xavfli sozlamalar eslatiladi (qisqa admin token, HTTPS'siz ommaviy manzil, eski umumiy token). systemd cheklovlari: faqat `cloud/data` ga yoza oladi |
| A06 Eski kutubxonalar | ✅ | ✅ | Minimal versiyalar ko'tarildi: `starlette>=1.3.1`, `python-multipart>=0.0.31`, `jinja2>=3.1.6`, `h11>=0.16`, `fastapi>=0.135`. pip-audit natijasi: zaiflik yo'q. Panel: `npm audit` — 0 |
| A07 Autentifikatsiya | ✅ | ✅ | Parol siyosati: 8–200 belgi, keng tarqalgan va email bilan bir xil parol rad etiladi. Parol almashtirilsa, boshqa sessiyalar yopiladi. **Edge: standart `admin123` bilan faqat shu kompyuterdan kiriladi, tarmoqdan — yo'q.** Kirishda sessiya yangilanadi |
| A08 Ma'lumot yaxlitligi | ✅ | ✅ | Edge→bulut: idempotent kalitlar, token qurilmaga bog'langan, klip faqat o'z hodisasiga. Rasm o'rnida faqat JPEG qabul qilinadi. Model yuklashda ONNX chiqishlari tekshiriladi. Yangilashdan oldin `update.ps1` zaxira oladi |
| A09 Jurnal va kuzatuv | ✅ | ✅ | Audit jurnali 400 kun saqlanadi: kirish, xato kirish, chiqish, parol, xodim, filial, token, tekshirish, klip ko'rish, Telegram ulash. Panelda: **Xodimlar → Jurnal**. Bulut jurnali aylanma faylga yoziladi (5 MB × 5). Server holati — panelda |
| A10 SSRF | ✅ | ✅ | **Edge: havoladan video yuklash** endi lokal tarmoq, shu kompyuter va metadata (169.254.x) manzillariga bormaydi. Yo'naltirishlar ham qo'lda tekshiriladi. Bulut: push manzili faqat ma'lum push xizmatlariga (FCM, Mozilla, Apple, Microsoft) |

## 1.8.0 da topilgan va tuzatilgan muammolar

| # | Qism | Muammo | Xavf | Tuzatish |
|---|---|---|---|---|
| 1 | Bulut | Sinxron baza chaqiruvlari `async` handler ichida edi. Sekin so'rov butun serverni (jonli oqim, edge qabuli) to'xtatib qo'yardi | O'rta (DoS) | Baza ishi threadpool'ga ko'chirildi (`run_in_threadpool`) |
| 2 | Bulut | Panel CSP'sida skriptlar uchun `'unsafe-inline'` bor edi | O'rta | Har sahifaning ichki skriptlari sha256 xeshi bilan ruxsat etiladi. Begona kiritilgan skript ishlamaydi |
| 3 | Bulut | Xodimni boshqarish yo'q edi: parolni almashtirish/tiklash, rol, qaytarish, hamma joydan chiqish | O'rta | Yangi API va panel bo'limlari qo'shildi, hammasi audit jurnaliga yoziladi |
| 4 | Bulut | Parol siyosati faqat "≥ 8 belgi" edi (`12345678` qabul qilinardi) | O'rta | Keng tarqalgan parollar ro'yxati, email bilan bir xil parol taqiqlandi |
| 5 | Bulut | nginx ortida `X-Forwarded-For` istalgan manbadan qabul qilinardi: login cheklovini aldab o'tish mumkin edi | O'rta | `CLOUD_TRUSTED_PROXIES` qo'shildi. Sarlavha faqat ishonchli proksidan olinadi |
| 6 | Bulut | Kirish va muhim amallar jurnali yo'q edi | O'rta | `audit_log` jadvali va panel bo'limi qo'shildi |
| 7 | Bulut | Bitta xodim cheksiz jonli oqim ochib, serverni band qila olardi | Past | Xodimga 8 ta, serverga 1000 ta chegara |
| 8 | Bulut | `.env` dagi `CLOUD_DB_URL` / `CLOUD_DATA_DIR` e'tiborsiz qolardi. VPS'da baza noto'g'ri joyga yozilardi | O'rta | Endi `.env` dan ham o'qiladi |
| 9 | Bulut | Zaxira yo'q edi. Disk to'lsa kliplar cheksiz o'sardi | Yuqori (ma'lumot yo'qolishi) | `cloud/maintenance.py` qo'shildi: avtomatik zaxira, muddati o'tgan kliplarni tozalash, disk kam qolganda tozalash |
| 10 | Edge | Havoladan video yuklash har qanday manzilga so'rov yuborardi: router, IP kamera, `127.0.0.1` | O'rta (SSRF) | `public_url_ok()` qo'shildi: faqat internetdagi ommaviy manzillar. Har yo'naltirish ham tekshiriladi |
| 11 | Edge | Standart `admin123` paroli bilan tarmoqdagi istalgan qurilmadan kirish mumkin edi (faqat ogohlantirish bor edi) | Yuqori | Tarmoqdan rad etiladi va login sahifasida tushuntiriladi |
| 12 | Edge | CSRF tekshiruvida `Sec-Fetch-Site: same-site` ishonchli deb olinardi: shu kompyuterdagi boshqa portdagi sahifa buyruq yubora olardi | Past | `Origin` tekshiriladi |
| 13 | Edge | CSP va Permissions-Policy yo'q edi | Past | Qo'shildi (`object-src 'none'`, `base-uri`, `form-action`, `frame-ancestors 'none'`) |
| 14 | Edge | Kirishda eski sessiya ma'lumoti saqlanib qolardi | Past | Kirishda sessiya tozalanadi |
| 15 | Kutubxonalar | Eski `starlette` / `python-multipart` versiyalariga ruxsat bor edi. Ularda ma'lum zaifliklar bor: Host/yo'l orqali `request.url` ni soxtalash, multipart DoS, Windows'da UNC orqali SSRF | O'rta | Minimal versiyalar ko'tarildi |

## Qolgan xavflar (ongli qaror yoki keyingi bosqich)

1. **Edge paneli lokal tarmoqda HTTP orqali ishlaydi.** Bir Wi-Fi tarmog'idagi odam trafikni tinglab, sessiya cookie'sini ko'rishi mumkin. Tavsiyalar:
   - Edge panelini internetga ochmang (router'da port yo'naltirmang).
   - Tashqaridan kirish faqat bulut paneli orqali bo'lsin (HTTPS).
   - Kerak bo'lsa, `.env` da `HOST=127.0.0.1` qiling.
2. **Edge CSP'sida `'unsafe-inline'` qolgan.** Shablonlarda ichki skript va `onclick` lar ko'p. Hozirgi himoya: autoescape va qat'iy CSRF. Shablonlarni alohida JS fayllarga ko'chirish — keyingi bosqich ishi.
3. **Edge'da bitta admin bor, rollar yo'q.** Rollar va xodimlar bulut panelida. Edge paneli do'kon egasi yoki texnik uchun.
4. **Maxfiy kalitlar va ma'lumot shifrlanmagan:**
   - `.env` (Telegram tokeni, `EDGE_TOKEN`) va SQLite bazalari diskda ochiq saqlanadi. Windows foydalanuvchi huquqlari va BitLocker tavsiya etiladi.
   - Bulut zaxiralari ham shifrlanmagan (`chmod 600`). Ularni boshqa joyga ko'chirganda shifrlangan diskda saqlang.
5. **Edge `/api/sources` RTSP manzilini paroli bilan qaytaradi.** Buni faqat kirgan admin ko'radi.
6. **Ikki bosqichli kirish (2FA) yo'q.** Bulut paneli uchun TOTP — keyingi bosqich (ko'p filial, ko'p xodim bo'lganda).
7. **Login cheklovi xotirada saqlanadi.** Server qayta ishga tushsa, hisoblagich nollanadi. Bitta server jarayoni uchun bu yetarli; bir nechta jarayon bo'lsa, Redis kerak bo'ladi.

## Muntazam tekshirish

- Har yangilanishdan oldin: `python -m tests.test_security` va `pip-audit -r requirements.txt` (yoki `pip-audit` virtual muhitda).
- Har 3 oyda: `cd cloud/panel && npm audit --omit=dev`.
- Bulut panelida **Xodimlar → Jurnal** ni ko'rib chiqing: begona IP'lar va ketma-ket xato kirishlar bormi.
- Xodim ishdan ketsa: **Xodimlar → O'chirish**. Shu zahoti uning barcha sessiyalari yopiladi va bildirishnomalari to'xtaydi.
