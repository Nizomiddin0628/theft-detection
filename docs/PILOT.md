# Pilot do'konda (2.5.0, 9-bosqich)

Reja: 1–3 do'kon, 4–6 hafta. Maqsad — tizim do'konda dasturchisiz barqaror ishlashini, signallar foydali va xatolar
kamligini, hisobotlar har kuni kelishini va do'kon egasi uni foydali deb bilishini o'lchash.

## 1. O'rnatish kuni

1. `install_windows.bat` (docs/DEPLOY.md, 1-bo'lim): parol, Telegram tokeni, avtomatik ishga tushish.
2. Kameralar va zonalar: kassa/xodimlar joyi ("xodim" yoki "e'tiborsiz" zona), eshikda sanash chizig'i (tashriflar
   uchun), kassa navbati va reja — ixtiyoriy.
3. Panelda **Pilot** sahifasi → **Tayyorlik ro'yxati**: hamma ❌ ni tuzating.

| Band | ❌ / ⚠️ qachon | Nima qilish |
|---|---|---|
| Panel paroli | standart `admin123` | `.env`: `ADMIN_PASSWORD=` va dasturni qayta ishga tushirish |
| Telegram bot | token yo'q yoki obunachi yo'q | `.env`: `TELEGRAM_BOT_TOKEN=`; botga ulanish kodini yuborish (Telegram bot sahifasi) |
| Kameralar | kamera yo'q yoki ishlamayapti | Kameralar sahifasi: manzil, kabel, quvvat |
| Zonalar | zonasiz kamera (⚠️) | kassa va xodimlar joyini belgilash — xato signallar kamayadi |
| Ish vaqti | kiritilmagan (⚠️) | Tashriflar sahifasi: masalan `09:00-22:00` |
| Avtomatik ishga tushish | `HushyorAI` vazifasi yo'q | `install_windows.bat` |
| Dastur uzluksiz ishlashi | oxirgi 7 kunda ish vaqtida ≥ 1 soat (⚠️) / ≥ 8 soat (❌) o'chiq | Windows uyqu rejimini o'chirish, UPS, avtomatik ishga tushish |
| Diskda bo'sh joy | < 20 GB (⚠️), < 5 GB (❌) | kliplar saqlash muddatini qisqartirish yoki disk |
| Saqlash muddati | < 30 kun (⚠️); pilot davomida < 60 kun (❌) | Sozlamalar: hodisalarni saqlash ≥ 60 kun yoki 0 (cheksiz) |
| Kunlik hisobot | o'chiq yoki oluvchi yo'q (⚠️) | Hisobotlar sahifasi |
| Bulut | ulanmagan (⚠️, ixtiyoriy) / aloqa yo'q (❌) | docs/DEPLOY.md, 2-bo'lim |

Ro'yxat 10 daqiqada bir yangilanadi (🔄 tugmasi — hozir). Qisqasi bulutga hello bilan boradi: bulut panelining
**Filiallar** sahifasida "9/11 tayyor · tuzatish kerak: …" va pilot kuni.

## 2. Pilot davomida

* **Pilotni boshlash** (sana bilan yoki hozirdan). Pilot kalendar haftalari bo'yicha hisoblanadi.
* Qo'riqchilar har signalga ✅ yoki ❌ bosadi (javobsizlar ≤ 10% bo'lsin).
* Har dushanba haftalik hisobotdan 10 daqiqa keyin Telegram'da egasidan **"1–5"** baho so'raladi (hisobot oladigan
  obunachilarga). Bahodan keyin 10 daqiqa ichida yozilgan xabar — izoh. Panelda (Pilot haftalari jadvali) ham
  kiritish mumkin.
* Muammo bo'lsa — **Diagnostika paketi** (ZIP) ni dasturchiga yuboring: versiyalar, tizim, kameralar, sozlamalar,
  tayyorlik, darvozalar, oxirgi 7 kun raqamlari va jurnallar (har biri oxirgi 2 MB). Parol, token, kalit, RTSP
  login/paroli, ulanish kodi yashiriladi; rasm, klip va obunachilar ro'yxati kirmaydi.

## 3. Darvoza

> Pilot ≥ 28 kun va butun pilot davomida:

| Mezon | Chegara | Qanday hisoblanadi |
|---|---|---|
| Dastur ish vaqtida ishladi | ≥ 98% | ish vaqtidagi daqiqalar ichida tizim qatori (`metrics_minute`, `source_id = 0`) bor daqiqalar; dastur birinchi marta ishga tushishidan oldingi vaqt hisobga olinmaydi |
| Kameralar ishladi | ≥ 95% | dastur ishlagan ish vaqtida kameralarning `up_pct` o'rtachasi |
| Xato signal | har kamerada ≤ 3 / kun | ❌ o'g'irlik signallari / kamera ishlagan kunlar (7-bosqich bilan bir xil, o'chirilgan ❌ ham); faqat ≥ 7 kun ishlagan kameralar (yangi qo'shilgan kamera bitta ❌ bilan pilotni yiqitmasin) |
| Javobsiz signallar | ≤ 10% | o'g'irlik signallari ichida ✅/❌ berilmaganlar (o'chirilganlari arxivdan) |
| Kunlik hisobot | ≥ 90% kun | rejalashtiruvchi rejadagi vaqtdan ≤ 30 daqiqa kechikib yetkazgan kunlar (8-bosqich bilan bir xil) |
| Egasining bahosi | o'rtacha ≥ 4, kamida 3 ta | pilot haftalaridagi barcha baholar |

Holat: 28 kun to'lmaguncha — "ma'lumot yetarli emas"; keyin — "o'tdi" yoki "o'tmadi". **Pilotni yakunlash** bosilsa,
oyna o'sha kunda tugaydi (keyingi hodisalar natijaga kirmaydi). Yopilgan hafta raqamlari (`pilot_weeks`) saqlanadi —
keyin eski hodisalar o'chirilsa ham o'zgarmaydi. Haftalik so'rov yetmagan chatga 10 daqiqada bir, ko'pi bilan 3 marta
qayta yuboriladi.

```
python tests\pilot_gate.py          # 0 — o'tdi, 1 — o'tmadi, 2 — yetarli emas / boshlanmagan
```

Mezonlar: `app/pilot.py` (panel, Telegram `/pilot`, PDF va buyruq qatori bir xil). Pilot hisoboti (PDF) — darvoza
jadvali, haftalar, barcha bosqichlar darvozalari, tayyorlik va egasining izohlari.

## 4. Darvozalar jadvali

| Bosqich | Manba | Qachon "qo'llanmaydi" |
|---|---|---|
| 3. Odamlarni sanash | `count_eval` (2 × 1 soat qo'lda sanash ≥ 95%, kunlik og'ish ≤ 5%) | — |
| 4. Kassa navbati | `queue_eval` (±1 ≥ 90%, kutish xatosi ≤ 20%) | navbat zonasi yo'q |
| 5. Oqim | `flow_eval` (nuqta xatosi ≤ 0,5 m, qo'lda sanash) | do'kon rejasi yo'q |
| 6. Xavfsizlik | `safety_eval` (yiqilish recall ≥ 85%, xato ≤ 1 / kamera / hafta) | — |
| 7. O'g'irlik | `theft_eval` (xato ≤ 3 / kamera / kun) | — |
| 8. Hisobotlar | `report_eval` (14 kun o'z vaqtida, raqamlar, ≥ 3 foydali tavsiya) | — |
| 9. Pilot | `pilot` (yuqoridagi jadval) | — |

72 soatlik barqarorlik sinovi alohida: `python tests\soak.py --hours 72`.
