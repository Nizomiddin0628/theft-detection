/* Hushyor AI — landing sayt (2.9.0): uch til (matnlar sahifada; skript xabarlari — <body data-i18n>), aloqa
   ma'lumotlari (sozlama.js), bosh sahna, "Qanday ishlaydi" (tomir tizimi) sxemasi, mini-sahnalar, tablar, forma.
   Animatsiyalarning o'zi — style.css da (CSS); bu yerda: ekranda ko'rinmaganda to'xtatish va bosilgandagi qism. */
(function () {
  "use strict";
  var cfg = window.HUSHYOR_SITE || {};
  var lang = (document.documentElement.getAttribute("lang") || "uz").slice(0, 2);
  var M = {};
  try { M = JSON.parse(document.body.getAttribute("data-i18n") || "{}"); } catch (e) { M = {}; }
  function tr(k, fallback) { return M[k] || fallback || ""; }
  var opened = Date.now();
  var motion = !(window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches);
  var hasIO = "IntersectionObserver" in window;
  document.body.classList.remove("no-js");

  function el(tag, attrs, text) {
    var e = document.createElement(tag);
    Object.keys(attrs || {}).forEach(function (k) { e.setAttribute(k, attrs[k]); });
    if (text) e.textContent = text;
    return e;
  }

  // ---- 3.0.0: mavzu tugmasi — tizim bo'yicha → yorug' → tungi → tizim bo'yicha (theme.js boshida qo'llaydi)
  var THEME_KEY = "hushyor-theme";
  var THEMES = ["system", "light", "dark"];
  var root = document.documentElement;
  var themeBtn = document.getElementById("theme-btn");
  var themeMeta = document.querySelector('meta[name="theme-color"]');
  function curTheme() {
    var t = root.getAttribute("data-theme");
    return t === "light" || t === "dark" ? t : "system";
  }
  function paintTheme() {
    var t = curTheme();
    var next = THEMES[(THEMES.indexOf(t) + 1) % THEMES.length];
    if (themeBtn) {
      var label = tr("theme_fmt", "{cur} → {next}").replace("{cur}", tr("theme_" + t, t)).replace("{next}", tr("theme_" + next, next));
      themeBtn.setAttribute("data-mode", t);
      themeBtn.setAttribute("aria-label", label);
      themeBtn.setAttribute("title", label);
    }
    if (themeMeta) {
      var dark = t === "dark" || (t === "system" && window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches);
      themeMeta.setAttribute("content", dark ? "#0b1520" : "#0f1b2a");
    }
  }
  if (themeBtn) {
    themeBtn.addEventListener("click", function () {
      var next = THEMES[(THEMES.indexOf(curTheme()) + 1) % THEMES.length];
      if (next === "system") root.removeAttribute("data-theme");
      else root.setAttribute("data-theme", next);
      try {
        if (next === "system") window.localStorage.removeItem(THEME_KEY);
        else window.localStorage.setItem(THEME_KEY, next);
      } catch (e) { /* saqlab bo'lmadi — faqat shu sahifada amal qiladi */ }
      paintTheme();
    });
    paintTheme();
  }

  // ---- aloqa ma'lumotlari (.env: CLOUD_SITE_PHONE, CLOUD_SITE_TELEGRAM, CLOUD_SITE_EMAIL, CLOUD_SITE_ADDRESS)
  var list = document.getElementById("contacts");
  function contact(label, text, href) {
    var li = el("li");
    li.appendChild(el("span", { "class": "k" }, label));
    if (href) li.appendChild(el("a", { href: href, rel: "noopener" }, text));
    else li.appendChild(el("span", {}, text));
    list.appendChild(li);
  }
  if (list) {
    if (cfg.phone) contact(tr("k_phone", "Telefon"), cfg.phone, "tel:" + String(cfg.phone).replace(/[^\d+]/g, ""));
    if (cfg.telegram) contact(tr("k_tg", "Telegram"), "@" + String(cfg.telegram).replace(/^https:\/\/t\.me\//, ""), cfg.telegram);
    if (cfg.email) contact(tr("k_email", "Email"), cfg.email, "mailto:" + cfg.email);
    if (cfg.address) contact(tr("k_addr", "Manzil"), cfg.address, "");
  }
  // Narx: o'zbekcha — CLOUD_SITE_PRICE, ruscha/inglizcha — CLOUD_SITE_PRICE_RU / _EN (berilmasa — sahifadagi matn)
  var price = document.getElementById("price");
  var priceText = lang === "uz" ? cfg.price : cfg["price_" + lang];
  if (price && priceText) price.textContent = priceText;
  var ver = document.getElementById("foot-ver");
  if (ver && cfg.version) ver.textContent = tr("version", "versiya") + " " + cfg.version;
  var demo = document.getElementById("demo-link");
  if (demo && cfg.demo) demo.classList.remove("hidden");

  // ---- ekranda ko'rinish: .live — animatsiyalar faqat shunda o'ynaydi (batareya va protsessor tejaladi).
  // [kuzatiladigan, .live oladigan]: tomir sxemasida faqat rasm kuzatiladi (telefonda izohlar ro'yxati ko'rinib,
  // sxema ekrandan chiqqanda impulslar va izohlar almashinuvi to'xtaydi)
  function watchLive(pairs) {
    if (!hasIO) { pairs.forEach(function (p) { p[1].classList.add("live"); }); return; }
    var io = new IntersectionObserver(function (ents) {
      ents.forEach(function (e) {
        pairs.forEach(function (p) { if (p[0] === e.target) p[1].classList.toggle("live", e.isIntersecting); });
      });
    }, { threshold: 0.15 });
    pairs.forEach(function (p) { io.observe(p[0]); });
  }
  var livePairs = Array.prototype.slice.call(document.querySelectorAll(".fv")).map(function (n) { return [n, n]; });
  var vasEl = document.getElementById("vas");
  var vasFig = vasEl && vasEl.querySelector(".vas-fig");
  if (vasFig) livePairs.push([vasFig, vasEl]);
  watchLive(livePairs);

  // ---- bosh sahna. Animatsiya CSS da (skriptsiz ham o'ynaydi). Skript bo'lsa: sahna ekranga chiqqanda boshidan
  // boshlanadi (telefonda u birinchi ekrandan pastda) va "Yana ko'rish" bilan qaytariladi (Web Animations API)
  var stage = document.getElementById("stage");
  var replay = document.getElementById("replay");
  if (stage && replay && motion && typeof stage.getAnimations === "function") {
    var restart = function (run) {
      stage.getAnimations({ subtree: true }).forEach(function (a) {
        try {
          a.currentTime = 0;
          if (run) a.play(); else a.pause();
        } catch (e) { /* eski brauzer — o'tkazib yuboriladi */ }
      });
    };
    replay.hidden = false;
    replay.addEventListener("click", function () { restart(true); });
    if (hasIO) {
      // Birinchi javob: sahna allaqachon ko'rinib turgan bo'lsa (kompyuter) — o'ynayotgan animatsiyaga tegilmaydi
      // (sekin yuklanganda ham orqaga sakramaydi); ko'rinmasa (telefon) — to'xtatiladi va ko'ringanda boshidan
      var first = true;
      var io = new IntersectionObserver(function (ents) {
        var vis = ents.some(function (e) { return e.isIntersecting; });
        if (first) {
          first = false;
          if (vis) io.disconnect(); else restart(false);
          return;
        }
        if (vis) { io.disconnect(); restart(true); }
      }, { threshold: 0.35 });
      io.observe(stage.querySelector(".monitor") || stage);
    }
  }

  // ---- "Qanday ishlaydi": tugun yoki izoh bosilsa — sxemada o'sha yo'l yonadi (aria-pressed). Foydalanuvchi
  // bosmaguncha (va harakat kamaytirilmagan bo'lsa) izohlar navbat bilan o'zi almashadi — bu faqat ko'rinish
  // (.tour klassi, aria-pressed emas). Sichqoncha yoki fokus ro'yxatda bo'lsa — almashinuv to'xtab turadi
  // (chiqqanda davom etadi); bosilsa — butunlay to'xtaydi. Sxema ekranda ko'rinmasa — kutadi
  var vas = vasEl;
  if (vas) {
    var items = Array.prototype.slice.call(vas.querySelectorAll(".vas-it"));
    var parts = Array.prototype.slice.call(vas.querySelectorAll(".vx[data-n]"));
    var vlist = vas.querySelector(".vas-list");
    var cur = -1, timer = null, touched = false, hover = false, focused = false;
    var select = function (i, user) {
      cur = i;
      var key = items[i].getAttribute("data-k");
      items.forEach(function (b, j) {
        b.classList.toggle("tour", !user && j === i);
        if (user) b.setAttribute("aria-pressed", j === i ? "true" : "false");
      });
      parts.forEach(function (p) {
        var on = (" " + p.getAttribute("data-n") + " ").indexOf(" " + key + " ") >= 0;
        p.classList.toggle("on", on);
      });
      vas.classList.add("focus");
    };
    var stop = function () {
      touched = true;
      if (timer) { clearInterval(timer); timer = null; }
    };
    var tick = function () {
      if (!touched && !hover && !focused && vas.classList.contains("live")) select((cur + 1) % items.length, false);
    };
    items.forEach(function (b, i) {
      b.addEventListener("click", function () { stop(); select(i, true); });
    });
    if (vlist) {
      vlist.addEventListener("mouseenter", function () { hover = true; });
      vlist.addEventListener("mouseleave", function () { hover = false; });
      vlist.addEventListener("focusin", function () { focused = true; });
      vlist.addEventListener("focusout", function () { focused = false; });
    }
    Array.prototype.slice.call(vas.querySelectorAll("[data-k].node, [data-k].heart")).forEach(function (n) {
      n.addEventListener("click", function () {
        var k = n.getAttribute("data-k");
        for (var i = 0; i < items.length; i++) {
          if (items[i].getAttribute("data-k") === k) { stop(); select(i, true); break; }
        }
      });
    });
    select(0, false);
    if (motion) timer = setInterval(tick, 3800);
  }

  // ---- ekranlar: tablar (klaviatura: ← → Home End)
  var tabs = Array.prototype.slice.call(document.querySelectorAll('.tabs [role="tab"]'));
  function show(i, focus, animate) {
    tabs.forEach(function (t, j) {
      var on = i === j;
      t.setAttribute("aria-selected", on ? "true" : "false");
      t.tabIndex = on ? 0 : -1;
      var p = document.getElementById(t.getAttribute("aria-controls"));
      if (!p) return;
      p.hidden = !on;
      p.classList.remove("in");
      if (on && animate) {
        void p.offsetWidth;            // animatsiya har bosishda qaytadan boshlansin
        p.classList.add("in");
      }
    });
    if (focus) tabs[i].focus();
  }
  tabs.forEach(function (t, i) {
    t.addEventListener("click", function () { show(i, false, true); });
    t.addEventListener("keydown", function (e) {
      var n = tabs.length, k = null;
      if (e.key === "ArrowRight") k = (i + 1) % n;
      if (e.key === "ArrowLeft") k = (i - 1 + n) % n;
      if (e.key === "Home") k = 0;
      if (e.key === "End") k = n - 1;
      if (k !== null) { e.preventDefault(); show(k, true, true); }
    });
  });
  if (tabs.length) show(0, false, false);

  // ---- so'rov formasi → POST /api/v1/leads
  var form = document.getElementById("lead-form");
  if (!form) return;
  var msg = document.getElementById("form-msg");
  var btn = form.querySelector('button[type="submit"]');
  function say(text, ok) {
    msg.textContent = text;
    msg.className = "form-msg " + (ok ? "ok" : "err");
  }
  function source() {
    var q = new URLSearchParams(window.location.search);
    var parts = ["sayt"];
    if (lang !== "uz") parts.push("til=" + lang);
    ["utm_source", "utm_campaign"].forEach(function (k) { if (q.get(k)) parts.push(k.slice(4) + "=" + q.get(k)); });
    return parts.join(" · ").slice(0, 200);
  }
  // Server xabarlari o'zbekcha — boshqa tilda holat bo'yicha o'z tilidagi xabar
  function serverError(status, detail) {
    if (lang === "uz") return detail || tr("fail");
    if (status === 429) return tr("busy", detail);
    if (/telefon/i.test(detail || "")) return tr("phone_bad", detail);
    if (/ism/i.test(detail || "")) return tr("name", detail);
    if (/eskirgan/i.test(detail || "")) return tr("expired", detail);
    return tr("fail", detail);
  }
  form.addEventListener("input", function () { form.classList.remove("sent"); });
  form.addEventListener("submit", function (e) {
    e.preventDefault();
    form.classList.remove("sent");
    var f = form.elements;
    var name = f.name.value.trim();
    var phone = f.phone.value.trim();
    f.name.parentNode.classList.toggle("bad", name.length < 2);
    var digits = phone.replace(/\D/g, "");
    f.phone.parentNode.classList.toggle("bad", digits.length < 9);
    if (name.length < 2) { say(tr("name", "Ismingizni yozing"), false); f.name.focus(); return; }
    if (digits.length < 9) { say(tr("phone", "Telefon raqamini to'liq yozing"), false); f.phone.focus(); return; }
    var body = {
      name: name, phone: phone, store: f.store.value.trim(), city: f.city.value.trim(),
      branches: f.branches.value, message: f.message.value.trim(), hp_extra: f.hp_extra.value,
      elapsed: (Date.now() - opened) / 1000, source: source()
    };
    btn.disabled = true;
    say(tr("sending", "…"), true);
    fetch(cfg.api || "/api/v1/leads", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body), credentials: "omit"
    })
      .then(function (r) {
        return r.json().catch(function () { return {}; }).then(function (d) { return { ok: r.ok, status: r.status, d: d }; });
      })
      .then(function (x) {
        if (x.ok) {
          form.reset();
          say(tr("thanks", "OK"), true);
          form.classList.add("sent");
        } else {
          say(serverError(x.status, x.d && x.d.detail), false);
        }
      })
      .catch(function () {
        say(tr("net") + (cfg.phone ? " " + tr("call") + " " + cfg.phone : "."), false);
      })
      .then(function () { btn.disabled = false; });
  });
})();
