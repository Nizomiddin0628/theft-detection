"""Kadrga skelet, ramka va xavf belgilarini chizish."""
from __future__ import annotations

import time

import cv2
import numpy as np

EDGES = [(5, 7), (7, 9), (6, 8), (8, 10), (5, 6), (5, 11), (6, 12), (11, 12), (11, 13), (13, 15),
         (12, 14), (14, 16), (0, 1), (0, 2), (1, 3), (2, 4)]

GREEN = (110, 200, 90)
AMBER = (40, 180, 250)
RED = (60, 60, 235)
GRAY = (170, 170, 170)
WHITE = (255, 255, 255)
CYAN = (230, 200, 60)


def risk_color(risk: float):
    if risk >= 55:
        return RED
    if risk >= 25:
        return AMBER
    return GREEN


def _label(img, text, org, color, scale=0.5, thick=1):
    (tw, th), base = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, scale, thick)
    x, y = int(org[0]), int(org[1])
    y = max(y, th + 4)
    cv2.rectangle(img, (x, y - th - 6), (x + tw + 8, y + base - 2), color, -1)
    cv2.putText(img, text, (x + 4, y - 3), cv2.FONT_HERSHEY_SIMPLEX, scale, WHITE, thick, cv2.LINE_AA)


def draw(frame: np.ndarray, persons: list, objects: list, status: dict | None = None,
         highlight: int | None = None, scale: float = 1.0, banner: str | None = None,
         time_text: str | None = None) -> np.ndarray:
    """persons: [(track_id, box, kpts)], objects: [(box, name, kind)]."""
    img = frame
    status = status or {}
    lw = max(1, int(round(img.shape[1] / 640)))
    for box, name, kind in objects:
        b = (np.asarray(box) * scale).astype(int)
        c = CYAN if kind == "item" else (200, 120, 200)
        cv2.rectangle(img, (b[0], b[1]), (b[2], b[3]), c, lw)
        cv2.putText(img, name.encode("ascii", "ignore").decode() or kind, (b[0], max(12, b[1] - 4)),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.42, c, 1, cv2.LINE_AA)
    for tid, box, kpts in persons:
        b = (np.asarray(box) * scale).astype(int)
        k = np.asarray(kpts, dtype=np.float32).copy()
        k[:, :2] *= scale
        risk = float(status.get(tid, {}).get("risk", 0.0))
        if highlight is not None:
            color = RED if tid == highlight else GRAY
        else:
            color = risk_color(risk)
        thick = lw + (2 if tid == highlight else 0)
        cv2.rectangle(img, (b[0], b[1]), (b[2], b[3]), color, thick)
        for a, c2 in EDGES:
            if k[a, 2] >= 0.3 and k[c2, 2] >= 0.3:
                cv2.line(img, (int(k[a, 0]), int(k[a, 1])), (int(k[c2, 0]), int(k[c2, 1])), color, lw,
                         cv2.LINE_AA)
        for i in range(17):
            if k[i, 2] >= 0.3:
                r = 4 if i in (9, 10) else 2
                cv2.circle(img, (int(k[i, 0]), int(k[i, 1])), r + lw - 1,
                           (0, 220, 255) if i in (9, 10) else color, -1, cv2.LINE_AA)
        tag = f"#{tid}"
        if highlight is not None and tid == highlight:
            tag += " TEKSHIRING"
        elif risk >= 10:
            tag += f" xavf {risk:.0f}"
        _label(img, tag, (b[0], b[1] - 2), color)
    if banner:
        h, w = img.shape[:2]
        cv2.rectangle(img, (0, 0), (w, 30), (30, 30, 200), -1)
        cv2.putText(img, banner.encode("ascii", "ignore").decode(), (10, 21), cv2.FONT_HERSHEY_SIMPLEX,
                    0.6, WHITE, 1, cv2.LINE_AA)
    if time_text:
        h, w = img.shape[:2]
        (tw, _), _ = cv2.getTextSize(time_text, cv2.FONT_HERSHEY_SIMPLEX, 0.5, 1)
        org = (w - tw - 10, h - 10)
        cv2.putText(img, time_text, org, cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 0), 3, cv2.LINE_AA)
        cv2.putText(img, time_text, org, cv2.FONT_HERSHEY_SIMPLEX, 0.5, WHITE, 1, cv2.LINE_AA)
    return img


def draw_timeline(img: np.ndarray, t: float, t0: float, t1: float, s0: float, s1: float) -> None:
    """Klip ustida vaqt chizig'i: qizil bo'lak — shubhali harakat, oq chiziq — hozirgi joy.
    Shubhali lahza davomida kadr qizil ramka bilan o'ralib, "SHUBHALI LAHZA" yoziladi."""
    h, w = img.shape[:2]
    span = max(t1 - t0, 1e-3)
    y0, y1 = 34, 46
    x = lambda tt: int(round(6 + (w - 12) * min(max((tt - t0) / span, 0.0), 1.0)))  # noqa: E731
    cv2.rectangle(img, (5, y0 - 1), (w - 5, y1 + 1), (20, 20, 20), -1)
    cv2.rectangle(img, (6, y0), (w - 6, y1), (150, 150, 150), -1)
    cv2.rectangle(img, (x(s0), y0), (max(x(s1), x(s0) + 4), y1), (40, 40, 230), -1)
    cv2.line(img, (x(t), y0 - 4), (x(t), y1 + 4), (0, 0, 0), 5)
    cv2.line(img, (x(t), y0 - 4), (x(t), y1 + 4), WHITE, 2)
    if s0 - 0.3 <= t <= s1 + 1.0:
        cv2.rectangle(img, (0, 0), (w - 1, h - 1), (40, 40, 230), 6)
        _label(img, f"SHUBHALI LAHZA  {max(t - s0, 0.0):.1f} s", (8, y1 + 26), (40, 40, 230), scale=0.7, thick=2)
    elif t < s0:
        _label(img, f"shubhali lahzagacha {s0 - t:.0f} s", (8, y1 + 22), (90, 90, 90), scale=0.5)


def wall_clock(ts: float | None = None) -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts or time.time()))
