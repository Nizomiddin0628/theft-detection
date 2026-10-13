# Hushyor AI — do'kon kameralarida shubhali harakatni aniqlash

Kamera (kompyuter kamerasi, IP kamera yoki video fayl) orqali odamlarni kuzatadi. Kimdir mahsulotni
**cho'ntakka, kiyim ichiga yoki sumkaga yashirganga o'xshash** harakat qilsa, o'sha lahzaning qisqa
klipini avtomatik kesib, **Telegram botga** sabablari bilan yuboradi. Qo'riqchi videoni ko'rib
**✅ Tasdiqlash** yoki **❌ Xato signal** tugmasini bosadi. Tizim hech kimni ayblamaydi — u faqat
"tekshirib ko'ring" deydi, qarorni odam qiladi (Motive dashcam'dagi kabi "AI → klip → inson tekshiruvi").

---

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
3. `start_windows.bat` faylini ikki marta bosing.
   Birinchi marta kutubxonalar o'rnatiladi (3–10 daqiqa, internet kerak), keyingi safar darhol ochiladi.
4. Brauzer o'zi ochiladi: **http://localhost:8000** — login `admin`, parol `admin123`.

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
python -m tests.test_latency 100          # signal tezligi: 100 hodisa, Telegram rasm/klip va web panel
python -m tests.test_health               # kamera uzilishi va tiklanishi xabarlari
python -m tests.test_web video.mp4        # to'liq sinov: web + AI + soxta Telegram API
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
app/            dastur kodi (web, AI, Telegram)
app/ai/         detektor, kuzatuvchi, tahlilchi, klip kesish, kamera oqimlari
app/ml/         xulq modeli: belgilar, sintetik ma'lumot, PoseLift, o'qitish
models/         ONNX modellar va o'qitilgan xulq modeli
data/           baza, kliplar, yuklangan videolar, jurnal (avtomatik yaratiladi)
data/eval/      aniqlik sinovi videolari;  data/snapshots/ — zona chizish uchun kamera kadrlari
tests/          sinovlar va soxta Telegram server
samples/        namunaviy do'kon videosi (CC BY 4.0)
```
