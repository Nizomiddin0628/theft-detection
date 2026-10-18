# Hisobotlar, tavsiyalar va rahbariyat paneli (2.4.0, 8-bosqich)

## Do'kon kompyuteri (edge)

**Davrlar** (kompyuterning mahalliy vaqti): kunlik — kecha 00:00–24:00; haftalik — o'tgan dushanba 00:00 dan
yakshanba oxirigacha. Solishtirish — bir hafta oldingi xuddi shunday davr (kunlikda "o'tgan haftaning shu kuni" — hafta
kunlari farqi tashriflarga ta'sir qilmasin).

**Yuborish:** `report_time` (sukut 09:00) dan keyin, o'sha kun ichida (`reports` jadvali, har tur + davr — bitta
qator). Haftalik — faqat dushanba kuni. Rejalashtiruvchi har 30 s tekshiradi (xato bo'lsa ham to'xtamaydi).
Hisobotlar yoqilishidan (`report_since` — 2.4.0 o'rnatilgan vaqt) oldin tugagan davr yuborilmaydi; obunachi bo'lmasa
hisoblanmaydi ham. Kimga: Telegram obunachilari (`subscribers.reports = 1`; /hisobot yoki panelda o'chiriladi).
Yetkazish har chat bo'yicha (`report_sends`): matn yetgan chat — yetkazilgan (PDF yetmasa — faqat belgi, matn qayta
yuborilmaydi); yetmagan chatga 10 daqiqada bir qayta — tarmoq/Telegram xatosida o'sha kun oxirigacha, doimiy xatoda
(bot bloklangan, chat yo'q — 400/403) 2 marta. Rejalashtiruvchi yetkazgan birinchi vaqt — `reports.sent_at`
(darvoza shuni sanaydi). Paneldagi "Telegram'ga yuborish" ham `report_sends` ga yoziladi (`auto = 0`): rejadagi
vaqtdan keyin yuborilgani — rejalashtiruvchi takror yubormaydi (darvozada o'sha kun "qo'lda yuborilgan"); oldin
yuborilgani hisobga olinmaydi — rejadagi nusxa baribir boradi. Tugamagan davr (bugun, joriy hafta) hech qachon
belgilanmaydi. /hisobot tugmasidagi PDF — faqat so'ragan chatga, hisobga kirmaydi. Yuborish boshlangan hisobot
muzlatiladi (`frozen`) — qayta hisoblanmaydi (Telegram'dagi xabar, 👍/👎 tugmalari va darvoza tekshiruvi — o'sha
nusxa). Tugmada tavsiya kaliti xeshi (`rf:<hisobot>:<xesh>:<1|0>`).
Xabar — Telegram HTML matni (≤ 4000 belgi, butun qatorlar bilan qisqaradi) va PDF (`app/pdfmini.py`, tashqi kutubxonasiz:
grafiklar, jadvallar).

**Raqamlar manbai** — panel sahifalari bilan bir xil:

| Bo'lim | Manba |
|---|---|
| Tashriflar | `counts` (kirdi, xodim va ish vaqtidan tashqari kirmaydi), `counting.summary` |
| Navbat | `queue_minute` / `queue_customers` — `queues.summary`; uzun navbat soati: soatning ≥ 25% ida navbat ≥ `queue_max_len` (≥ 10 daqiqa kuzatilgan soat) |
| Bo'limlar | oqim yig'indisi (`flow.summary`) |
| O'g'irlik signallari | `events` (video tahlili — `job_id` bor — va xavfsizlik turlari kirmaydi): ✅ tasdiqlangan, ❌ rad etilgan, 🟡 javobsiz |
| Xavfsizlik | `events.kind` ∈ fall, lying, night, crowd, blocked, fire, fight |
| Kameralar | `metrics_minute.up_pct` ish vaqtida (ish vaqti — sanash sozlamalari), dastur o'chiq — tizim qatori yo'q daqiqalar |

**Tavsiya qoidalari** (`app/reports.py: recommend`, ko'pi bilan 6 ta, ogohlantirishlar oldin):

| Kalit | Shart |
|---|---|
| `queue:<zona>:<a>-<b>` | uzun navbat soatlari ketma-ket oraliq |
| `queue:p90` | 90% kutish ≥ `queue_max_wait_min` (≥ 20 xaridor) |
| `cam:<id>` | kamera ish vaqtida ≥ 30 daqiqa ishlamagan |
| `app_off` | dastur ish vaqtida ≥ 30 daqiqa o'chiq |
| `false` | ❌ ≥ 60% (≥ 5 javob) — eng ko'p xato bergan kamera bilan |
| `unreviewed` | javobsiz ≥ 3 va ≥ 30% |
| `safety_unrev` | javobsiz xavfsizlik signallari |
| `visitors:drop` / `visitors:rise` | tashriflar ≤ −20% / ≥ +25% (asos ≥ 50) |
| `drift` | kirdi–chiqdi farqi > 10% |
| `no_lines` | haftalikda: sanash chizig'i yo'q |

Baholar: `report_feedback` (hisobot, tavsiya kaliti, kim) — 👍 = foydali, 👎 = foydasiz; qayta bosilsa almashtiriladi.

**Telegram:** `/hisobot` — bugungi holat (o'tgan haftaning shu kuni bilan xuddi shu soatgacha solishtiriladi) va
tugmalar `rp:daily`, `rp:weekly` (PDF), `rp:off` / `rp:on`.

**Panel API** (kirish talab qilinadi): `GET /api/reports/list`, `GET|POST /api/reports/settings`,
`GET /api/reports/gate`, `POST /api/reports/generate` (`{"kind": "daily"|"weekly", "date": "YYYY-MM-DD"}`),
`GET /api/reports/{id}`, `GET /api/reports/{id}/pdf`, `POST /api/reports/{id}/send`, `POST /api/reports/{id}/feedback`.

## Darvoza

> Hisobot 2 hafta har kuni o'z vaqtida keladi, raqamlari to'g'ri, ≥ 3 ta tavsiya foydali.

`app/report_eval.py` (panel va `tests/report_gate.py` bir xil):

1. Oxirgi 14 ta tugagan kun (hisobotlar yoqilgan kundan keyingi) — har birining kunlik hisoboti rejadagi vaqtdan
   ≤ 30 daqiqa kechikib yuborilgan; ko'pi bilan 1 kun o'tkazib yuborilishi mumkin.
2. Yuborilgan har hisobotdagi tashriflar va o'g'irlik signallari soni hisobot yaratilgan paytda bazadan mustaqil SQL
   bilan qayta sanalganiga (`reports.checked`) teng — keyin hodisalar o'chirilsa (❌ larni tozalash, saqlash muddati)
   darvoza buzilmaydi.
3. Kamida 3 xil tavsiya 👍: faqat oynadagi yuborilgan kunlik hisobotlardagi baholar; tur bo'yicha (bitta kassaning
   turli soat oraliqlari — bitta tur); 👍 lar 👎 lardan ko'p bo'lgan tur — foydali.

```
python tests\report_gate.py          # 0 — o'tdi, 1 — o'tmadi, 2 — ma'lumot yetarli emas
```

Qanday o'tkazish: Telegram bot ulangan, kompyuter har kuni ertalab yoqiq (avtomatik ishga tushish — install_windows.bat),
14 kun davomida har hisobotdagi tavsiyalarga 👍/👎 bosing.

## Bulut: taqqoslash va haftalik xulosa

`GET /api/v1/compare?period=week|day&date=YYYY-MM-DD` (egasi, menejer — o'z filiallari): davr `CLOUD_TZ_OFFSET`
bo'yicha; sana berilmasa — oxirgi tugagan kun / hafta (seshanba kuni ham o'tgan hafta). Har filial: tashriflar
(`visits_minute`, + bir hafta oldingi davr), navbat (`queue_minute`), signallar va xavfsizlik (`events`, video tahlili
kirmaydi), ishlagan vaqt — tizim qatorlari (`metrics_minute`, `source_id = 0`, har daqiqa) ulushi, edge birinchi
ko'rsatkich yuborgan paytdan boshlab (hech yubormagan — `null`).

Tavsiyalar (`cloud/report.py: recommend`): `wait:<id>` — o'rtacha kutish boshqa filiallar medianasidan ≥ 1,5 barobar
va ≥ 60 s (≥ 30 xaridor); `false:<id>` — ❌ ≥ 60% (≥ 5 javob); `unrev:<id>` — javobsiz ≥ 5 va ≥ 30%; `safety:<id>`;
`uptime:<id>` — < 90%; `visits:<id>` — ≤ −20% (asos ≥ 100; kompyuter o'chiq bo'lgan filialda aytilmaydi — sabab o'sha).

Haftalik xulosa (`cloud/notify.py: weekly_digest`): dushanba 09:00 dan keyin yoki seshanba, haftasiga bir marta
(`kv.digest_week`); egasi va menejerlar (sozlama `digest`, sukut yoqilgan), Telegram va email manzillariga, har kimga
o'z filiallari bo'yicha.
