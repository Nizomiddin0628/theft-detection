# Demo, sayt va so'rovlar (2.6.0, 10-bosqich)

Maqsad: mijozga Hushyor AI ni 10 daqiqada ko'rsatish (do'konga borib yoki video qo'ng'iroqda), saytdan so'rov
yig'ish va pilotga olib chiqish.

| Nima | Qanday ochiladi | Manzil | Login |
|---|---|---|---|
| Do'kon demosi (1 do'kon, 30 kun) | `demo_windows.bat` | http://127.0.0.1:8090 | `demo` / `demo12345` |
| Bulut demosi (3 filial) | `.venv\Scripts\python -m cloud.demo` | http://127.0.0.1:9090 | `demo@hushyor.uz` / `demo12345` (kirish oynasida yozilgan) |
| Sayt (landing) | bulut serveri bilan birga | `/sayt/` (demo: http://127.0.0.1:9090/sayt/) | — |

## 1. Do'kon demosi

```powershell
cd <loyiha papkasi>                # masalan, Hushyor AI o'rnatilgan papka
.\demo_windows.bat                 # birinchi marta ~10 s ma'lumot yaratadi, keyin brauzer o'zi ochiladi
.\demo_windows.bat --reset         # ma'lumotni qaytadan yaratish (bugungi sana bilan)
.\demo_windows.bat --port 8091     # 8090 band bo'lsa
```

* **Alohida papka** — `data\demo`. Haqiqiy baza (`data\hushyor.db`), Telegram bot, bulut va SMS ga **tegmaydi**:
  `.env` dagi tokenlar demo jarayonga berilmaydi. Ishlab turgan Hushyor AI bilan bir vaqtda ishlaydi.
* Panel faqat shu kompyuterdan ochiladi (127.0.0.1). Har sahifada "DEMO rejimi" yozuvi turadi.
* Demo AI'ni 3 ta namuna videoda ishlatadi (protsessor yuki). Do'kon kompyuterida asosiy dastur bilan birga uzoq
  qoldirmang — mijozga ko'rsatish uchun noutbukdan foydalaning.
* Ma'lumot oldingi kuni yaratilgan bo'lsa, demo ochilganda bugungi sana bilan avtomatik qayta yaratiladi (eskirgan
  "bo'shliq" ko'rinmasin). Yaratish yarim yo'lda to'xtatilgan bo'lsa ham keyingi safar qaytadan yaratiladi.
* Kameralar o'rnida `samples\` videolari aylanadi — AI jonli ishlaydi (Jonli kuzatuv: skelet, zonalar, navbat, sanash).
* O'tgan 30 kun ma'lumoti (har ishga tushirishda bir xil ssenariy): tashriflar (soat va hafta kuni bo'yicha), kassa
  navbati (17:00–19:00 gavjum), o'g'irlik signallari (klip bilan, ✅/❌ va kim javob bergani), 4 ta xavfsizlik signali,
  do'kon rejasi (6 bo'lim, kalibrlangan zal kamerasi, 14 kunlik heatmap), 14 kunlik va 3 haftalik hisobot (tavsiyalar
  va baholar bilan), 3 hafta oldin boshlangan pilot va egasining bahosi.
* Bugungi raqamlar soat bo'yicha to'ladi: ertalab 9:00 gacha ochilsa, "bugun" bo'limlari bo'sh ko'rinadi —
  ko'rsatishdan oldin `--reset` qiling yoki kunduzi oching.
* To'xtatish: demo oynasida **Ctrl+C** (yoki oynani yoping). Ma'lumot `data\demo` da qoladi; o'chirish — papkani
  o'chiring.

## 2. Bulut demosi (ko'p filial)

```powershell
.venv\Scripts\python -m cloud.demo             # http://127.0.0.1:9090
.venv\Scripts\python -m cloud.demo --reset     # qaytadan yaratish
```

* Alohida baza: `cloud\data\demo` (haqiqiy bulut bazasi, bot, email, filial tokenlari — tegilmaydi).
* "Namuna savdo (DEMO)": **Chilonzor**, **Yunusobod**, **Samarqand** filiallari. Filiallar ataylab har xil — Taqqoslash
  sahifasida tavsiyalar chiqadi: Yunusobodda kutish 2 barobar uzoq, Samarqandda xato signallar ko'p va o'tgan haftada
  kompyuter bir kun o'chiq bo'lgan, kassa kamerasi hozir uzilgan.
* Server ishlab turganda har daqiqa filiallardan "aloqa" taqlid qilinadi — filiallar onlayn, kassalar jonli.
* Qo'shimcha xodimlar: `menejer@demo.hushyor.uz` / `Menejer2026` (2 filial), `qorovul@demo.hushyor.uz` /
  `Qorovul2026` (1 filial) — rollar farqini ko'rsatish uchun.
* Telefonda ko'rsatish: kompyuter va telefon bir Wi-Fi da bo'lsa ham demo faqat 127.0.0.1 da — telefonda ko'rsatish
  uchun brauzerning telefon ko'rinishidan (F12 → telefon belgisi) foydalaning.

## 3. Demo ssenariysi (10–12 daqiqa)

| Vaqt | Ekran | Nima deyish kerak |
|---|---|---|
| 0:00 | — | Muammo: "Kameralar bor, lekin hech kim qaramaydi. O'g'irlik, navbat, bo'sh kassa — keyin bilinadi." |
| 1:00 | Do'kon → **Jonli kuzatuv** | "Mavjud kameralar. AI har odamni kuzatadi — qo'l, sumka, polka. Yangi kamera shart emas." |
| 2:00 | **Hodisalar** → bitta signal | "Shubhali harakat — klip va sababi bilan Telegram'ga keladi. Qo'riqchi ✅ yoki ❌ bosadi. Hech kimni ayblamaydi — 'tekshiring' deydi." (Telefoningizdagi haqiqiy do'kon botidan bitta xabarni ham ko'rsating.) |
| 4:00 | **Boshqaruv paneli**, **Tashriflar** | "Bugun nechta xaridor kirdi, hozir ichkarida nechta, qaysi soat gavjum. Xodimlar alohida." |
| 5:00 | **Navbat** | "Kassada navbat va kutish vaqti, kassir joyida yo'qligi. Navbat uzaysa — darhol xabar." |
| 6:00 | **Oqim** | "Do'kon rejasida: qaysi bo'limga ko'p kirishadi, xaridorlar qanday yuradi. Mahsulot joylashuvi uchun." |
| 7:00 | **Hisobotlar** | "Har kuni 9:00 da kechagi kun, dushanba — hafta. Raqamlar va aniq tavsiyalar, PDF." |
| 8:00 | **Pilot** | "4 hafta pilot — mezonlar oldindan: dastur ≥ 98%, kameralar ≥ 95%, xato signal ≤ 3/kun… Natijani siz baholaysiz." |
| 9:00 | Bulut → **Bosh sahifa**, **Taqqoslash** | "Bir nechta filial bo'lsa — hammasi bir joyda, telefonda ham. Har dushanba filiallar taqqoslangan xulosa." |
| 10:00 | Sayt → forma | "Pilotni boshlash uchun: do'koningizni ko'rib chiqamiz — kameralar, kompyuter, internet." |

**Savollarga tayyor javoblar** — saytdagi "Ko'p beriladigan savollar" bilan bir xil: yangi kamera kerakmi, internet
uzilsa, videolar qayerda, xato qiladimi, yuzni taniydimi, ko'p filial.

### Qisqa video (3–4 daqiqa)

1. Ikkala demoni `--reset` bilan kunduzi oching. Ekran yozuvi: OBS Studio (1920×1080, 30 kadr/s), brauzer 100%
   masshtabda, ortiqcha tablar yopiq.
2. Ssenariy: 0:00 muammo (ovoz bilan, do'kon kamerasi kadri) → 0:20 Jonli kuzatuv → 0:45 Hodisalar va klip → 1:20
   telefonda Telegram xabari (haqiqiy botdan, ✅ bosish) → 1:50 Navbat → 2:15 Oqim → 2:35 Hisobot → 3:00 Bulut
   taqqoslash → 3:20 "Pilot so'rash" (sayt manzili ekranda).
3. Mijozlarning haqiqiy ma'lumoti (yuzlar, ismlar, telefonlar) videoga tushmasin — faqat demo.

## 4. Sayt (landing)

* Fayllar: `site/` (`index.html`, `style.css`, `app.js`, `img/`). Tashqi shrift, CDN va skript yo'q; qat'iy CSP
  (ichki skript taqiqlangan). Telefon birinchi — 360 px kenglikda ham gorizontal siljish yo'q.
* Bulut serveri `/sayt/` da beradi. Aloqa qatorlari **`.env` dan** (berilmagani ko'rsatilmaydi — raqam o'ylab
  topilmaydi):

  ```ini
  CLOUD_SITE_PHONE=+998 XX XXX XX XX
  CLOUD_SITE_TELEGRAM=@sizning_akkauntingiz      # yoki https://t.me/...
  CLOUD_SITE_EMAIL=
  CLOUD_SITE_ADDRESS=
  CLOUD_SITE_PRICE=                              # bo'sh — "so'rov bo'yicha"
  ```
* Domen ildizida sayt chiqishi uchun nginx'dagi `location = /` blokini yoqing (docs/DEPLOY.md): kirmagan mehmon —
  sayt, panelga kirgan xodim — panel (kirish `/login/` dan).
* Rasmlarni yangilash (panel ko'rinishi o'zgarganda): ikkala demo ochiq turganda `python scripts\site_shots.py`.

## 5. Saytdagi so'rovlar (lidlar)

* Forma → `POST /api/v1/leads` (faqat JSON, faqat o'z saytidan). Himoya: yashirin "honeypot" maydoni, forma
  ochilganidan 3 s dan tez yuborish (ikkalasi ham jim "qabul" — bot o'rganmasin), bitta IP dan soatiga 5 ta / kuniga
  20 ta (IPv6 — /64 tarmoq bo'yicha), kuniga 300 tadan keyin saqlanadi-yu xabar yuborilmaydi, 1000 tadan keyin rad
  etiladi; bitta telefon 24 soatda bir marta (takrori o'sha so'rovga qo'shiladi). IP saqlanmaydi (faqat HMAC).
* Xabar — bulut boti orqali Telegram guruhiga va/yoki emailga:

  ```ini
  CLOUD_TELEGRAM_BOT_TOKEN=...        # bulut boti (do'kon botidan alohida)
  CLOUD_LEADS_CHAT=-100...            # guruh: botni guruhga qo'shing va guruhda /chatid yozing
  CLOUD_LEADS_EMAIL=sotuv@...         # CLOUD_SMTP_* sozlangan bo'lsa
  ```
  Yuborilmasa — 1, 2, 4… daqiqada, keyin har soat qayta urinadi (2 kungacha). Kanal sozlanmagan paytda kelgan
  so'rovlar bazada turadi va kanal sozlangach (server qayta ishga tushgach) oxirgi 2 kundagilari yuboriladi.
  Bitta telefon 24 soatda qayta yuborsa, faqat "takror" soni oshadi (yangi matn saqlanmaydi — begona odam raqamni
  bilsa ham so'rovni o'zgartira olmaydi); saytga javob har doim bir xil "qabul qilindi".
* Holatlar: **yangi → bog'lanildi → pilot kelishildi | rad etildi**. Telegram xabaridagi tugmalar (📞 / 🤝 / ✖️)
  bilan, guruhda `/lidlar` — ro'yxat va darvoza. Serverda:

  ```bash
  python -m cloud.server --leads                       # ro'yxat va 10-bosqich darvozasi
  python -m cloud.server --lead 5 pilot "3 kamera, 1-noyabrdan"
  ```
  Admin API (`CLOUD_ADMIN_TOKEN`): `GET /api/v1/admin/leads`, `POST /api/v1/admin/leads/{id}` `{"status", "note"}`.

## 6. 10-bosqich darvozasi

| Mezon | Qanday tekshiriladi |
|---|---|
| Demo bitta buyruq bilan ochiladi, har bo'limda ma'lumot | `python -m tests.test_demo` (avtomatik) + o'zingiz `demo_windows.bat` |
| Sayt telefonda ishlaydi, forma Telegram'ga yetadi | sinov (360 px brauzer, soxta Telegram) + VPS'da: telefondan formani yuboring → guruhga keldi |
| ≥ 10 so'rov va ≥ 3 pilot kelishuvi | `python -m cloud.server --leads` → "O'TDI ✅" |
