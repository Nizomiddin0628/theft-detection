# Ko'rish modellari va litsenziya (2.7.0)

Hushyor AI kamerada odamni topadi, uning 17 ta tana nuqtasini (skelet) aniqlaydi va kerak bo'lganda qo'l atrofida
buyum qidiradi. Buning uchun tayyor neyron tarmoq modellari ishlatiladi. Mahsulotni **sotish** uchun shu modellarning
litsenziyasi muhim.

## To'plamlar

| To'plam | Odam | Skelet | Buyum | Litsenziya | Holat |
|---|---|---|---|---|---|
| `yolo11` — **standart** | YOLO11n-pose | (o'sha model) | YOLO11n | **AGPL-3.0** | sinalgan, yiqilishni yaxshiroq topadi |
| `open-m` — **sotish uchun tavsiya** | YOLOX-tiny (≤ 512 px) | MoveNet Thunder | YOLOX-tiny | Apache-2.0 | 2.7.0 da qo'shildi |
| `open-ml` — tez / zaxira | YOLOX-tiny (≤ 512 px) | MoveNet Lightning | YOLOX-tiny | Apache-2.0 | kuchsiz kompyuter, ko'p kamera |
| `open`, `open-s` | YOLOX | RTMPose-t / -s | YOLOX-tiny | Apache-2.0 | qo'lda (fayl OpenMMLab saytidan) |

* **Apache-2.0** — tijoratda erkin, faqat litsenziya matni saqlanadi (`models/LICENSES.md`).
* **AGPL-3.0** (Ultralytics YOLO11) — yopiq mahsulotda ishlatish uchun pullik Enterprise litsenziya kerak.

## Nega ochiq to'plam standart emas (rostini aytsam)

Reja: ochiq to'plam YOLO11 dan sezilarli yomon bo'lmasa — standart qilish. O'lchov natijasi:

| Sinov (Linux, 2 yadro, ONNX Runtime) | YOLO11 | open-m | open-ml |
|---|---|---|---|
| Odam topish (`samples/` 4 video, YOLO11 topgan odamlardan) | — | 99,3% (ortiqcha 1,3%) | 99,3% |
| Skelet mosligi YOLO11 bilan, PCK@0,1 / @0,2 | — | 0,84 / 0,91 | 0,79 / 0,88 |
| Bir kadr, o'rtacha (odam + skelet) | ~46 ms | ~67–70 ms (~1,5×) | ~51 ms |
| Sanash videosi (7 chiqdi, 0 kirdi) — `test_count_video` | 5/5 | 5/5 | 5/5 |
| Navbat videolari — `test_queue_video` | 7/7 | 7/7 | 7/7 |
| Oqim videosi — `test_flow_video` | 9/9 | 9/9 | 9/9 |
| Web + AI + soxta Telegram — `test_web` | 50/50 | 50/50 | 51/51 |
| O'g'irlik: `dokon_namuna_20s` (1 belgilangan hodisa, sinov chegarasi 35) | topdi, xato 0 | **topmadi, xato 1** | topdi, xato 1 |
| Do'kon namunalarida xato yiqilish signali (4 video) | 0 | 0 | **1** |
| **Ochiq yiqilish videolari** (ambianic, 5 ta) | **3/5** | **0/5** | **0/5** |

Sanash, navbat va oqimda farq yo'q (`test_web` dagi sinovlar soni signallar soniga bog'liq). **O'g'irlik** — bitta
20 soniyalik namunada farq bor: xulq tahlili va xulq modeli YOLO11 skeletlarida sozlangan/o'qitilgan, MoveNet nuqtalari
boshqacha joylashadi. Bitta video xulosa uchun kam, lekin ochiq to'plamga o'tgandan keyin ✅/❌ javoblar bilan xulq
modelini qayta o'qitish (challenger) kerak bo'ladi. **Yiqilish** esa sezilarli yomon (`open-ml` do'kon namunasida
1 ta xato yiqilish ham berdi): bu
videolar past sifatli, qariyalar (bukchaygan, yurish moslamasi bilan) — YOLOX-tiny ularning ayrimlarini umuman
ko'rmaydi (1-video: kadrlarning 0% ida ≥ 0,25 ishonch, YOLO11 — 65%; 3-video: 38% vs 100%), yerda yotgan odamni ham
kamroq topadi. Shu sabab va'da qilinganidek — **ochiq to'plam standart qilinmadi**, panelda tavsiya sifatida turadi.

Sinab ko'rilgan, lekin yordam bermagan (hammasi Apache-2.0):

| Variant | 1-video (≥ 0,25) | 3-video | Vaqt | Natija |
|---|---|---|---|---|
| YOLOX-tiny 640 px (512 o'rniga) | — | — | +20 ms | yiqilish 0/5 va navbat videosida 1 xato yiqilish |
| YOLOX-s | 5% | 57% | ~1,5× tiny | yetarli emas |
| MoveNet MultiPose (o'zi odam topadi) | 5% | 71% | ~36 ms | 4–5-videolarda YOLOX dan past (92–97% vs 100%) |
| D-FINE-N (2024, DETR) | 25% | 90% | ~80 ms | 3-video yaxshi, lekin 2× sekin; integratsiya qilinmadi |
| YOLOX-nano | — | — | tezroq | sanashda ortiqcha "chiqdi + kirdi" juftlari (3/5) — rad etildi |

## Mavjud va yangi o'rnatish

* Standart — `yolo11` (yangi o'rnatishda ham). Tanlangan to'plam qayta ishga tushganda o'zgarmaydi.
* AI model sahifasida litsenziya ogohlantirishi, tayyorlik ro'yxatida «Modellar litsenziyasi» bandi (ixtiyoriy;
  demoda ko'rsatilmaydi).
* O'tish — *AI model → Ko'rish modellari → YOLOX + MoveNet → Tanlash* (kameralar to'xtamaydi; qaytish ham shunday).
  O'tishdan oldin: *Aniqlik sinovi* → "Hammasini solishtirish" (o'z belgilangan videolaringizda) va do'konda
  sahnalashtirilgan yiqilish (gilam ustida, 6-bosqich darvozasi). O'tgandan keyin: 1–2 hafta ✅/❌ va xulq modelini
  challenger sifatida qayta o'qitish (u yangi skeletlarda o'qiydi).
* Zaxira zanjiri: tanlangan ochiq to'plam fayli yo'q yoki buzuq bo'lsa (buzuq fayl `*.broken` qilib chetga olinadi) —
  `open-ml`, u ham bo'lmasa `yolo11`. Kameralar to'xtamaydi; sahifada "Tanlangan: …, hozir ishlayapti: …", yo'q fayl
  fonda qayta yuklab olinadi.

## Fayllar

* Repoda: `yolo11n-pose.onnx`, `yolo11n.onnx`, `yolox_tiny.onnx`, `movenet_lightning.onnx` (9,5 MB).
* `movenet_thunder.onnx` (25 MB) — repoda yo'q (`.gitignore`). O'rnatuvchi va yangilash skripti GitHub'dan yuklab
  oladi (o'zgarmas commit, SHA-256 tekshiriladi); bo'lmasa — AI model sahifasida "Yuklab olish" yoki
  `python -m app.ai.model_zoo open-m`. Internet yo'q kompyuterda: faylni boshqa joydan olib *Qo'lda yuklash*.
* MoveNet — "bitta odam" modeli: har odam alohida kesib olinadi (ramka × 1,25, kvadrat). Vaqt odamlar soniga qarab
  oshadi: Thunder ~18 ms, Lightning ~6 ms har odamga (2 yadro). Kadrda 5+ odam bo'ladigan kamerada `open-ml` yaxshiroq.
* MoveNet nuqta ishonchi YOLO11 shkalasiga o'tkaziladi (bir xil odamlarda kvantillar bo'yicha, 23 392 nuqta) —
  tahlilchi chegaralari (0,3 / 0,45 / 0,6) o'zgarmaydi.

## Sinovlar

```bash
python -m tests.test_models                                  # to'plamlar, zaxira, SHA-256, tayyorlik bandi, API
HUSHYOR_MODEL_SET=open-m python -m tests.test_safety_video   # istalgan sinovni boshqa to'plam bilan
```

`HUSHYOR_MODEL_SET` — faqat sinov/baholash uchun (sozlamadan ustun); odatda bo'sh qoldiriladi.

## Keyingi qadam (taklif)

Yiqilishdagi farqni yopish uchun eng istiqbolli variant — kuchliroq ochiq odam detektori (D-FINE-N yoki shunga
o'xshash) yoki do'kon kamerasi videolarida YOLOX ni qo'shimcha o'qitish. Bu alohida bosqich: avval egasining do'konida
sahnalashtirilgan yiqilishlar bilan YOLO11 va open-m ni solishtirish kerak (ochiq videolar do'kon sharoiti emas).
