"""5-bosqich sinovlari uchun sintetik kamera: pol tekisligini haqiqiy pinhole kamera kabi ko'radi (balandlik, egilish,
fokus) va OpenCV uslubidagi linza egriligi (k1, k2 — modelimizdagidan boshqacha formula: model aniq mos emas, haqiqiy
kameradagidek). Faqat sinovlar uchun (ishlab turgan tizimda ishlatilmaydi).
"""
from __future__ import annotations

import math

import numpy as np


class SimCamera:
    def __init__(self, pos=(5.0, -2.0), height=3.0, look=(5.0, 6.0), fov_deg=90.0, w=1280, h=720,
                 k1=0.0, k2=0.0, roll_deg=0.0):
        self.w, self.h = w, h
        self.k1, self.k2 = k1, k2
        self.C = np.array([pos[0], pos[1], height], dtype=np.float64)
        T = np.array([look[0], look[1], 0.0])
        fwd = T - self.C
        fwd /= np.linalg.norm(fwd)
        up = np.array([0.0, 0.0, 1.0])
        right = np.cross(fwd, up)
        right /= np.linalg.norm(right)
        down = np.cross(fwd, right)
        r = math.radians(roll_deg)
        right, down = right * math.cos(r) + down * math.sin(r), -right * math.sin(r) + down * math.cos(r)
        self.R = np.stack([right, down, fwd])          # dunyo → kamera
        self.f = (w / 2) / math.tan(math.radians(fov_deg) / 2)

    def project(self, xy, z=0.0) -> tuple[np.ndarray, np.ndarray]:
        """Rejadagi nuqtalar (m, pol yoki z balandlikda) → kadr (0..1), ko'rinadimi."""
        xy = np.asarray(xy, dtype=np.float64).reshape(-1, 2)
        P = np.c_[xy, np.full(len(xy), z)] - self.C
        Pc = P @ self.R.T
        zc = Pc[:, 2]
        front = zc > 0.1
        zc = np.where(front, zc, 0.1)
        x, y = Pc[:, 0] / zc, Pc[:, 1] / zc
        r2 = x * x + y * y
        d = 1 + self.k1 * r2 + self.k2 * r2 * r2
        u = (self.f * x * d + self.w / 2) / self.w
        v = (self.f * y * d + self.h / 2) / self.h
        # Haqiqiy linzada tasvir "buklanmaydi": egrilik formulasi monoton bo'lmagan joy (juda chet) — ko'rinmaydi
        mono = 1 + 3 * self.k1 * r2 + 5 * self.k2 * r2 * r2 > 0.15
        vis = front & mono & (u >= 0) & (u <= 1) & (v >= 0) & (v <= 1)
        return np.c_[u, v], vis

    @property
    def aspect(self) -> float:
        return self.w / self.h


def box_for(cam: SimCamera, xy, height=1.7, width=0.5) -> np.ndarray | None:
    """Rejadagi joyida turgan odam ramkasi (piksel): oyoq — pol nuqtasi, bosh — height metr balandlikda."""
    foot, ok1 = cam.project([xy], 0.0)
    head, _ = cam.project([xy], height)
    if not ok1[0]:
        return None                                  # oyoq kadrda emas — detektor odamni to'liq ko'rmaydi
    fx, fy = foot[0][0] * cam.w, foot[0][1] * cam.h
    hy = max(head[0][1] * cam.h, 0.0)                # bosh kadrdan chiqsa — ramka kadr chetida kesiladi
    bh = max(fy - hy, 4.0)
    bw = max(fy - head[0][1] * cam.h, 4.0) * width / height
    return np.array([fx - bw / 2, fy - bh, fx + bw / 2, fy], dtype=np.float32)
