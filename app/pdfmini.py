"""Kichik PDF yozuvchi (2.3.0, dalil paketi uchun) — tashqi kutubxonasiz.

Imkoniyatlari: A4 sahifalar, Helvetica / Helvetica-Bold matn (WinAnsi kodlash), JPEG rasm (DCTDecode — qayta
siqilmaydi), oddiy chiziq. O'zbekcha lotin harflari (o', g', sh, ch) ASCII; kirill matn lotinga o'giriladi; WinAnsi'da
yo'q belgilar (emoji va h.k.) tashlab yuboriladi.
"""
from __future__ import annotations

import zlib

PAGE_W, PAGE_H = 595.28, 841.89          # A4, pt
MARGIN = 48

_CYR = {"а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "yo", "ж": "j", "з": "z", "и": "i", "й": "y",
        "к": "k", "л": "l", "м": "m", "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u", "ф": "f",
        "х": "x", "ц": "ts", "ч": "ch", "ш": "sh", "щ": "sh", "ъ": "'", "ы": "i", "ь": "", "э": "e", "ю": "yu", "я": "ya",
        "ў": "o'", "қ": "q", "ғ": "g'", "ҳ": "h"}
_MAP = {"—": "\x97", "–": "\x96", "“": "\x93", "”": "\x94", "‘": "'", "’": "'", "ʻ": "'", "ʼ": "'", "…": "\x85",
        "•": "\x95", "№": "No", "≥": ">=", "≤": "<=", "→": "->", "×": "\xd7", "°": "\xb0", "·": "\xb7"}
# Helvetica kengliklari (1/1000 em) — asosiy ASCII; qolganlari o'rtacha
_W = {" ": 278, "!": 278, '"': 355, "'": 191, "(": 333, ")": 333, ",": 278, "-": 333, ".": 278, "/": 278, ":": 278,
      ";": 278, "I": 278, "i": 222, "j": 222, "l": 222, "f": 278, "t": 278, "r": 333, "m": 833, "w": 722, "M": 833,
      "W": 944, "%": 889, "@": 1015}


def to_winansi(s: str) -> str:
    out = []
    for ch in str(s if s is not None else ""):
        low = ch.lower()
        if low in _CYR:
            r = _CYR[low]
            out.append(r.capitalize() if ch != low and r else r)
        elif ch in _MAP:
            out.append(_MAP[ch])
        elif ch == "\t":
            out.append("    ")
        elif ch == "\n":
            out.append("\n")                         # qator — wrap() bo'ladi; PDF'ga hech qachon yozilmaydi
        elif ch == "\r":
            continue
        elif 32 <= ord(ch) < 256 and not 127 <= ord(ch) < 160 or ch in "\x85\x93\x94\x95\x96\x97":
            out.append(ch)
    return "".join(out)


def text_width(s: str, size: float, bold: bool = False) -> float:
    w = 0
    for ch in s:
        if ch in _W:
            w += _W[ch]
        elif "0" <= ch <= "9":
            w += 556
        elif ch.isupper():
            w += 667
        else:
            w += 556
    return w * size / 1000 * (1.05 if bold else 1.0)


def wrap(s: str, size: float, width: float, bold: bool = False) -> list[str]:
    s = to_winansi(s)
    lines = []
    for para in s.split("\n"):
        cur = ""
        for word in para.split(" "):
            cand = (cur + " " + word) if cur else word
            if text_width(cand, size, bold) <= width or not cur:
                cur = cand
            else:
                lines.append(cur)
                cur = word
            while text_width(cur, size, bold) > width and len(cur) > 1:   # juda uzun so'z (xesh)
                k = max(1, int(len(cur) * width / text_width(cur, size, bold)) - 1)
                lines.append(cur[:k])
                cur = cur[k:]
        lines.append(cur)
    return lines


def _esc(s: str) -> str:
    return s.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def jpeg_size(data: bytes) -> tuple[int, int] | None:
    """JPEG SOF sarlavhasidan (eni, bo'yi)."""
    i = 2
    n = len(data)
    if data[:2] != b"\xff\xd8":
        return None
    while i + 9 < n:
        if data[i] != 0xFF:
            i += 1
            continue
        m = data[i + 1]
        if m in (0xD8, 0x01) or 0xD0 <= m <= 0xD7:
            i += 2
            continue
        ln = int.from_bytes(data[i + 2:i + 4], "big")
        if m in (0xC0, 0xC1, 0xC2):
            h = int.from_bytes(data[i + 5:i + 7], "big")
            w = int.from_bytes(data[i + 7:i + 9], "big")
            return w, h
        i += 2 + ln
    return None


class Doc:
    def __init__(self, title: str = ""):
        self.title = title
        self.pages: list[list[str]] = []
        self.images: list[tuple[bytes, int, int]] = []
        self.page_imgs: list[set[int]] = []
        self.y = 0.0
        self.new_page()

    # ----------------------------------------------------------------- joylash
    def new_page(self) -> None:
        self.pages.append([])
        self.page_imgs.append(set())
        self.y = PAGE_H - MARGIN

    def _need(self, h: float) -> None:
        if self.y - h < MARGIN:
            self.new_page()

    def text(self, s: str, size: float = 10, bold: bool = False, gap: float = 1.35, indent: float = 0,
             color: tuple = (0, 0, 0)) -> None:
        width = PAGE_W - 2 * MARGIN - indent
        for line in wrap(s, size, width, bold):
            self._need(size * gap)
            self.y -= size * gap
            font = "F2" if bold else "F1"
            r, g, b = color
            self.pages[-1].append(f"BT {r:.3f} {g:.3f} {b:.3f} rg /{font} {size:.1f} Tf {MARGIN + indent:.2f} "
                                  f"{self.y:.2f} Td ({_esc(line)}) Tj ET")

    def kv(self, key: str, value: str, size: float = 10, key_w: float = 130) -> None:
        lines = wrap(value, size, PAGE_W - 2 * MARGIN - key_w)
        self._need(size * 1.4 * min(len(lines), 3))      # kalit qiymatning boshi bilan bir sahifada
        first = True
        for line in lines:
            self._need(size * 1.4)                       # uzun izoh sahifadan oshsa — keyingi sahifaga
            self.y -= size * 1.4
            if first:
                self.pages[-1].append(f"BT 0.35 0.38 0.42 rg /F1 {size:.1f} Tf {MARGIN:.2f} {self.y:.2f} Td "
                                      f"({_esc(to_winansi(key))}) Tj ET")
                first = False
            self.pages[-1].append(f"BT 0 0 0 rg /F1 {size:.1f} Tf {MARGIN + key_w:.2f} {self.y:.2f} Td ({_esc(line)}) Tj ET")

    def space(self, h: float = 8) -> None:
        self.y = max(self.y - h, MARGIN)

    def rule(self) -> None:
        self._need(10)
        self.y -= 6
        self.pages[-1].append(f"0.8 0.82 0.85 RG 0.6 w {MARGIN:.2f} {self.y:.2f} m {PAGE_W - MARGIN:.2f} {self.y:.2f} l S")
        self.y -= 4

    def image(self, jpeg: bytes, max_w: float | None = None, max_h: float = 300, caption: str = "") -> bool:
        sz = jpeg_size(jpeg)
        if not sz:
            return False
        iw, ih = sz
        max_w = max_w or PAGE_W - 2 * MARGIN
        s = min(max_w / iw, max_h / ih, 1.0 * 72 / 96 * 2)
        w, h = iw * s, ih * s
        self._need(h + (16 if caption else 4))
        self.images.append((jpeg, iw, ih))
        idx = len(self.images)
        self.page_imgs[-1].add(idx)
        self.y -= h
        self.pages[-1].append(f"q {w:.2f} 0 0 {h:.2f} {MARGIN:.2f} {self.y:.2f} cm /Im{idx} Do Q")
        if caption:
            self.text(caption, size=8, color=(0.4, 0.4, 0.45))
        self.y -= 4
        return True

    # ----------------------------------------------------------------- yozish
    def render(self) -> bytes:
        objs: list[bytes] = []

        def add(b: bytes) -> int:
            objs.append(b)
            return len(objs)

        f1 = add(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>")
        f2 = add(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold /Encoding /WinAnsiEncoding >>")
        img_ids = []
        for data, w, h in self.images:
            img_ids.append(add(f"<< /Type /XObject /Subtype /Image /Width {w} /Height {h} /ColorSpace /DeviceRGB "
                               f"/BitsPerComponent 8 /Filter /DCTDecode /Length {len(data)} >>\nstream\n".encode()
                               + data + b"\nendstream"))
        pages_id = len(objs) + 1 + 2 * len(self.pages)    # Pages obyekti sahifalardan keyin
        kids = []
        total = len(self.pages)
        for n, (cmds, imgs) in enumerate(zip(self.pages, self.page_imgs), 1):
            foot = (f"BT 0.5 0.5 0.55 rg /F1 8 Tf {MARGIN:.2f} 24 Td ({_esc(to_winansi(self.title))}  -  {n}/{total}) "
                    f"Tj ET")
            body = zlib.compress(("\n".join(cmds + [foot])).encode("latin-1", "replace"))
            cid = add(f"<< /Length {len(body)} /Filter /FlateDecode >>\nstream\n".encode() + body + b"\nendstream")
            xo = " ".join(f"/Im{i} {img_ids[i - 1]} 0 R" for i in sorted(imgs))
            res = f"<< /Font << /F1 {f1} 0 R /F2 {f2} 0 R >>" + (f" /XObject << {xo} >>" if xo else "") + " >>"
            kids.append(add(f"<< /Type /Page /Parent {pages_id} 0 R /MediaBox [0 0 {PAGE_W} {PAGE_H}] "
                            f"/Resources {res} /Contents {cid} 0 R >>".encode()))
        assert len(objs) + 1 == pages_id
        add(f"<< /Type /Pages /Kids [{' '.join(f'{k} 0 R' for k in kids)}] /Count {len(kids)} >>".encode())
        info = add(f"<< /Title ({_esc(to_winansi(self.title))}) /Producer (Hushyor AI) >>".encode("latin-1", "replace"))
        cat = add(f"<< /Type /Catalog /Pages {pages_id} 0 R >>".encode())
        out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
        offs = []
        for i, b in enumerate(objs, 1):
            offs.append(len(out))
            out += f"{i} 0 obj\n".encode() + b + b"\nendobj\n"
        x = len(out)
        out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode()
        for o in offs:
            out += f"{o:010d} 00000 n \n".encode()
        out += (f"trailer\n<< /Size {len(objs) + 1} /Root {cat} 0 R /Info {info} 0 R >>\nstartxref\n{x}\n%%EOF\n").encode()
        return bytes(out)
