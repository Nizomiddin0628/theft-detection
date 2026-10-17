/* Hushyor AI — do'kon rejasi chizish yordamchilari (2.1.0: Oqim, Reja va kameralar, Kalibrlash sahifalari) */
const P = (() => {
  // Hodisa nuqtasi — canvas'ning o'z (tabiiy) koordinatalarida
  function evPt(cv, e) {
    const r = cv.getBoundingClientRect();
    return [Math.min(cv.width, Math.max(0, (e.clientX - r.left) / r.width * cv.width)),
            Math.min(cv.height, Math.max(0, (e.clientY - r.top) / r.height * cv.height))];
  }
  // Ekrandagi 1 piksel — canvas'da necha piksel (chiziq qalinligi va bosish masofasi uchun)
  function k(cv) { const r = cv.getBoundingClientRect(); return r.width ? cv.width / r.width : 1; }
  function rgba(hex, a) { const n = parseInt(String(hex || "#64748b").slice(1), 16); return `rgba(${n >> 16},${(n >> 8) & 255},${n & 255},${a})`; }
  function centroid(pts) { let x = 0, y = 0; for (const p of pts) { x += p[0]; y += p[1]; } return [x / pts.length, y / pts.length]; }
  function inside(pt, poly) {
    let c = false;
    for (let i = 0, j = poly.length - 1; i < poly.length; j = i++) {
      const [xi, yi] = poly[i], [xj, yj] = poly[j];
      if ((yi > pt[1]) !== (yj > pt[1]) && pt[0] < ((xj - xi) * (pt[1] - yi)) / (yj - yi) + xi) c = !c;
    }
    return c;
  }
  function path(ctx, pts, close = true) {
    ctx.beginPath(); pts.forEach((p, i) => i ? ctx.lineTo(p[0], p[1]) : ctx.moveTo(p[0], p[1])); if (close) ctx.closePath();
  }
  function label(ctx, text, x, y, col, fs) {
    ctx.font = `600 ${fs}px system-ui, sans-serif`;
    const tw = ctx.measureText(text).width;
    ctx.fillStyle = col; ctx.fillRect(x - tw / 2 - 6, y - fs / 2 - 5, tw + 12, fs + 9);
    ctx.fillStyle = "#fff"; ctx.textAlign = "center"; ctx.textBaseline = "middle"; ctx.fillText(text, x, y + 0.5);
    ctx.textAlign = "start"; ctx.textBaseline = "alphabetic";
  }
  // Bo'limlar: ko'pburchak + nom (va ixtiyoriy son)
  function sections(ctx, cv, secs, opt = {}) {
    const s = k(cv), lw = Math.max(1.5, 2 * s), fs = Math.max(11, 13 * s);
    for (const z of secs) {
      if (!z.points || z.points.length < 3) continue;
      const isSel = opt.sel && opt.sel.id === z.id;
      path(ctx, z.points);
      ctx.fillStyle = rgba(z.color, z.enabled === 0 ? 0.05 : (opt.fill ?? (isSel ? 0.28 : 0.14))); ctx.fill();
      ctx.lineWidth = isSel ? lw * 1.8 : lw; ctx.strokeStyle = z.color; ctx.setLineDash(z.enabled === 0 ? [8 * s, 6 * s] : []); ctx.stroke(); ctx.setLineDash([]);
      const [cx, cy] = centroid(z.points);
      const txt = opt.text ? opt.text(z) : z.name;
      if (txt) label(ctx, txt, cx, cy, z.color, fs);
      if (isSel) for (const p of z.points) { ctx.beginPath(); ctx.arc(p[0], p[1], lw * 3.2, 0, 7); ctx.fillStyle = "#fff"; ctx.fill(); ctx.lineWidth = lw; ctx.strokeStyle = z.color; ctx.stroke(); }
    }
  }
  // Issiqlik ranglari: ko'k → yashil → sariq → qizil, kichik qiymat shaffof
  const STOPS = [[0, [37, 99, 235]], [0.35, [22, 163, 74]], [0.65, [234, 179, 8]], [1, [220, 38, 38]]];
  function ramp(t) {
    t = Math.max(0, Math.min(1, t));
    let i = 0; while (i < STOPS.length - 2 && t > STOPS[i + 1][0]) i++;
    const [t0, c0] = STOPS[i], [t1, c1] = STOPS[i + 1], f = (t - t0) / (t1 - t0 || 1);
    return [0, 1, 2].map((j) => Math.round(c0[j] + (c1[j] - c0[j]) * f));
  }
  function heatCanvas(h, gamma = 0.6) {
    const raw = atob(h.data), n = raw.length;
    const oc = document.createElement("canvas"); oc.width = h.nx; oc.height = h.ny;
    const octx = oc.getContext("2d"), im = octx.createImageData(h.nx, h.ny);
    for (let i = 0; i < n; i++) {
      const v = raw.charCodeAt(i) / 255;
      if (v <= 0.01) { im.data[i * 4 + 3] = 0; continue; }
      const t = Math.pow(v, gamma), c = ramp(t);
      im.data[i * 4] = c[0]; im.data[i * 4 + 1] = c[1]; im.data[i * 4 + 2] = c[2];
      im.data[i * 4 + 3] = Math.round(70 + 150 * t);
    }
    octx.putImageData(im, 0, 0);
    return oc;
  }
  function heat(ctx, h, mpp, oc) {
    const cellPx = h.cell / mpp;
    ctx.save(); ctx.imageSmoothingEnabled = true; ctx.imageSmoothingQuality = "high";
    ctx.drawImage(oc || heatCanvas(h), 0, 0, h.nx * cellPx, h.ny * cellPx);
    ctx.restore();
  }
  function arrows(ctx, cv, list, mpp) {
    const s = k(cv), maxN = Math.max(1, ...list.map((a) => a.n));
    for (const a of list) {
      if (a.c < 0.25) continue;                      // yo'nalishlar aralash — strelka chizilmaydi
      const x = a.x / mpp, y = a.y / mpp, L = (0.6 + 0.9 * Math.sqrt(a.n / maxN)) / mpp * Math.min(1, a.c + 0.2);
      const x2 = x + a.dx * L, y2 = y + a.dy * L, ang = Math.atan2(a.dy, a.dx), hs = 7 * s;
      ctx.lineWidth = 2.2 * s; ctx.strokeStyle = "rgba(15,23,42,.75)"; ctx.fillStyle = "rgba(15,23,42,.75)";
      ctx.beginPath(); ctx.moveTo(x - a.dx * L * 0.3, y - a.dy * L * 0.3); ctx.lineTo(x2, y2); ctx.stroke();
      ctx.beginPath(); ctx.moveTo(x2, y2); ctx.lineTo(x2 - hs * Math.cos(ang - 0.45), y2 - hs * Math.sin(ang - 0.45));
      ctx.lineTo(x2 - hs * Math.cos(ang + 0.45), y2 - hs * Math.sin(ang + 0.45)); ctx.closePath(); ctx.fill();
    }
  }
  function dot(ctx, cv, x, y, col, r = 6, ring = "#fff") {
    const s = k(cv);
    ctx.beginPath(); ctx.arc(x, y, r * s, 0, 7); ctx.fillStyle = col; ctx.fill(); ctx.lineWidth = 2 * s; ctx.strokeStyle = ring; ctx.stroke();
  }
  function cross(ctx, cv, x, y, col, r = 7) {
    const s = k(cv); ctx.lineWidth = 2.2 * s; ctx.strokeStyle = col;
    ctx.beginPath(); ctx.moveTo(x - r * s, y - r * s); ctx.lineTo(x + r * s, y + r * s); ctx.moveTo(x + r * s, y - r * s); ctx.lineTo(x - r * s, y + r * s); ctx.stroke();
  }
  function num(ctx, cv, x, y, text, col) {
    const s = k(cv), fs = 12 * s;
    ctx.font = `700 ${fs}px system-ui, sans-serif`; ctx.lineWidth = 3 * s; ctx.strokeStyle = "rgba(0,0,0,.65)";
    ctx.strokeText(text, x + 8 * s, y - 8 * s); ctx.fillStyle = col || "#fff"; ctx.fillText(text, x + 8 * s, y - 8 * s);
  }
  // Lupa: kursor atrofini kattalashtirib chap yuqori burchakda ko'rsatadi (aniq bosish uchun)
  function loupe(ctx, cv, src, pt, zoom = 4) {
    if (!pt || !src) return;
    const s = k(cv), R = 70 * s, sz = R * 2 / zoom;
    const bx = pt[0] < cv.width / 2 ? cv.width - R * 2 - 10 * s : 10 * s, by = 10 * s;
    ctx.save(); ctx.beginPath(); ctx.rect(bx, by, R * 2, R * 2); ctx.clip();
    ctx.imageSmoothingEnabled = false;
    ctx.drawImage(src, pt[0] - sz / 2, pt[1] - sz / 2, sz, sz, bx, by, R * 2, R * 2);
    ctx.restore();
    ctx.lineWidth = 2 * s; ctx.strokeStyle = "#fff"; ctx.strokeRect(bx, by, R * 2, R * 2);
    ctx.strokeStyle = "#ef4444"; ctx.lineWidth = 1.5 * s;
    ctx.beginPath(); ctx.moveTo(bx + R, by + R - 10 * s); ctx.lineTo(bx + R, by + R + 10 * s); ctx.moveTo(bx + R - 10 * s, by + R); ctx.lineTo(bx + R + 10 * s, by + R); ctx.stroke();
  }
  function loadImg(url) {
    return new Promise((ok, fail) => { const im = new Image(); im.onload = () => ok(im); im.onerror = fail; im.src = url; });
  }
  const m = (v, d = 2) => (v === null || v === undefined) ? "—" : `${Number(v).toFixed(d)} m`;
  return { evPt, k, rgba, centroid, inside, path, label, sections, ramp, heatCanvas, heat, arrows, dot, cross, num, loupe, loadImg, m };
})();
