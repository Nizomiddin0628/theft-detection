# Hushyor AI — do'kon kameralarida shubhali harakatni aniqlash

Kamera (kompyuter kamerasi, IP kamera yoki video fayl) orqali odamlarni kuzatadi. Kimdir mahsulotni
**cho'ntakka, kiyim ichiga yoki sumkaga yashirganga o'xshash** harakat qilsa, o'sha lahzaning qisqa
klipini avtomatik kesib, **Telegram botga** sabablari bilan yuboradi. Qo'riqchi videoni ko'rib
**✅ Tasdiqlash** yoki **❌ Xato signal** tugmasini bosadi. Tizim hech kimni ayblamaydi — u faqat
"tekshirib ko'ring" deydi, qarorni odam qiladi (Motive dashcam'dagi kabi "AI → klip → inson tekshiruvi").

---

## 2.5.0 — 9-bosqich: pilotga tayyorgarlik

* **Yangi bo'lim: Pilot** (panel menyusida, Telegram'da **/pilot**).
* **Tayyorlik ro'yxati** — do'konga o'rnatgach bitta sahifada: panel paroli, do'kon nomi, Telegram (bot va
  obunachilar), kameralar (ishlayaptimi — nomi bilan), zonalar, sanash chizig'i, ish vaqti, avtomatik ishga tushish
  (Windows vazifasi `HushyorAI`), oxirgi 7 kunda dastur ish vaqtida o'chiq bo'lgan vaqt (uyqu rejimi), diskda joy,
  hodisalarni saqlash muddati, kunlik hisobot, bulut aloqasi. Har band ✅ / ⚠️ / ❌, izoh va "Tuzatish →" havolasi.
  Ro'yxatning qisqasi bulutga ketadi: bulut panelining **Filiallar** sahifasida "9/11 tayyor · tuzatish kerak: …" va
  pilot kuni ko'rinadi.
* **Darvozalar jadvali** — 3–8-bosqich darvozalari (sanash, navbat, oqim, xavfsizlik, o'g'irlik, hisobot) va pilot
  bitta jadvalda: o'tdi / o'tmadi / ma'lumot yetarli emas / qo'llanmaydi (masalan, navbat zonasi yo'q). Mezonlar —
  har bosqichning o'z sahifasidagi bilan bir xil.
* **Pilot** — "Pilotni boshlash" (istalgan sanadan). Kalendar haftalari bo'yicha: dastur va kameralar ishlagan vaqt,
  o'g'irlik signallari (✅/❌/javobsiz), signalga javob berish vaqti (mediana), xavfsizlik signallari, tashriflar,
  navbatda o'rtacha kutish, kunlik hisobot o'z vaqtida kelgan kunlar, egasining bahosi.
* **Egasining haftalik bahosi** — har dushanba (haftalik hisobotdan 10 daqiqa keyin) Telegram'da "Hushyor AI o'tgan
  hafta qanchalik foydali bo'ldi? 1–5" tugmalari; bahodan keyin 10 daqiqa ichida yozilgan xabar — izoh. Panelda ham
  kiritish mumkin.
* **9-bosqich darvozasi:** pilot ≥ 28 kun va butun pilot davomida — dastur ish vaqtining ≥ 98% ida ishlagan, kameralar
  ≥ 95%, o'g'irlik xato signali har kamerada ≤ 3 / kun, javobsiz signallar ≤ 10%, kunlik hisobot kunlarning ≥ 90% ida
  o'z vaqtida, egasining o'rtacha bahosi ≥ 4 (kamida 3 ta). Yopilgan hafta raqamlari saqlanadi (eski hodisalar
  o'chsa ham o'zgarmaydi); pilot davomida hodisalarni saqlash muddati ≥ 60 kun bo'lishi kerak (tayyorlik ro'yxati).
  `python tests\pilot_gate.py`. Batafsil: **docs/PILOT.md**.
* **Pilot hisoboti (PDF)** — darvoza mezonlari, haftalar jadvali, darvozalar jadvali, tayyorlik va egasining izohlari
  (do'kon egasiga yoki investorga ko'rsatish uchun).
* **Diagnostika paketi** — bitta tugma: ZIP (versiyalar, tizim, kameralar, sozlamalar, tayyorlik, darvozalar, oxirgi
  7 kun raqamlari va jurnallar). Parol, token, kalit, RTSP login/paroli va ulanish kodi yashiriladi; rasm, klip va
  obunachilar ro'yxati kirmaydi — dasturchiga masofadan yordam uchun.
* **Bulut (UzCloud):** `deploy/install_vps.sh` ga domen o'rniga IP berish mumkin — `203-0-113-7.sslip.io` nomi bilan
  Let's Encrypt sertifikati olinadi (domen keyin). `docs/DEPLOY.md` yangilandi (`bosqich-9`).
* **O'lchovlarim (rostini aytsam)** — pilot mezonlari sinov bazasida 30 kunlik soxta ma'lumotda tekshirildi (o'tdi /
  bir kun kompyuter o'chiq — o'tmadi / xato ko'p — o'tmadi), Telegram so'rovi soxta Telegram API'da. Haqiqiy natija —
  do'kondagi pilotda.

## 2.4.0 — 8-bosqich: hisobotlar va tavsiyalar, rahbariyat paneli

* **Kunlik va haftalik hisobot (do'kon kompyuteri).** Har kuni ertalab (sukut 09:00, Hisobotlar sahifasida
  o'zgartiriladi) kechagi kun bo'yicha, har dushanba — o'tgan hafta (dushanba–yakshanba) bo'yicha Telegram'ga qisqa
  xabar va **PDF** keladi: tashriflar (o'tgan haftaning shu kuni / o'tgan hafta bilan farq, eng gavjum soat/kun), navbat
  (o'rtacha va 90% kutish, uzun navbat soatlari), bo'limlar (oqim), o'g'irlik signallari (✅/❌/javobsiz, kamera
  bo'yicha), xavfsizlik signallari, ish vaqtida ishlamagan kameralar va dastur o'chiq bo'lgan vaqt. Dastur yoki
  kompyuter rejadagi vaqtda o'chiq bo'lsa — o'sha kuni yoqilganda yuboriladi (haftalik — dushanba kuni). Har chatga bir
  marta: yetmagan chatga o'sha kun davomida 10 daqiqada bir qayta, PDF yetmasa matn takrorlanmaydi; yuborilgan
  hisobot keyin qayta hisoblanmaydi.
* **Tavsiyalar (qoidalar asosida, ko'pi bilan 6 ta, muhimi oldin):** uzun navbat soatlari ("10:00–12:00 da qo'shimcha
  kassa oching"), 90% kutish chegaradan uzun, ish vaqtida ≥ 30 daqiqa ishlamagan kamera, dastur ≥ 30 daqiqa o'chiq,
  xato signallar ≥ 60% (eng ko'p xato bergan kamera bilan), javobsiz signallar, javobsiz xavfsizlik signallari,
  tashriflar −20% / +25%, kirdi–chiqdi farqi > 10% (sanash chizig'i joyi), sanash chizig'i yo'q. Har tavsiyaga
  Telegram'da yoki panelda **👍 / 👎** — darvoza shu baholarni sanaydi.
* **Telegram: /hisobot** — bugungi holat (hozirgacha) va tugmalar: kunlik PDF, haftalik PDF, hisobotlarni shu chat
  uchun o'chirish/yoqish (o'g'irlik signallari kelaveradi).
* **Hisobotlar sahifasi** (yuqorida yangi bo'lim): istalgan kun/hafta hisobotini yaratish, ko'rsatkichlar, tavsiyalar
  va baholar, PDF yuklab olish, Telegram'ga qayta yuborish, sozlamalar (vaqt, kunlik/haftalik, qaysi chatlarga),
  yuborilganlar tarixi va darvoza holati. Eski "Signallar tahlili" o'sha sahifada pastda qoldi.
* **Rahbariyat paneli (bulut): Taqqoslash** — barcha filiallar bitta jadvalda: tashriflar (bir hafta oldingi xuddi
  shu davrga nisbatan), o'rtacha kutish, signallar (✅/❌/javobsiz, xato ulushi), xavfsizlik, do'kon kompyuteri ishlagan
  vaqt; har ustunda eng yomon filial ajratiladi. Kun yoki hafta, istalgan sana. Filiallararo tavsiyalar: kutish
  boshqa filiallardan ≥ 1,5 barobar (va ≥ 60 s), xato signallar ≥ 60%, javobsiz signallar, ishlagan vaqt < 90%,
  tashriflar −20%. Egasi barcha filiallarni, menejer — o'zinikini ko'radi; qo'riqchiga ko'rinmaydi.
* **Haftalik xulosa (bulut):** har dushanba 09:00 dan keyin (bulut vaqt mintaqasi, `CLOUD_TZ_OFFSET`) egasi va
  menejerlarga Telegram/email orqali — filiallar taqqoslash va tavsiyalar, haftasiga bir marta (server dushanba o'chiq
  bo'lsa — seshanba). Sozlamalar → Bildirishnomalar → "Haftalik xulosa" bilan o'chiriladi.
* **Darvoza** (reja: *hisobot 2 hafta har kuni o'z vaqtida, raqamlari to'g'ri, ≥ 3 ta tavsiya foydali*): oxirgi 14 kun
  — har kungi hisobot rejadagi vaqtdan ≤ 30 daqiqa kechikib yetgan (ko'pi bilan 1 kun o'tkazib), yuborilgan har
  hisobotdagi tashrif va signallar soni yaratilgan paytda bazadan mustaqil qayta sanalganiga teng, kamida 3 xil
  tavsiya 👍 (bitta kassaning turli soatlari — bitta tavsiya). Hisobotlar sahifasida
  yoki `python tests\report_gate.py`. Mezonlar: `app/report_eval.py`. Batafsil: **docs/REPORTS.md**.
* Bulut paneli telefonda: pastki menyu 10 bo'limgacha yon tomonga suriladi, ochiq bo'lim doim ko'rinadi.
* **O'lchovlarim (rostini aytsam)** — raqamlar sinov bazasida qo'lda hisoblangani bilan solishtirildi (edge 81/81,
  bulut 75/75 SQLite va PostgreSQL'da); PDF va sahifalar brauzerda ko'rib chiqildi. Tavsiyalar qanchalik foydali —
  faqat do'konda, sizning 👍/👎 baholaringiz bilan bilinadi (darvoza).

## 2.3.1 — tuzatishlar (72 soatlik sinov topgan)

* **Yiqilish: xato signal tuzatildi.** Kameradan tez uzoqlashib, eshik tomon ketayotgan odam "yiqildi" deb chiqardi
  (sinov videosida 45 daqiqada 19 ta xato signal). Sabab: tik bo'y oxirgi 3 s dagi eng katta qiymatdan olinardi — odam
  shu vaqtda ~1,7 marta kichrayadi. Endi 1 s (odam yerda bo'lganda esa yiqilishdan oldingi qiymatda qotadi — kamera
  o'qi bo'ylab yotgan odam ham topiladi); tasdiq kutilayotganda odam yurib ketsa — signal bekor. Yiqilish videolarida
  topish o'zgarmadi (ambianic 3/5), sintetik sinovlar 174/174 (yangi: kamera o'qi bo'ylab yiqilish, eshikdan chiqish).
* **Telegram: signal yo'qolmaydi.** Telegram (yoki internet) bir daqiqa ishlamay tursa, o'sha paytdagi signal xabari
  butunlay yo'qolardi (faqat 3 ta tezkor urinish bor edi). Endi yetmagan har chatga 10 s, 30 s, 1, 2, 4 daqiqadan keyin
  qayta yuboriladi (15 daqiqadan eski hodisa — yo'q; yangi signallardan keyin navbatda); klip bosqichi bilan
  takrorlanmaydi va video o'sha xabarga qo'shiladi. Dastur qayta ishga tushsa, kutilayotgan qayta urinishlar yo'qoladi.
* **72 soatlik sinov (tests/soak.py):** Windows'da xotira to'g'ri o'lchanadi (`.venv\Scripts\python.exe` kichik
  ishga tushirgich — haqiqiy dastur uning bola jarayoni; avval faqat ~4 MB ko'rinardi va xotira oqishini sezmasdi);
  xavfsizlik signallari (2.2.0 dan sarlavhasi boshqa) "yetmagan" deb sanalmaydi.
* **Yangilash skripti** avtomatik ishga tushgan dasturni (`HushyorAI`, `HushyorCloud` vazifalari) o'zi to'xtatadi
  (jarayon chiqishini kutib) va oxirida — xato yoki Ctrl+C bo'lsa ham — qayta yoqadi. `install_autostart.ps1` panel manzilini `.env` dagi PORT bo'yicha yozadi.

## 2.3.0 — 7-bosqich: o'g'irlik v2 (qayta o'qitish, champion/challenger), janjal, dalil paketi

* **Champion / challenger** (AI model sahifasi): yangi xulq modeli endi sukut bo'yicha **challenger** sifatida o'qitiladi
  — faol model (champion) darhol almashmaydi. Challenger "soyada" ishlaydi: har signal paytida uning ehtimoli ham
  hodisaga yoziladi (`ml_ch`), lekin ball va signalga ta'sir qilmaydi. Solishtiruv faqat siz ✅/❌ bergan o'g'irlik
  hodisalarida: "challenger bo'lganda ball qancha bo'lardi" qayta hisoblanadi (o'sha signaldagi haqiqiy chegara bilan —
  zona/polka hisobga olinadi) — qancha ❌ chiqmay qolardi va qancha ✅ yo'qolardi, AUC. Ikki manba: **soya**
  (challenger yaratilgandan keyingi jonli hodisalar) — tavsiya faqat shundan; **ko'rmagan baholash** (o'qitishda har
  javob uni ko'rmagan model bilan baholanadi) — faqat ma'lumot. Tavsiya: ≥ 20 javob (har turdan ≥ 5), ✅ yo'qolmasin,
  ❌ kamaysin. "Faollashtirish" / "Bekor qilish" tugmalari; oldingi modelga qaytish —
  versiyalar jadvalidan. Cheklov: champion o'tkazib yuborgan o'g'irliklar bu yerda ko'rinmaydi (Aniqlik sinovi kerak).
* Faol model ko'rsatkichi endi `data/behavior_active.json` da (dastur bilan kelgan `models/behavior_active.json`
  o'zgarmaydi) — model almashtirgandan keyin ham git ishchi daraxti toza qoladi va yangilash skripti to'xtamaydi.
  2.2.0 da boshqa model tanlagan bo'lsangiz — yangilash skripti tanlovni `data/` ga ko'chiradi.
* **Janjal (tajriba, sukut bo'yicha o'chiq)** — Xavfsizlik sahifasida yoqiladi: ikki odam yonma-yon va biri ikkinchisiga
  qarab qo'lini keskin harakatlantiradi (bitta juftlikda 4 s ichida ≥ 4 "zarba"; 8/15/30 kadr/s da bir xil). Haqiqiy
  janjal videolarida o'lchanmagan — docs/SAFETY.md.
* **Dalil paketi** — hodisa sahifasida "Dalil paketi (ZIP + PDF)": klip, rasm, odam qirqimi (asl fayllar), PDF hisobot
  (do'kon, kamera, vaqt, turi, ball, sabablar, ball tarkibi, kim qachon tekshirdi, izoh, rasmlar, har faylning SHA-256
  xeshi), `manifest.json` va `TEKSHIRISH.txt` (`certutil -hashfile klip.mp4 SHA256`). Yangi kutubxona kerak emas.
* **Darvoza** (reja: *xato ≤ 3 / kamera / kun*): Aniqlik sinovi sahifasida "Jonli kameralarda xato signal" — har
  kamerada ❌ o'g'irlik signallari / ishlagan kun (≥ 7 kun, javobsizlar ≤ 10%; xavfsizlik signallari kirmaydi;
  o'chirilgan ❌ hodisalar ham sanaladi).
  `python tests\theft_gate.py`. Mezonlar: `app/theft_eval.py`. Batafsil: **docs/THEFT_V2.md**.
* `start_windows.bat` brauzer manzilini `.env` dagi `PORT` bo'yicha yozadi (avval doim 8000 deb yozardi).
* Buyruq qatori: `python -m app.ml.train --challenger` (champion'ni almashtirmasdan o'qitish).
* **O'lchovlarim (rostini aytsam)** — sintetik skelet va soxta javoblarda: ko'rmagan baholash har javobni qoplaydi,
  solishtiruv va faollashtirish mantiqi sinovdan o'tgan; janjal — sintetik zarba topiladi, 7 xil oddiy harakatda va
  do'kon namunalarida 0 signal. Haqiqiy natija — sizning javoblaringiz bilan (AI model sahifasi) va do'kondagi darvoza.

## 2.2.0 — 6-bosqich: xavfsizlik va salomatlik

* **Yangi bo'lim: Xavfsizlik** (panel menyusida, Telegram'da **/xavfsizlik**). Signallar o'g'irlik signallari bilan bir
  xil yo'ldan keladi (rasm, klip, ✅/❌, bulut), lekin "ayblov" emas — sarlavha "🆘 Yiqilish — yordam kerak bo'lishi
  mumkin" kabi, ball yo'q, xodimlar ham hisobga olinadi:
  * **Yiqilish** — skelet bo'yicha: tik holatdan 2 s ichida yerga tushdi (yoki cho'kib, keyin yotdi) va 2 s turmadi.
    Yerda yotgan odamni detektor ko'pincha ko'rmaydi — "yiqilib, ko'rinmay qoldi" ham hisoblanadi; o'sha joyda o'sha
    bo'yli tik odam paydo bo'lsa — bekor. Cho'kkalash, egilish, tiz cho'kish, polga o'tirish — signal emas (gavda tik).
    Sezgirlik "yuqori" — ko'proq topadi, ko'proq xato.
  * **Odam yotibdi** — yerda harakatsiz ≥ 20 s (yiqilishi ko'rinmagan bo'lsa ham).
  * **Tungi kirish** — ish vaqtidan tashqarida (yopilgandan keyin / ochilishdan oldin 20 daqiqa hisobga olinmaydi) yoki
    o'z jadvali bo'yicha kamerada odam ≥ 2 s.
  * **Gavjumlik** — do'konda "hozir ichkarida" (sanash) yoki bitta kadrdagi odamlar soni chegaradan ≥ 60 s oshsa.
  * **Chiqish to'silgan** — yangi zona **"Favqulodda chiqish"** + "Bo'sh holatini eslab qolish"; zona ≥ 10 daqiqa
    (ish vaqtida, zonada odam yo'q paytda) ≥ 35% o'zgargan tursa. Yorug'lik farqi tekislanadi, chiroq o'chsa
    solishtirilmaydi.
  * **Olov (tajriba, sukut bo'yicha o'chiq)** — olov rangi + miltillash ≥ 10 s. Tutun aniqlanmaydi.
  * **Kritik SMS** (`app/sms.py`): Eskiz.uz yoki umumiy HTTP shlyuz; telefonlar, qaysi turlar, soatlik chegara, sinov
    tugmasi, jurnal (`sms_log`). Login/parol faqat `.env` da (`SMS_ESKIZ_EMAIL`, `SMS_ESKIZ_PASSWORD`, `SMS_FROM`).
    Eskiz SMS matnini moderatsiya qiladi — shablonni oldindan tasdiqlating.
* Xavfsizlik hodisalari o'g'irlik modelini o'qitishga va o'g'irlik aniqlik sinoviga **aralashmaydi**; bulutda egasining
  ball chegarasi (75) ularni to'smaydi; bulut paneli va boti yangi turlarni ko'rsatadi.
* **Darvoza** (reja: *yiqilish recall ≥ 85%, xato ≤ 1 / kamera / hafta*): Xavfsizlik → Aniqlik sinovi — do'konda
  sahnalashtirilgan yiqilish videolari (soniyalari bilan, ≥ 20 ta) va jonli ❌ javoblar (har kamera ≥ 7 kun).
  `python tests\safety_gate.py`. Mezonlar: `app/safety_eval.py`. Batafsil: **docs/SAFETY.md**.
* **O'lchovlarim (rostini aytsam)** — Linux, 2 yadro, YOLO11n-pose: sintetik skelet 192/192 yiqilish, 148 salbiy
  harakatda 0 xato; ochiq videolar (ambianic, oxirgi kadr 3,5 s takrorlangan) **3/5**; do'kon namunalarida 0 xato
  signal; qo'shimcha ~0,15 ms/qadam. Bu do'koningizdagi natija emas — darvoza faqat do'konda o'tadi.

## 2.1.0 — 5-bosqich: oqim va heatmap (do'kon rejasi)

* **Yangi bo'lim: Oqim** (panel menyusida). Sozlash — **Oqim → Reja va kameralar**:
  1. **Reja**: do'kon chizmasini (PNG/JPG) yuklash yoki rasmsiz "eni × bo'yi" (metrda, 1 m katakli). Masshtab —
     rejada uzunligi ma'lum ikki nuqta + masofa. Rejadagi hamma narsa reja pikselida saqlanadi: masshtab keyin
     tuzatilsa, bo'limlar va kalibrlash o'zi to'g'rilanadi (eski heatmap yangisi bilan aralashmaydi).
  2. **Bo'limlar**: reja ustida ko'pburchak ("Sut", "Non"), rang, yoqish/o'chirish.
  3. **Kamerani rejaga bog'lash (kalibrlash)**: kadr va reja yonma-yon — kadrda pol nuqtasini, rejada o'sha joyni
     bosasiz (lupa bilan). Natija darhol: har nuqtaning LOO xatosi (shu nuqtasiz model uni qanchalik topadi), kadr
     ustida 1 metrlik to'r (pol plitkalariga mos tushishi kerak), rejada kamera qamrovi va model topgan joylar,
     "noto'g'ri bosilgan" gumonli nuqta, nuqtalar bir chiziqda/kadrning kichik qismida degan ogohlantirishlar.
     "Tekshiruv nuqtasi" — moslashda ishtirok etmaydi, xatoni halol ko'rsatadi. Saqlangach rejada jonli odamlar.
* **Model** (`app/ai/homography.py`): pol tekisligi gomografiyasi + linza egriligi (bo'linma modeli, k1 — 6+
  nuqtada, k2 — 10+ nuqtada; LOO xatosi bo'yicha, "oddiyroq model afzal" qoidasi bilan). Ufqqa yaqin va kadrda 1 m
  18 pikseldan kam joy (u yerda 2–3 px xato metrlab xato beradi) rejaga tushirilmaydi.
* **Hisob** (`app/ai/flow.py`; worker.py ga yangi ulanish kerak emas — 1.9.0 sanash ulanishi orqali):
  * odam oyog'i rejaga o'tkaziladi; oyoqlari polka orqasida ko'rinmasa — skelet bo'yicha taxmin (son + 1,7 × gavda);
    xodimlar hisobga olinmaydi;
  * **bo'lim tashrifi** — bo'limga kirib, to'xtagan yoki sekin yurgan (jami ≥ 2 s, shundan ≥ 1 s 0,45 m/s dan sekin,
    yoki bo'limda ≥ 8 s). To'xtamay o'tib ketgan, chegarada titragan, burchakni kesib o'tgan — tashrif emas;
    15 s ichida qaytgan — o'sha tashrif. Vaqti — kirgan payt. Bo'limdagi vaqt (dwell) hisoblanmaydi (14.10 qaror);
  * **yo'nalishlar** — shu odamning oldingi bo'limi (3 daqiqa ichida): "Sut → Non" (bitta kamera ichida);
  * **heatmap** (0,5 m katak): "bandlik" (odam·s/m²), "o'tganlar" (soatiga bir odam bir marta), yurish yo'nalishi
    strelkalari (2 m katak);
  * **bir nechta kamera**: har katak va har bo'limni uni kadrda eng yirik (1 m ko'proq piksel) ko'radigan kamera
    hisoblaydi — bitta odam ikki marta sanalmaydi;
  * to'silishda ID almashsa (1,5 s, 1 m ichida) — o'sha odam (tashrif ikki marta sanalmaydi).
* **Oqim sahifasi**: reja ustida heatmap (bandlik/o'tganlar, bugun/kecha/7/30 kun), bo'lim nomlari va tashriflar,
  strelkalar, jonli nuqtalar, bo'limlar jadvali (oldingi davr bilan), yo'nalishlar, soat/kun grafigi, CSV.
  Telegram'da **/oqim**.
* **Aniqlik darvozasi** (reja: *belgilangan nuqtalarda xato ≤ 0,5 m; bo'lim tashriflari qo'lda tekshiruvga mos*):
  * **Nuqtada turish** — polga belgi, joyi rejada; odam belgida turadi, tizim 3 s o'lchaydi (detektor + kalibrlash
    xatosi birga). Har kamerada ≥ 5 o'lchov, ≥ 3 joyda; 90% o'lchovda xato ≤ 0,5 m.
  * **Qo'lda sanash** — 1–3 bo'limni kuzatib, kirgan har xaridorda "+1" (telefonda katta tugmalar). Oxirgi 2 ta
    ≥ 30 daqiqalik sinovda aniqlik ≥ 90%, juftlash (±5 s) ≥ 85%, jami ≥ 30 bosish.
  * Buyruq qatoridan: `python tests\flow_gate.py` (bazani faqat o'qiydi). Mezonlar: `app/flow_eval.py`.
* **Bulut**: hello'da rejalar va bo'limlar (rasmsiz, metrda), soatlik yig'indi (`POST /edge/flow`; faqat bulut
  `features: ["flow"]` deganidan keyin), `GET /api/v1/flow`, panelda yangi **Oqim** sahifasi (reja, heatmap,
  bo'limlar, yo'nalishlar), bulut botida `/bugun` — eng ko'p tashrif qilingan bo'limlar. docs/SYNC_API.md.
* **O'lchovlarim (rostini aytsam)** — Linux, 2 yadro; sintetik pinhole kamera (balandlik 2,7–3,6 m, 75–105°),
  bosish xatosi ~2,5 px, rejada 5 sm:
  * egriliksiz linza, 8 nuqta: 8 sahnadan 8 tasida 90% joyda xato ≤ 0,13 m; o'rtacha egrilik, 12 nuqta: ≤ 0,36 m;
  * kuchli "bochka" (2,8 mm linza) — 12–16 nuqtada ham sahnalarning ~65–70% ida ≤ 0,5 m: bunday kamerada ko'proq
    nuqta va "nuqtada turish" sinovi albatta kerak;
  * haqiqiy video (`samples/sanash_namuna.mp4`, keng burchakli, taxminiy kalibrlash): 7 kishi, yurish tezligi
    mediana 1,06 m/s (sakrashsiz), to'xtamay o'tganlar — 0 tashrif, oqim har AI qadamiga ~0,2 ms qo'shadi.
  * Bu do'koningizdagi natija emas. Darvoza faqat Oqim sahifasidagi sinovlar bilan o'tadi.

## 2.0.0 — 4-bosqich: kassa navbati va kassir qoidalari

* **Ikki yangi zona** (*Kameralar → Zonalar*):
  * **Kassa navbati** — kassa oldida xaridorlar kutadigan joy, to'lov joyi bilan birga (har kassaga alohida, nomi
    "1-kassa").
  * **Kassir joyi** — peshtaxta orti: u yerdagi odam navbatga qo'shilmaydi, bo'sh qolsa — "kassir yo'q". Bitta
    kamerada bir nechta kassa bo'lsa, har navbat eng yaqin kassir joyi bilan juftlanadi. O'g'irlik signallarini
    o'chirmaydi (kassir yo'qligida peshtaxta ortiga o'tgan begona odam ham kuzatilsin); kassirga signal kelmasin
    desangiz — o'sha joyga "Xodimlar joyi" ham chizing.
* **O'lchash** (`app/ai/queues.py`; `worker.py` ga yangi ulanish kerak emas — 1.9.0 sanash ulanishi orqali ishlaydi):
  * Navbat uzunligi — zonada oyog'i turgan, kamida 1 s turgan odamlar; o'tib ketayotgan sanalmaydi. Oldidagi odam
    to'sib, 1,5 s ko'rinmay qolgan ham sanaladi. Ko'rsatiladigan son — oxirgi 2,5 s medianasi (raqam sakramaydi).
  * Kutish vaqti — har odam navbatga kirgandan to'lab chiqqungacha (5 s dan qisqasi — o'tib ketgan, yozilmaydi).
  * Navbatda odamlar bir-birini to'sadi, kuzatuvchi ID ni yo'qotadi. Yo'qolgan odam 8 s eslab turiladi; zona
    ichida birdan paydo bo'lgan yangi ID — eng yaqin yo'qolgan odamning davomi (kutish vaqti meros). Tashqaridan
    kirib kelgan odam har doim yangi xaridor. Zonada 20 daqiqadan uzoq "turgan odam" (manekin, plakat, doim turgan
    qo'riqchi) hisobga olinmaydi.
  * Kassir 8 s dan ko'p ko'rinmasa — "kassir yo'q". Har kassa holati: uzun navbat / band / bo'sh / yopiq /
    navbat bor, kassir yo'q.
  * Video tahlili va aniqlik sinovi bazaga yozmaydi, signal bermaydi.
* **5 qoida** (sozlamalar Navbat sahifasida):
  1. **Qo'shimcha kassa oching** — navbatda ≥ 4 kishi 30 s davomida yoki kimdir ≥ 3 daqiqa kutmoqda. Xabarda
     boshqa kassalar holati ham bor. Bir kassa uchun 10 daqiqada bir marta ("Navbat hali ham uzun").
  2. **Kassani tekshiring** — navbatda ≥ 2 kishi, kassir joyi 45 s bo'sh.
  3. **Qo'shimcha kassir kerak emas** — 30 daqiqa: ≥ 2 kassa ochiq, navbat deyarli yo'q (band vaqt yig'indisi ≤
     (ochiq − 1) × 50%, navbat ≤ 2). 2 soatda bir marta; sukut bo'yicha faqat panelda.
  4. **Kassir jadvali** — har dushanba 09:00 dan keyin: oxirgi 4 haftada bir xil hafta kuni va soatda takrorlangan
     navbat (soat ichida ≥ 5 daqiqa, kunlarning ≥ yarmida; kamida 2 haftalik tarix). "Har kuni 13:00–14:00",
     "Juma 18:00–20:00" ko'rinishida.
  5. **Tarmoq signali** (bulut) — 15 daqiqa ichida ≥ 2 filialda 1/2-qoida: egasiga bitta xabar (umumiy sabab:
     to'lov terminali, internet, kassa dasturi).
* **Telegram (do'kon boti):** 1–2-qoida signallari (4-qoida ham). **/navbat** — kassalar hozir va bugungi
  ko'rsatkich; shu xabardagi tugma bilan har kim navbat signallarini o'zi uchun o'chiradi (o'g'irlik signallari
  kelaveradi). Navbat sahifasida "Telegram sinovi".
* **Navbat sahifasi** (edge panel): kassalar hozir (3 s da yangilanadi), bugun (o'tdi, o'rtacha va 90% kutish, eng
  uzoq, eng uzun navbat, uzun navbat daqiqalari), soatlar bo'yicha grafiklar, signallar, qoidalar sozlamasi, kassir
  jadvali tavsiyasi, kunlar, CSV. Boshqaruv panelida — navbat kartasi.
* **Aniqlik darvozasi** (reja: *navbat soni ±1 aniqlikda ≥ 90% namunada, kutish vaqti xatosi ≤ 20%*):
  * **Namuna olish** — tizim o'sha lahzadagi kadrni (navbat zonasi atrofi) va o'z sonini saqlaydi; siz kadrdagi
    navbatni sanab kiritasiz (tizim soni javobdan keyin ko'rinadi). Kamida 50 ta, 15 tasida navbatda ≥ 2 kishi.
  * **Sekundomer** — odam navbatga turganda va to'lab chiqqanda bosiladi; avtomatik yozuv bilan vaqt kesishuvi
    bo'yicha juftlanadi. Juftlanmagan o'lchov 100% xato. Kamida 10 kishi, o'rtacha xato ≤ 20%.
  * Buyruq qatoridan: `python tests\queue_gate.py` (bazani faqat o'qiydi). Qo'shimcha: Little qonuni tekshiruvi
    (L = λ·W) — izlar uzilayotganini ko'rsatadi.
* **Bulut:** Navbat sahifasi (filiallar kassalari hozir, bugun, soatlar, signallar, kunlar), sozlamalarda "Kassa
  navbati" va "Qo'shimcha kassir kerak emas", bulut botida `/navbat`. Edge navbat yozuvlarini faqat bulut
  `features: ["queues"]` deganidan keyin yuboradi — eski bulut bilan hodisalar navbati to'xtamaydi
  (docs/SYNC_API.md).
* **O'lchovlarim (rostini aytsam):**
  * Sintetik sahna (30 kishi, 4 kadr/s; kompyuterim 2 yadroli Xeon, Linux):
    * xatosiz: kutish xatosi < 3%;
    * o'rtacha shovqin (10% kadr yo'q, to'silishlar, ID almashishi): 1,3–6,1%, soni ±1 ichida 99–100%;
    * og'ir shovqin (20% kadr yo'q): 8,7–15,8%, ±1 ichida 98,6–98,9%.
  * Ochiq haqiqiy videolar, to'liq quvur (YOLO11n):
    * 6 kishilik "xizmat joyi" — hammasi topildi, kutish xatosi o'rtacha 4,2%;
    * 1–3 kishilik guruhlar — soni ±1 ichida 97,9%, aniq 90,7%.
  * Bu do'koningizdagi natija emas: kamera burchagi va navbat zichligi boshqa. Darvoza faqat Navbat sahifasidagi
    sinov bilan o'tadi.

## 1.9.1 — tuzatishlar (do'kon kompyuterida topilgan)

* **`start_windows.bat` ishga tushmasdi:** `AttributeError: module 'app.web' has no attribute 'SERVER'`.
  * Sabab: 1.4.1 ning asl `run.py` si yo'qolgan edi, qayta tiklangan nusxa 1.5.0+ dagi asl `web.py` bilan mos
    kelmagan (`SERVER_REF`, `HUSHYOR_CTL_TOKEN`).
  * Avvalgi sinovlar `run.py` ni chetlab o'tgan, shuning uchun bu ko'rinmagan.
  * Endi `python -m tests.test_run` dasturni xuddi `start_windows.bat` kabi haqiqiy jarayon sifatida tekshiradi:
    * ishga tushish;
    * band portda ikkinchi nusxa;
    * toza to'xtatish;
    * nazoratchi (`--supervise`) va uning o'ldirilishi.
* **Kirish sahifasi:**
  * Standart parol bilan tarmoqdan kirish rad etilsa, endi aniq sabab yoziladi (avval "parol noto'g'ri" chiqardi).
  * Bloklanganda necha soniya qolgani ko'rinadi.
* **Qizil ogohlantirish** ("standart parol bilan tarmoqqa ochiq") panelda yana chiqadi. Avval qayta tiklangan
  `base.html` boshqa nomdagi o'zgaruvchini kutardi.
* **RTSP login/paroli** kameralar ro'yxatida, boshqaruv panelida va API'da yashirin: `rtsp://***@...`.
* `tests/test_fixes.py` asl kod bilan moslashtirildi: 50/50. Qayta tiklangan barcha fayllar ham tekshirildi:
  modullar orasidagi har bir murojaat bor nomga ishora qiladi.

## 1.9.0 versiyadagi o'zgarishlar (3-bosqich: odamlarni sanash)

* **Sanash chizig'i** — yangi zona turi (*Kameralar → Zonalar → "Sanash chizig'i"*):
  * Ikki nuqta bilan chiziladi. Strelka ichkari tomonni ko'rsatadi, "⇅ Yo'nalishni almashtirish" tugmasi bor.
  * Odam oyog'i chiziqni bir tomondan ikkinchisiga to'liq kesib o'tsa — bitta "kirdi" yoki "chiqdi".
  * Chiziq atrofida o'lik oraliq bor (odam bo'yining 10%): eshik oldida u yoqdan-bu yoqqa yurish sanalmaydi.
  * Kuzatuvchi ID ni almashtirsa yoki izni yo'qotib qayta topsa (1,2 s ichida, yaqin joyda) — odam ikki marta ham,
    umuman sanalmay ham qolmaydi. Ulash 0,8 s "sinovda" turadi: shu orada eski ID qaytsa (detektor bir-ikki kadr
    ko'rmagan ekan, yonida esa boshqa odam paydo bo'lgan) — ulash bekor, soxta o'tish yo'q. Xodim belgisi faqat
    ishonchli ulashda (keyingi kadr, juda yaqin) meros bo'ladi: xodimning ortidan kirgan xaridor "xodim" bo'lib
    qolmaydi.
  * Chiziq tahrirlansa yoki yo'nalishi almashtirilsa — kadrdagi odamlar soxta "o'tish" bermaydi.
  * Chiziqli kamerada odam bo'lsa, AI to'liq tezlikda ishlaydi — izlar uzilmaydi.
  * Sanashdagi har qanday xato kamera tahlilini (o'g'irlikni aniqlashni) to'xtatmaydi.
  * Aylanuvchi video fayl manbasida o'tishlar faqat birinchi aylanishda sanaladi (signallar kabi).
* **Xodimlar va ish vaqti xaridorlar soniga kirmaydi:**
  * Xodimlar joyida turgan odam xodim hisoblanadi. U keyin aniqlansa ham, oldingi o'tishlari orqaga qarab
    tuzatiladi: lokal baza ham, bulutga ketgan daqiqalik yig'indi ham.
  * Ish vaqti (masalan `09:00-22:00`, tunni kesib o'tsa ham) — undan tashqaridagi o'tishlar alohida sanaladi.
  * "Hozir ichkarida" — xodimsiz, lekin ish vaqtidan qat'i nazar: 22:00 dan keyin chiqqan xaridor ham ayiriladi.
    Har kuni do'kon yopiq paytining o'rtasida nolga qaytadi (09:00-22:00 → 03:30; ish vaqti bo'sh bo'lsa yarim tunda),
    "0 qilish" tugmasi ham bor. Kun oxiri farqi ham shu hisobda va "biznes kuni" bo'yicha (03:30 dan 03:30 gacha):
    22:00 dan keyin, hatto yarim tundan keyin chiqqanlar ham o'sha kunga tushadi.
* **Tashriflar sahifasi** (edge panel, menyuda):
  * hozir ichkarida — qo'lda "0 qilish" tugmasi bilan;
  * bugun kirdi/chiqdi va kecha bilan solishtirish;
  * soatlar bo'yicha grafik, eng gavjum soat, oxirgi o'tishlar;
  * kunlik jadval (7/14/30/90 kun): kun oxiri farqi, xodimlar, ish vaqtidan tashqari;
  * oxirgi 7 kun ↔ undan oldingi 7 kun;
  * CSV eksport.

  Boshqaruv panelida "Bugun kirdi / ichkarida" qatori chiqadi.
* **Bulut:**
  * Daqiqalik ko'rsatkichlarda `in/out/staff_in/staff_out/offhours/off_in/off_out`.
  * Edge kechikkan o'tishni (AI navbati) va daqiqa o'rtasida to'xtab qolgan daqiqani o'zi to'g'rilab, qayta yuboradi.
    Bulutdagi yig'indi edge bilan aynan bir xil bo'ladi.
  * Sanash alohida `visits_minute` jadvalida, SQL'da yig'iladi: 280 ming qator (90 kun × 2 filial × 2 kamera)
    ustida `/visits?days=90` — 0,1–0,2 s (SQLite va PostgreSQL).
  * `GET /api/v1/visits` — filial va kun bo'yicha.
  * Panelda yangi **Tashriflar** sahifasi:
    * filial filtri;
    * kunlar va bugungi soatlar grafigi — soat do'kon vaqtida;
    * haftalik taqqoslash;
    * filiallar va kunlar jadvali.
  * Bosh sahifadagi filial kartasida "ichkarida · bugun".
  * Grafiklarda kirganlar.
  * Telegram `/bugun` buyrug'ida tashriflar.
  * "Hozir ichkarida" edge'dan hello bilan keladi, shuning uchun bulut va do'kon paneli bir xil ko'rsatadi.
  * Hujjat: [`docs/SYNC_API.md`](docs/SYNC_API.md).
* **Aniqlik darvozasi** (reja: "2 × 1 soat qo'lda sanash bilan ≥ 95%; kun oxirida og'ish ≤ 5%"):
  * Panelda **Tashriflar → Qo'lda sanash**: telefonda katta "Kirdi +1 / Chiqdi +1" tugmalari, bekor qilish va
    taymer. Tugagach tizim har yo'nalishni alohida solishtiradi. Har bosishni ±4 s ichidagi avtomatik o'tish bilan
    juftlaydi va o'tkazib yuborilgan hamda ortiqchalarni ko'rsatadi.
  * Darvoza: oxirgi 2 ta ≥ 1 soatlik sinovning har birida son aniqligi ≥ 95% **va** juftlash ≥ 95%. Ikkinchisi
    shuning uchun kerak: 10 ta o'tkazib yuborilgan va 10 ta ortiqcha o'tish sonni "100%" qilib ko'rsatishi mumkin.
  * Buyruq qatoridan: `python tests\count_gate.py` — bazani faqat o'qiydi. Chiqish kodi: 0 — o'tdi,
    1 — o'tmadi (maslahat bilan), 2 — ma'lumot yetarli emas. Mezonlar bitta joyda: `app/count_eval.py`.
* **O'rnatishda bir qo'shimcha qadam:** `python scripts\apply_counting_hook.py`.
  * Sanash kamera jarayoniga (`app/ai/worker.py`) 6 ta kichik qo'shimcha bilan ulanadi (7-chisi ixtiyoriy).
  * Yangilanish ZIP'i bu faylni **almashtirmaydi** — sizdagi asl 1.4.1 tuzatishlari saqlanadi.
  * Har qo'shimcha joyi aniq topilmasa, fayl o'zgartirilmaydi. Natija tekshiriladi: import, `Processor`
    yaratiladi va sanash chaqiriladi. Xato bo'lsa — eski nusxa qaytadi. Zaxira `data\backups\` da.
    BOM bilan saqlangan fayl ham ishlaydi.
  * Ulanmagan bo'lsa, Tashriflar sahifasida qizil ogohlantirish chiqadi.
  * 1.9.0 dan keyingi `update.ps1` bu qadamni o'zi bajaradi.
* **72 soatlik sinov (`tests/soak.py`) endi sanashni ham tekshiradi:** sukut videosi — `sanash_namuna.mp4`,
  kamerada sanash chizig'i. Har o'tish daqiqalik yig'indiga va bulutga **aynan** yetganini solishtiradi.
  45 daqiqalik sinov: 351 o'tish, 4 ta internet uzilishi (rad etish va "qora tuynuk") — edge va bulut bir xil,
  yo'qolgan ham, takrorlangan ham yo'q.
* **Sinov topgan va tuzatilgan muammo:** kamera ishlamay turgan paytda dastur qayta ishga tushsa, "Kamera tiklandi"
  xabari kelmasdi (holat xotirada edi). Endi holat bazada eslab qolinadi: qayta ishga tushgach "ishlamayapti"
  takrorlanmaydi, kamera tiklansa xabar keladi.
* **Telefonda panel:** keng jadvallar sahifani yonga surmaydi (jadval o'z joyida suriladi), sarlavha tugmalari yangi
  qatorga o'tadi.
* **Sinovlar:**
  * `tests.test_counting` — 79 ta: mantiq, ID almashishi, xodim, ish vaqti, saqlash, kechikkan o'tish, qayta ishga
    tushish, API, darvoza, ulash skripti.
  * `tests.test_counting_cloud` — 18 ta, SQLite va PostgreSQL'da (36 tekshiruv), tezlik ham.
  * `tests.test_count_video` — haqiqiy video: `samples/sanash_namuna.mp4`, Intel sample-videos, CC BY 4.0.
    Videoda 7 kishi chiziqni kesib o'tadi:
    * natija 7/7;
    * chiziq teskari bo'lsa 7/7 "kirdi";
    * AI 4 kadr/s da ham 7/7;
    * chiziq balandligi y = 0,72 / 0,80 / 0,88 — 7/7. Xona ichkarisida (y = 0,55–0,62) esa 4–5: u yerda odamlar
      chiziqni to'liq kesmasdan yon eshikdan chiqib ketadi. **Chiziq joyi muhim:** odam albatta to'liq kesib
      o'tadigan joyga qo'ying.
  * CPU: shu videoda sanash chizig'i AI qadamlarini +3% oshirdi, vaqt farqi o'lchash xatosi ichida
    (2 yadroli Xeon/Linux, sizning Ryzen'ingizda emas).
  * **Halol eslatma:** bu bitta ochiq video. Do'koningiz eshigidagi aniqlik faqat qo'lda sanash sinovi bilan
    isbotlanadi.

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
| Hisobotlar | Kunlik/haftalik hisobot (Telegram + PDF), tavsiyalar va 👍/👎, darvoza (2.4.0); signallar tahlili: kunlar/soatlar/kameralar, aniqlik, javob vaqti |
| Pilot | Tayyorlik ro'yxati, darvozalar jadvali (3–9-bosqich), pilot haftalari va egasining bahosi, pilot hisoboti (PDF), diagnostika paketi (2.5.0) |
| Tashriflar | Eshikdan kirdi/chiqdi, hozir ichkarida, soat/kun/hafta, eng gavjum soat, qo'lda sanash sinovi, CSV (1.9.0) |
| Navbat | Har kassada navbat va kutish vaqti, kassir bor/yo'q, qoidalar va signallar, kassir jadvali tavsiyasi, aniqlik sinovi (namuna + sekundomer), CSV (2.0.0) |
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
python -m tests.test_run                  # 1.9.1 run.py haqiqiy jarayon: ishga tushish, to'xtatish, nazoratchi
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
python -m tests.test_counting             # 1.9.0 odam sanash: mantiq, xodim, ish vaqti, API, darvoza, worker.py ga ulash
python -m tests.test_counting_cloud       # 1.9.0 bulutda tashriflar (CLOUD_TEST_PG_URL bilan PostgreSQL ham)
python -m tests.test_count_video          # 1.9.0 haqiqiy videoda sanash (samples/sanash_namuna.mp4, 7 kishi)
python tests/count_gate.py                # 1.9.0 sanash darvozasi (qo'lda sanash sinovlari va kunlik og'ish)
python -m tests.test_queues               # 2.0.0 kassa navbati: o'lchash, ID uzilishi, qoidalar 1-4, saqlash, Telegram, API, darvoza
python -m tests.test_queues_cloud         # 2.0.0 bulutda navbat: hello, signallar, 5-qoida, doira (CLOUD_TEST_PG_URL bilan PostgreSQL ham)
python -m tests.test_queue_video          # 2.0.0 haqiqiy videoda navbat (samples/navbat_namuna.mp4, navbat_guruh.mp4)
python tests/queue_gate.py                # 2.0.0 navbat darvozasi (namunalar va sekundomer)
python -m tests.test_flow                 # 2.1.0 oqim: gomografiya, bo'lim tashriflari, yo'nalishlar, heatmap, API, darvoza
python -m tests.test_flow_cloud           # 2.1.0 bulutda oqim: hello, soatlik yig'indi, /flow, edge → bulut (PostgreSQL ham)
python -m tests.test_flow_video           # 2.1.0 haqiqiy videoda oqim (samples/sanash_namuna.mp4, taxminiy kalibrlash)
python tests/flow_gate.py                 # 2.1.0 oqim darvozasi (nuqtada turish va qo'lda sanash)
python -m tests.test_reports              # 2.4.0 hisobotlar: davrlar, raqamlar, matn, PDF, Telegram, API, darvoza
python -m tests.test_reports_cloud        # 2.4.0 bulutda taqqoslash va haftalik xulosa (CLOUD_TEST_PG_URL bilan PostgreSQL ham)
python tests/report_gate.py               # 2.4.0 hisobot darvozasi (14 kun, o'z vaqtida, raqamlar, foydali tavsiyalar)
python -m tests.test_pilot                # 2.5.0 pilot: tayyorlik, darvozalar, pilot darvozasi, baho so'rovi, diagnostika, bulut
python tests/pilot_gate.py                # 2.5.0 pilot darvozasi va barcha bosqichlar jadvali
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
