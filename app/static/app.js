/* Hushyor AI — umumiy JS yordamchilari */
const H = (() => {
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  function toast(msg, kind = "", ms = 4500) {
    const box = document.getElementById("toasts");
    if (!box) return;
    const el = document.createElement("div");
    el.className = "toast " + kind;
    el.innerHTML = msg;
    box.appendChild(el);
    setTimeout(() => el.remove(), ms);
  }

  async function api(url, opts = {}) {
    const o = { headers: { Accept: "application/json" }, ...opts };
    if (o.json !== undefined) {
      o.method = o.method || "POST";
      o.headers["Content-Type"] = "application/json";
      o.body = JSON.stringify(o.json);
      delete o.json;
    }
    const r = await fetch(url, o);
    if (r.status === 401) { location.href = "/login?next=" + encodeURIComponent(location.pathname); return null; }
    let data = null;
    try { data = await r.json(); } catch (e) { data = null; }
    if (!r.ok) {
      const msg = (data && (data.detail || data.message)) || ("Xato: " + r.status);
      toast("⚠️ " + esc(typeof msg === "string" ? msg : JSON.stringify(msg)), "alert");
      throw new Error(msg);
    }
    return data;
  }
  const post = (url, json = {}) => api(url, { method: "POST", json });

  // 2.2.0: xavfsizlik signali — "shubhali harakat" va ball emas
  const evHead = (ev) => ev.safety ? `🆘 <b>${esc(ev.kind_label)}</b> — darhol tekshiring`
    : `🚨 <b>Shubhali harakat:</b> ${esc(ev.kind_label)} — ball ${Math.round(ev.score)}`;
  const levelCls = (l) => ({ "past": "past", "o'rta": "orta", "yuqori": "yuqori" }[l] || "past");
  const STATUS = { yangi: "Tekshirilmagan", tasdiqlangan: "Tasdiqlangan", rad_etilgan: "Xato signal" };
  const statusBadge = (s) => `<span class="badge ${esc(s)}">${esc(STATUS[s] || s)}</span>`;

  function evCard(ev) {
    const img = ev.thumb_url
      ? `<img src="${ev.thumb_url}" loading="lazy" alt="">`
      : `<div class="empty" style="color:#94a3b8;padding-top:22%">Klip tayyorlanmoqda…</div>`;
    const btns = ev.status === "yangi"
      ? `<button class="btn sm ok" onclick="H.review(${ev.id},'tasdiqlangan',this)">✓ Tasdiqlash</button>
         <button class="btn sm" onclick="H.review(${ev.id},'rad_etilgan',this)">✕ Xato</button>`
      : `${statusBadge(ev.status)}<span class="muted small" style="margin-left:auto">${esc(ev.reviewed_by || "")}</span>`;
    return `<div class="card ev" id="ev-${ev.id}">
      <a class="img" href="/events/${ev.id}">${img}<span class="score ${levelCls(ev.level)}">${ev.safety ? "🆘" : Math.round(ev.score)}</span>
        <span class="play"><svg width="44" height="44" viewBox="0 0 24 24" fill="#fff"><path d="M8 5v14l11-7z"/></svg></span></a>
      <div class="body"><h3>${esc(ev.kind_label)}</h3>
        <div class="muted small">#${ev.id} · ${esc(ev.time_str)}</div>
        ${ev.susp_label ? `<div class="small" style="color:#b42318">Shubha: ${esc(ev.susp_label)}${ev.susp_offset !== null && ev.susp_offset !== undefined ? ` · klipda ${ev.susp_offset} s` : ""}</div>` : ""}
        <div class="small" style="margin-top:2px">${esc(ev.source_name || "")}</div></div>
      <div class="foot">${btns}</div></div>`;
  }

  async function review(id, status, btn) {
    if (btn) btn.disabled = true;
    try {
      const r = await post(`/api/events/${id}/review`, { status });
      toast(status === "tasdiqlangan" ? "✅ Hodisa tasdiqlandi" : "Xato signal deb belgilandi", status === "tasdiqlangan" ? "ok" : "");
      const card = document.getElementById("ev-" + id);
      if (card && r && r.event) card.outerHTML = evCard(r.event);
      document.dispatchEvent(new CustomEvent("reviewed", { detail: r && r.event }));
    } finally { if (btn) btn.disabled = false; }
  }

  /* Oddiy SVG ustunli diagramma: series = [{name, color, values}] (stacked) */
  function bars(el, labels, series, opts = {}) {
    const W = el.clientWidth || 600, Hh = opts.height || 220, pl = 30, pb = 26, pt = 10, pr = 8;
    const n = labels.length;
    const totals = labels.map((_, i) => series.reduce((a, s) => a + (s.values[i] || 0), 0));
    const max = Math.max(1, ...totals);
    const step = Math.max(1, Math.ceil(max / 4));
    const top = step * 4;
    const cw = (W - pl - pr) / n, bw = Math.max(3, Math.min(34, cw * 0.62));
    let svg = `<svg class="chart" viewBox="0 0 ${W} ${Hh}" height="${Hh}">`;
    for (let g = 0; g <= 4; g++) {
      const y = pt + (Hh - pt - pb) * (1 - g / 4);
      svg += `<line x1="${pl}" x2="${W - pr}" y1="${y}" y2="${y}" stroke="#e2e8f0"/><text x="${pl - 6}" y="${y + 4}" text-anchor="end">${g * step}</text>`;
    }
    const every = Math.ceil(n / (opts.maxLabels || 14));
    labels.forEach((lab, i) => {
      const x = pl + cw * i + (cw - bw) / 2;
      let y0 = Hh - pb;
      series.forEach((s) => {
        const v = s.values[i] || 0;
        if (!v) return;
        const h = (Hh - pt - pb) * v / top;
        y0 -= h;
        svg += `<rect x="${x}" y="${y0}" width="${bw}" height="${h}" rx="2" fill="${s.color}"><title>${esc(lab)}: ${esc(s.name)} — ${v}</title></rect>`;
      });
      if (i % every === 0) svg += `<text x="${x + bw / 2}" y="${Hh - 8}" text-anchor="middle">${esc(lab)}</text>`;
    });
    svg += "</svg>";
    el.innerHTML = svg;
  }

  function hbars(el, entries, color = "#0e7c6b") {
    const max = Math.max(1, ...entries.map((e) => e[1]));
    el.innerHTML = entries.length ? entries.map(([k, v]) => `
      <div class="bd-row" style="grid-template-columns:1fr 160px 40px"><span>${esc(k)}</span>
        <span class="bd-bar"><i style="width:${(v / max) * 100}%;background:${color}"></i></span><b class="num">${v}</b></div>`).join("")
      : `<div class="muted small">Ma'lumot yo'q</div>`;
  }

  /* Global holat: yon panel va yangi hodisa xabarnomasi */
  let lastId = Number(sessionStorage.getItem("lastEventId") || 0);
  async function poll() {
    try {
      const s = await api("/api/summary");
      if (!s) return;
      const tg = s.telegram || {};
      const tgDot = document.getElementById("tg-dot"), tgText = document.getElementById("tg-text");
      if (tgDot) {
        const ok = tg.status === "ishlayapti";
        tgDot.className = "dot " + (ok ? (s.subs ? "on" : "warn") : (tg.enabled ? "off" : ""));
        tgText.textContent = ok ? (s.subs ? `${s.subs} obunachi` : "obunachi yo'q") : (tg.status || "—");
      }
      const run = (s.sources || []).filter((x) => x.running).length;
      const camDot = document.getElementById("cam-dot"), camText = document.getElementById("cam-text");
      if (camDot) { camDot.className = "dot " + (run ? "on" : ""); camText.textContent = `${run} ta yoqilgan`; }
      const np = document.getElementById("nav-pending");
      if (np) np.textContent = s.pending;
      if (lastId && s.last_id > lastId) {
        const ev = (s.latest || []).find((e) => e.id === s.last_id);
        if (ev) toast(`${evHead(ev)}<br><a href="/events/${ev.id}">Ko'rish →</a>`, "alert", 9000);
      }
      lastId = s.last_id;
      sessionStorage.setItem("lastEventId", String(lastId));
      document.dispatchEvent(new CustomEvent("summary", { detail: s }));
    } catch (e) { /* jim */ }
  }
  /* Ovozli signal (brauzer ruxsati: sahifada birorta bosilishdan keyin ishlaydi) */
  const store = {
    get(k) { try { return localStorage.getItem(k); } catch (e) { return null; } },
    set(k, v) { try { localStorage.setItem(k, v); } catch (e) { /* jim */ } },
  };
  let actx = null;
  function soundOn() { return store.get("hushyor_sound") !== "0"; }
  function beep() {
    if (!soundOn()) return;
    try {
      actx = actx || new (window.AudioContext || window.webkitAudioContext)();
      [0, 0.22].forEach((d) => {
        const o = actx.createOscillator(), g = actx.createGain(), t0 = actx.currentTime + d;
        o.type = "sine"; o.frequency.value = 880;
        g.gain.setValueAtTime(0.0001, t0);
        g.gain.exponentialRampToValueAtTime(0.25, t0 + 0.02);
        g.gain.exponentialRampToValueAtTime(0.0001, t0 + 0.18);
        o.connect(g).connect(actx.destination); o.start(t0); o.stop(t0 + 0.2);
      });
    } catch (e) { /* jim */ }
  }
  function renderSound() {
    const el = document.getElementById("sound-toggle");
    if (el) el.textContent = soundOn() ? "🔔 Ovoz yoqilgan" : "🔕 Ovoz o'chirilgan";
  }
  function toggleSound() { store.set("hushyor_sound", soundOn() ? "0" : "1"); renderSound(); if (soundOn()) beep(); }

  /* Jonli oqim (SSE): yangi hodisa sekundlar ichida — 4 s kutmasdan */
  let es = null;
  function startStream() {
    if (!window.EventSource) return;
    try {
      es = new EventSource("/api/sse");
      es.addEventListener("ev", (m) => {
        let d = null;
        try { d = JSON.parse(m.data); } catch (e) { return; }
        const ev = d.event || {};
        if (d.kind === "new") {
          if (ev.id > lastId) { lastId = ev.id; sessionStorage.setItem("lastEventId", String(lastId)); }
          toast(`${evHead(ev)}<br>${esc(ev.source_name || "")}<br><a href="/events/${ev.id}">Ko'rish →</a>`, "alert", 12000);
          beep();
        }
        document.dispatchEvent(new CustomEvent("ev-" + d.kind, { detail: ev }));
        poll();
      });
    } catch (e) { es = null; }
  }

  document.addEventListener("DOMContentLoaded", renderSound);
  renderSound();
  startStream();
  poll();
  setInterval(poll, 10000);

  const fmtDur = (sec) => { sec = Math.round(sec || 0); const h = Math.floor(sec / 3600), m = Math.floor(sec % 3600 / 60), s = sec % 60; return h ? `${h} soat ${m} daq` : (m ? `${m} daq ${s} s` : `${s} s`); };
  const pct = (v) => (v === null || v === undefined) ? "—" : Math.round(v * 100) + "%";

  return { esc, toast, api, post, evCard, review, bars, hbars, levelCls, statusBadge, fmtDur, pct, poll, beep, toggleSound };
})();
