# Xavfsizlik va salomatlik (2.2.0, 6-bosqich)

Kod: `app/ai/safety.py` (qoidalar), `app/sms.py` (SMS), `app/safety_web.py` (panel API), `app/safety_eval.py`
(darvoza hisobi), `tests/safety_gate.py` (buyruq qatori), sahifa `/xavfsizlik`, Telegram `/xavfsizlik`.

## Signal yo'li

Xavfsizlik signallari o'g'irlik signallari bilan **bir xil yo'ldan** o'tadi: `Processor.step()` qaytargan alert →
`manager.create_event` (rasm, Telegram, klip, ✅/❌) → bulut (`/edge/events`, yangi API kerak emas — `kind` maydoni).

| | O'g'irlik | Xavfsizlik |
|---|---|---|
| Ball | 0–100, chegara | doim 100, daraja "yuqori" |
| Kimga | xodim zonasidagilar chiqariladi | hamma (xodim ham) — yiqilish va yotish uchun |
| Matn | "Shubhali harakat — tekshirib ko'ring", "Bu ayblov emas" | "🆘 Yiqilish — yordam kerak bo'lishi mumkin" va h.k. |
| Bulut min_score (egasi 75) | to'sadi | to'smaydi |
| O'qitish (`app/ml/train.py`), aniqlik sinovi (`evaluation`) | ishlatiladi | aralashmaydi |
| SMS | yo'q | tanlangan turlar |

`Processor.step()` da `safety.update()` ikkala yo'lda chaqiriladi: jim kadrda ham (tungi kirish, chiqish va olov
qoidalari kadr bilan ishlaydi; kutayotgan yiqilish tasdig'i vaqt o'tishi bilan yakunlanadi). Qoidadagi xato
ushlanadi (jurnalga daqiqada bir marta) — o'g'irlik tahlili to'xtamaydi. Video tahlilida (background) faqat
yiqilish, yotish, gavjumlik (kadr bo'yicha) va olov.

## Yiqilish va "odam yotibdi"

Har kuzatuvda (trek) skelet bo'yicha holat (`classify`):

* **H_ref** — tik bo'y: oxirgi 3 s dagi tik kadrlarning eng kattasi; skeletdan taxmin (yelka→son→tizza→to'piq / 0,8 —
  yon tomonga yotgan odamda ham to'g'ri) va trekdagi eng katta ramka.
* **yotgan**: son pastda (`hip_low = (ramka pasti − son) / H_ref ≤ 0,25`) VA gavda yotiq (tik balandligi ≤ 0,15·H yoki
  burchak ≥ 55°) yoki ramka ≤ 0,45·H yoki ramka ≥ 1,3 yassi. **Lekin** gavda tik (≥ 0,2·H, < 40°) bo'lsa —
  o'tirgan/cho'kkalagan, yotgan emas. Gavda aniq yotiq va past bo'lsa son biroz balandroq (≤ 0,33) bo'lishi mumkin.
  Son ko'rinmasa: ramka ≥ 1,2 yassi va ≤ 0,5·H. Ramka kadr pastiga tegsa (oyoq kadrdan tashqarida) — faqat juda yassi.
* **tik**: son o'z ramkasi bo'yining ≥ 0,4 qismida va ramka ≥ 0,6·H (uzoqlashayotgan odam kichrayadi — shuning uchun
  o'z bo'yiga nisbatan).

**Yiqilish**: oxirgi "tik" kadrdan ≤ 2 s ichida "yotgan"; yoki *cho'kib yiqilish* — tik → son yerda ≤ 2 s, keyin ≤ 1,5 s
ichida yotdi, hammasi ≤ 2,5 s. **Tasdiq**: keyingi 2 s ichida tik turmadi. Trek yo'qolsa (yerdagi odamni detektor
ko'pincha topmaydi) — yerda deb hisoblanadi; o'sha joyda **o'sha bo'yli** tik odam paydo bo'lsa — bekor (turib ketgan).
Kuzatuvchi trekka ulamagan past ishonchli (0,25–0,4) detektsiya yo'qolgan trek o'rnida bo'lsa — o'sha odamning davomi.
To'silishdan keyin yangi ID — yo'qolgan trek o'rnida, tik bo'lmasa — o'sha odam (holati meros).

**Odam yotibdi**: "yotgan" ≥ 20 s (oynada ≥ 60% kuzatuv yotgan, tik yo'q, uzilish ≤ 6 s, markaz siljishi ≤ 0,6·H).
Yiqilish signali bo'lgan joyda 5 daqiqa, "yotibdi" signali bo'lgan joyda 10 daqiqa takrorlanmaydi (detektor yotgan
odamni goh yo'qotib, yangi ID bilan qayta topadi — har safar yangi signal va SMS bo'lmasin).

Sezgirlik "yuqori": son ≤ 0,33 va "qulab, darhol ko'rinmay qoldi" (ramka ≤ 0,65·H, tepasi ≥ 0,3·H tushgan) — polka
orqasiga cho'kkalash ham shunday ko'rinadi, ko'proq xato signal.

### O'lchovlar (rostini aytsam)

Linux, 2 yadro, YOLO11n-pose (ONNX Runtime va OpenVINO bir xil natija), AI qadami jonli kameradagi kabi (tinch rejim
4 kadr/s, faol 8 kadr/s):

| Sinov | Natija |
|---|---|
| Sintetik skelet: 192 yiqilish (0,5–1,8 s, 140–450 px, oldinga/orqaga, shovqin, detektor yo'qotgan) | 192/192, har biri aynan 1 signal |
| Sintetik salbiy: cho'kkalash, egilish, tiz cho'kish, polga o'tirish, uzoqlashish (148 ta) | 0 xato signal |
| ambianic/fall-detection 5 video (oxirgi kadr 3,5 s takrorlangan) | **3/5** (1, 2, 3); signal yerga tushgandan 2–2,2 s keyin |
| — 4-video | oxiri g'ijimlangan (to'liq yotmaydi), video tugaydi — topilmadi |
| — 5-video | detektor odamni yiqilish paytida yo'qotadi — topilmadi ("yuqori" sezgirlikda ham) |
| do'kon namunalari (samples/*.mp4, ~5 daqiqa) | 0 xato signal (oddiy sezgirlik); "yuqori" da 1 ta xato |
| Qo'shimcha hisob | ~0,15 ms/qadam; olov yoqilgan holda ~0,75 ms/qadam |

Bu **do'koningizdagi natija emas**. Ochiq videolar qariyalar uyi, kamera boshqa balandlikda, video yiqilishdan keyin
tugaydi. Ma'lum xavf: **to'rt oyoqlab** (qo'l va tizzada) polka ostiga qarash yiqilishga o'xshaydi; tez yotib olgan
odam ham. Darvoza faqat do'konda o'tadi.

## Tungi kirish

Jadval: `safety_night_hours` ("22:30-07:30") yoki ish vaqti (`work_hours`, Tashriflar sahifasi) — undan tashqarida,
yopilgandan keyin va ochilishdan oldin `safety_night_grace_min` (20) daqiqa hisobga olinmaydi. Jadval yo'q — qoida
ishlamaydi (sahifada aytiladi). Odam ≥ 2 s (≥ 3 detektsiya) ko'rinsa — signal; bitta tashrif (2 daqiqa uzilishsiz) —
bitta signal; keyingisi `safety_cooldown_min` dan keyin. Bir nechta kamera bir kirishni ko'rsa — 2 daqiqa ichida
do'kon bo'yicha bitta signal (bitta SMS).

## Gavjumlik

* Do'kon bo'yicha: "hozir ichkarida" (sanash chizig'i: kirdi − chiqdi, Tashriflar bilan bir xil hisob) >
  `safety_crowd_inside` ≥ `safety_crowd_sec` (60 s). Signal eng ko'p odam ko'rayotgan kameraga beriladi (rasm/klip
  o'sha kameradan). Kamera holati oqimidan ~10 s da bir tekshiriladi.
* Kadr bo'yicha: kamerada > `safety_crowd_cam` kishi ≥ 60 s. ≤ 10 s tushishlar (detektor odamni yo'qotishi) hisobni
  buzmaydi.

## Favqulodda chiqish to'silgan

Zona turi `favqulodda`. "Bo'sh holatini eslab qolish" — zona atrofi kulrang rasm (160 px enida) `data/safety/` ga,
ma'lumoti `safety_exits` ga. Zonada odam bo'lsa — eslab qolinmaydi. Har 5 s: zonada odam yo'q va ish vaqti bo'lsa,
8×8 katakchalar bo'yicha solishtiriladi: yorqinlik farqi (o'rtacha yorqinlik tekislangandan keyin) > 22 yoki chegaralar
zichligi farqi > 0,12 — katakcha o'zgargan. O'zgargan ulush ≥ `safety_blocked_frac` (35%) ≥ `safety_blocked_min`
(10) daqiqa va hozir ham to'silgan bo'lsa — signal (bitta epizodga bitta; 30 s bo'sh tursa epizod tugaydi). Ish vaqtidan
tashqarida hisob to'xtaydi va yangidan boshlanadi; qayta eslab qolinsa ham. O'rtacha yorqinlik > 60 farq qilsa
(chiroq o'chgan) — solishtirilmaydi. Zona nuqtalari o'zgarsa eslab qolingan rasm eskiradi.

## Olov (tajriba)

Sukut bo'yicha o'chiq. Kichraytirilgan kadrda olov rangi (R > 190, G > 100, R ≥ G > B, R − B > 70), odamlar ramkasi va
"e'tiborsiz" zonalar chiqariladi; miltillash — ketma-ket niqoblar farqi / birlashmasi ≥ 0,25. Kadrning ≥ 0,2% i,
≥ 10 s (namunalarning ≥ 70%). **Tutun aniqlanmaydi** — oddiy usul bilan ishonchli qilib bo'lmaydi. Qizil/to'q sariq
kiyim, lampa, ekran signal berishi mumkin. Yong'in signalizatsiyasining o'rnini bosmaydi.

## Janjal (2.3.0, tajriba)

Sukut bo'yicha o'chiq (`safety_fight`). Ikki odam yonma-yon (ramkalar orasidagi oraliq ≤ 0,6 × bo'y, bo'ylari 0,6–1,6
nisbatda — bir chuqurlikda, tik ustma-ust tushish ≥ 50%) va birining bilagi gavdaga nisbatan ≥ 4 gavda uzunligi / s
tezlikda ikkinchisiga qarab harakatlanib, uning ramkasiga yetadi — "zarba". Tezlik kamida 0,2 s oraliqda o'lchanadi
(8, 15 yoki 30 kadr/s — natija bir xil, nuqtalar titrashi zarba bo'lib ko'rinmaydi). Bitta cho'zilish bir necha kadrda
ko'rinsa ham — bitta zarba: keyingisi qo'l orqaga tortilgandan keyin sanaladi. Zarba beruvchining o'zi (son)
≤ 2 gavda / s siljisin (yonidan yurib o'tganda qo'l silkinishi hisoblanmaydi). **Bitta juftlikda** 4 s ichida ≥ 4 zarba
— signal (zalning ikki chetidagi ikki juftlik zarbalari qo'shilmaydi); takror `safety_cooldown_min` da bir.

Sintetik skeletlarda (4–30 kadr/s, titrash bilan): zarba (1–2 Hz) — topiladi; 3 zarba, gaplashib qo'l silkitish (30 s,
kuchli titrash bilan ham), qo'l berib ko'rishish, yonidan yurib o'tish, uzoqda havoga zarba, ikki alohida juftlikda
2 tadan zarba — signal yo'q; do'kon namunalarida (navbatda yonma-yon turganlar bilan) 0 signal. **Haqiqiy
janjal videolarida o'lchanmagan** (ochiq, litsenziyasi mos dataset topilmadi). O'yin, sport mashqi, qo'l silkitib
bahslashish ham signal berishi mumkin. Itarish va ushlashib olish (zarbasiz) aniqlanmaydi.

## Kritik SMS

Sozlamalar (bazada): `sms_enabled`, `sms_provider` (eskiz | http), `sms_phones`, `sms_kinds`, `sms_max_per_hour`.
Maxfiy qiymatlar faqat `.env`: `SMS_ESKIZ_EMAIL`, `SMS_ESKIZ_PASSWORD`, `SMS_FROM` (4546), `SMS_HTTP_URL`,
`SMS_HTTP_TOKEN`.

* **Eskiz.uz**: `POST https://notify.eskiz.uz/api/auth/login` (email, password) → token (30 kun; 25 kunda yangilanadi) →
  `POST /api/message/sms/send` (mobile_phone `998…`, message, from), `Authorization: Bearer`. 401 — qayta login (bir
  marta). **Eskiz har matn shablonini moderatsiya qiladi**: kabinetda shablonni oldindan tasdiqlating. Matn namunasi:
  `Hushyor: YIQILISH - <do'kon>, <kamera> 14:05. Telegramdagi rasmni ko'ring`. Tasdiqlanmagan matn yuborilmaydi —
  Eskiz xatosi `sms_log` va panel jurnalida ko'rinadi.
* **HTTP shlyuz**: `POST SMS_HTTP_URL`, JSON `{"phone", "text"}`, `Authorization: Bearer SMS_HTTP_TOKEN`; 2xx — yuborildi.
* Matn ASCII, ≤ 160 belgi. Bitta hodisa — har raqamga bir marta. Soatlik chegara (6). Video tahlili hodisalari uchun
  SMS yo'q. Yuborish alohida oqimda — kamera kutmaydi.

## Darvoza

Reja: **yiqilish recall ≥ 85%, xato ≤ 1 / kamera / hafta**.

* Recall: `safety_videos` (panelda yuklanadi, yiqilish soniyalari bilan). `evaluation.analyze(..., hold_last=3,5)`;
  mos kelish oynasi [belgi − 1 s, belgi + 10 s]; kerak ≥ 20 yiqilish. Salbiy videolar ("yiqilish yo'q") ham.
* Xato: jonli "Yiqilish" va "Odam yotibdi" hodisalaridagi ❌ / kamera-hafta (ishlagan vaqt — `metrics_minute`,
  up_pct ≥ 0,5 daqiqalar), har kamerada ≥ 7 kun, javobsiz hodisa qolmasin. Ikkalasi bir oynada: 2.2.0 o'rnatilgan
  vaqtdan (`safety_since`) va hodisalar saqlanadigan muddatdan (`retention_days`, ≤ 90 kun) — qaysi biri keyinroq.
* `python tests\safety_gate.py` — bazani faqat o'qiydi; chiqish kodi 0/1/2.

Do'konda: yumshoq gilam (yoki gimnastika to'shagi) ustida, ko'ngilli bilan, turli joyda (kamera yaqinida va uzoqda),
oldinga/orqaga/yonga, sekin cho'kib, kameraga qarab va yon tomondan; har yiqilishdan keyin 5–10 s yerda yotish.
Salbiy: pastki polkaga cho'kkalash, egilib poldan olish, tiz cho'kish.
