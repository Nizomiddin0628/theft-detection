# Modellar litsenziyasi (third-party notices)

| Fayl | Model | Muallif | Litsenziya | Manba |
|---|---|---|---|---|
| `yolo11n-pose.onnx`, `yolo11n.onnx` | YOLO11 | Ultralytics | AGPL-3.0 (tijorat uchun Enterprise litsenziya) | https://github.com/ultralytics/ultralytics |
| `yolox_nano.onnx`, `yolox_tiny.onnx` | YOLOX 0.1.1rc0 (COCO) | Megvii | Apache-2.0 | https://github.com/Megvii-BaseDetection/YOLOX |
| `rtmpose_t.onnx`, `rtmpose_s.onnx` | RTMPose (body7, 256x192) | OpenMMLab | Apache-2.0 | https://github.com/open-mmlab/mmpose/tree/main/projects/rtmpose |

O'zgartirishlar: YOLOX fayllarida faqat `Reshape` shakllari `[1, 85, -1]` ga o'zgartirilgan (istalgan kirish
o'lchami uchun, og'irliklar o'zgarmagan) — `app/ai/model_zoo.py: make_yolox_dynamic`. RTMPose fayllari
OpenMMLab arxividagi `end2end.onnx` ning o'zi.

Eslatma: Apache-2.0 kod va og'irliklarga tegishli. RTMPose "body7" og'irliklari bir nechta ochiq datasetlarda
(COCO, AI Challenger, CrowdPose, MPII, Halpe, PoseTrack, sub-JHMDB) o'qitilgan; ularning ayrimlari o'z shartlariga
ega. Mahsulotni sotishdan oldin yurist bilan tekshirish tavsiya etiladi.
