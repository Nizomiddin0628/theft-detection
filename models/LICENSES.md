# Modellar litsenziyasi (third-party notices)

| Fayl | Model | Muallif | Litsenziya | Manba |
|---|---|---|---|---|
| `yolo11n-pose.onnx`, `yolo11n.onnx` | YOLO11 | Ultralytics | AGPL-3.0 (tijorat uchun Enterprise litsenziya) | https://github.com/ultralytics/ultralytics |
| `yolox_nano.onnx`, `yolox_tiny.onnx` | YOLOX 0.1.1rc0 (COCO) | Megvii | Apache-2.0 | https://github.com/Megvii-BaseDetection/YOLOX |
| `rtmpose_t.onnx`, `rtmpose_s.onnx` | RTMPose (body7, 256x192) | OpenMMLab | Apache-2.0 | https://github.com/open-mmlab/mmpose/tree/main/projects/rtmpose |
| `movenet_lightning.onnx`, `movenet_thunder.onnx` | MoveNet SinglePose v4 (Lightning 192, Thunder 256) | Google | Apache-2.0 | https://www.kaggle.com/models/google/movenet (ONNX: https://github.com/Kazuhito00/MoveNet-Python-Example, commit `515743a`, Apache-2.0) |

O'zgartirishlar: YOLOX fayllarida faqat `Reshape` shakllari `[1, 85, -1]` ga o'zgartirilgan (istalgan kirish
o'lchami uchun, og'irliklar o'zgarmagan) — `app/ai/model_zoo.py: make_yolox_dynamic`. RTMPose fayllari
OpenMMLab arxividagi `end2end.onnx` ning o'zi. MoveNet fayllari o'zgartirilmagan (SHA-256 `model_zoo.FILES` da
qayd etilgan va yuklab olishda tekshiriladi); `movenet_lightning.onnx` dastur bilan birga keladi, `movenet_thunder.onnx`
(25 MB) o'rnatuvchi / yangilash skripti / AI model sahifasi orqali GitHub'dan yuklab olinadi.

Litsenziyasi toza to'plamlar: `open-m` (YOLOX-tiny + MoveNet Thunder) va `open-ml` (YOLOX-tiny + MoveNet Lightning) —
faqat Apache-2.0 modellar. Standart hali YOLO11 (AGPL-3.0): ochiq to'plam yiqilishni yomonroq topadi (docs/MODELS.md).
Mahsulotni sotishda YOLO11 ishlatilsa — Ultralytics Enterprise litsenziyasi kerak.

Eslatma: Apache-2.0 kod va og'irliklarga tegishli. RTMPose "body7" og'irliklari bir nechta ochiq datasetlarda
(COCO, AI Challenger, CrowdPose, MPII, Halpe, PoseTrack, sub-JHMDB) o'qitilgan; ularning ayrimlari o'z shartlariga
ega. MoveNet Google tomonidan COCO va Google'ning ichki (fitnes/raqs/yoga) videolarida o'qitilgan; model kartasi
(Kaggle/TF Hub) Apache-2.0 ni ko'rsatadi. Mahsulotni sotishdan oldin yurist bilan tekshirish tavsiya etiladi.
