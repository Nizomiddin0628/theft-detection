# O'g'irlik v2 (2.3.0, 7-bosqich): qayta o'qitish, champion/challenger, dalil paketi, darvoza

Kod: `app/ml/train.py` (o'qitish, `oof_feedback`), `app/ml/champion.py` (solishtiruv), `app/ml/model.py`
(champion va challenger ko'rsatkichlari), `app/ai/analyzer.py` (soya ehtimoli `ml_ch`), `app/evidence.py` va
`app/pdfmini.py` (dalil paketi), `app/theft_eval.py` + `tests/theft_gate.py` (darvoza). Sinov: `tests/test_theft_v2.py`.

## Qayta o'qitish

Ma'lumot: sintetik skelet oynalari + PoseLift (ixtiyoriy) + qo'riqchi javoblari (tasdiqlangan = 1, rad etilgan = 0;
og'irligi ×5). Xavfsizlik hodisalari (yiqilish, janjal...) olinmaydi. Javob — signal lahzasigacha bo'lgan oxirgi 2,5 s
skelet (hodisa bilan saqlangan `pose_seq`; hodisa o'chirilsa ham `data/feedback/` arxivida qoladi).

Tavsiya: 1–2 hafta ishlating, har signalga ✅/❌ bering (kamida 20 ta, ikkala turdan), keyin AI model sahifasida
"Challenger sifatida" belgisi bilan o'qiting (buyruq qatori: `python -m app.ml.train --challenger`). Ko'rmagan baholash
(oof) faqat challenger o'qitilganda hisoblanadi.

## Champion / challenger

| | Champion | Challenger |
|---|---|---|
| Ko'rsatkich | `data/behavior_active.json` (yo'q bo'lsa `models/behavior_active.json`) | `data/behavior_challenger.json` |
| Ballga ta'siri | `ml_weight × max(0, p − 0,5) × 2` | yo'q (faqat `events.ml_ch` ga yoziladi) |

Ko'rsatkichlar atomar yoziladi (vaqtinchalik fayl + `os.replace`); bo'sh challenger — `{"path": null}`. Yuklab
bo'lmagan model fayli (buzuk) eslab qolinadi — fayl o'zgarmaguncha har kadrda qayta urinilmaydi. 2.2.0 gacha panelda
tanlangan model git faylida (`models/behavior_active.json`) yozilardi: yangilash skripti va dastur uni `data/` ga
ko'chiradi, git fayli asl holiga qaytadi (`git status` toza).

Solishtiruv (`champion.compare`): har javob berilgan o'g'irlik hodisasi uchun `ball − champion hissasi + challenger
hissasi` o'sha signaldagi haqiqiy chegara (`events.thr`: zona/polka bilan) va ML og'irligi (`events.ml_w`) bilan,
qirqilmagan ball (`score_raw`) dan; champion ham o'sha chegara bilan (teng sharoit). 2.3.0 dan oldingi hodisalar —
hozirgi umumiy chegara bilan ("taxminiy" deb sanaladi); signal paytida AI model o'chiq bo'lgan hodisalar va "chiqish"
signallari (o'sha hodisani ikkinchi marta sanardi) kirmaydi. Natija: ❌ signal (champion → challenger),
✅ signal, yo'qolgan ✅ (qizil), AUC (champion — hodisa paytidagi haqiqiy ehtimol, challenger — oof yoki soya).

* **Ko'rmagan baholash (oof)**: o'qitishda javob berilgan hodisalar k (≤ 5) guruhga bo'linadi; har guruh uni ko'rmagan
  model bilan baholanadi (sintetik va boshqa guruhlar bilan o'qitilgan). Kamida 10 ta hodisa, ikkala turdan.
* **Soya**: challenger yaratilgandan keyingi, `ml_ch` yozilgan va javob berilgan hodisalar. Eng halol manba.
* **Tavsiya** (`verdict`): faqat soya bo'yicha — ≥ 20 javob, har turdan ≥ 5; yo'qolgan ✅ = 0; ❌ kamaygan yoki
  AUC ≥ +0,02. Oof natijasi faqat ma'lumot: challenger o'sha hodisadan keyingi javoblarda ham o'qitilgan, champion
  ehtimoli esa hodisa paytidagi eski modelniki — bu baho yangi modelga yon bosadi.
* **Cheklov**: faqat champion bergan signallar o'lchanadi. Champion o'tkazib yuborgan va challenger topadigan
  o'g'irliklar ko'rinmaydi — Aniqlik sinovidagi belgilangan videolar bilan tekshiring.

Faollashtirish: challenger → champion (versiyalar jadvalida `role=champion`, `promoted_at`); oldingi champion jadvalda
qoladi — "Faollashtirish" bilan qaytariladi. Bekor qilish: `role='rad etilgan'`. Yangi challenger o'qitilsa yoki
champion boshqacha almashsa (o'qitib faollashtirish, boshqa versiyani qo'lda faollashtirish) — eski challenger
`role='eskirgan'` va soya bo'shaydi (soya solishtiruvi eski champion bilan edi).

## Dalil paketi

`GET /api/events/{id}/evidence.zip` (hodisa sahifasidagi tugma): `hodisa_<id>.pdf`, `klip.mp4`, `rasm.jpg`, `odam.jpg`
(asl fayllar, o'zgarmagan), `manifest.json` (hodisa ma'lumoti, har faylning SHA-256 va hajmi), `TEKSHIRISH.txt`.
PDF — o'z yozuvchimiz (`app/pdfmini.py`: A4, Helvetica, JPEG — qayta siqilmaydi), kirill matn lotinga o'giriladi,
emoji tashlanadi. Paket hech kimni ayblamaydi — tizim nima ko'rgani va odam qanday qaror qilganini qayd etadi; video
shaxsiy ma'lumot, faqat qonun doirasida berilsin.

## Darvoza: xato ≤ 3 / kamera / kun

Har jonli kamera: ❌ o'g'irlik hodisalari / ishlagan kun (metrics_minute, up_pct ≥ 0,5). Oyna — hodisalar saqlanadigan
muddat (`retention_days`, ≤ 90 kun), kamera daqiqalari ham shu oynadan. Kerak: ≥ 7 kun, javobsizlar ≤ 10%.
O'chirilgan hodisalarning javoblari ham sanaladi (`data/feedback/ev*.json` arxivi, 2.3.0 dan skelet izi bo'lmasa ham
yoziladi) — ❌ signalni o'chirib darvozani "aldab" bo'lmaydi. 7 kun to'lmasdan ❌ soni 21 dan (3 × 7) oshsa — darhol
"o'tmadi". Kuniga xato yaxlitlanmasdan solishtiriladi (3,004 > 3).
Panel — Aniqlik sinovi; `python tests\theft_gate.py` (bazani faqat o'qiydi; `HUSHYOR_DATA_DIR` bo'lsa o'sha papkadagi;
kod 0 — o'tdi, 1 — o'tmadi, 2 — ma'lumot yetarli emas).
