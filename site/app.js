/* Hushyor AI — landing sayt (3.3.0). Uch til (matnlar sahifada; skript xabarlari — <body data-i18n>), aloqa
   ma'lumotlari (sozlama.js), mavzu (yorug' ⇄ tungi), til va menyu oynalari, bosh sahna, "Qanday ishlaydi" sahnasi,
   kuzatuv devori, tablar, forma. Animatsiyalarning o'zi — style.css da; bu yerda: ekranda ko'ringanda yoqish/to'xtatish
   va bosilgandagi qism. Ichki style yo'q (CSP): faqat klasslar va CSS o'zgaruvchilari (CSSOM). */
(function () {
  "use strict";
  var cfg = window.HUSHYOR_SITE || {};
  var doc = document;
  var root = doc.documentElement;
  var body = doc.body;
  var lang = (root.getAttribute("lang") || "uz").slice(0, 2);
  var M = {};
  try { M = JSON.parse(body.getAttribute("data-i18n") || "{}"); } catch (e) { M = {}; }
  function tr(k, fallback) { return M[k] || fallback || ""; }
  var opened = Date.now();
  function mq(q) { return window.matchMedia ? window.matchMedia(q) : { matches: false }; }
  var motion = !mq("(prefers-reduced-motion: reduce)").matches;
  var hasIO = "IntersectionObserver" in window;
  body.classList.remove("no-js");

  function $(s, r) { return (r || doc).querySelector(s); }
  function $$(s, r) { return Array.prototype.slice.call((r || doc).querySelectorAll(s)); }
  function el(tag, attrs, text) {
    var e = doc.createElement(tag);
    Object.keys(attrs || {}).forEach(function (k) { e.setAttribute(k, attrs[k]); });
    if (text) e.textContent = text;
    return e;
  }

  // ---- mavzu: bitta tugma (yorug' ⇄ tungi). Tanlov tizim sozlamasiga teng bo'lsa saqlanmaydi — sayt yana tizimga
  // ergashadi (theme.js boshida qo'llaydi). O'tish — tugmadan yoyiladigan doira (View Transitions; bo'lmasa darhol)
  var THEME_KEY = "hushyor-theme";
  var themeBtn = $("#theme-btn");
  var sysDark = mq("(prefers-color-scheme: dark)");
  function forced() {
    var t = root.getAttribute("data-theme");
    return t === "light" || t === "dark" ? t : null;
  }
  function isDark() { var f = forced(); return f ? f === "dark" : !!sysDark.matches; }
  var themeMetas = $$('meta[name="theme-color"]');
  var THEME_COLOR = { light: "#f3f6f8", dark: "#0b1520" };
  function paintTheme() {
    // brauzer paneli rangi: tanlov bo'lsa — o'sha mavzu; bo'lmasa — har meta o'z media'si bo'yicha (tizim)
    var f = forced();
    themeMetas.forEach(function (m) {
      var own = /dark/.test(m.getAttribute("media") || "") ? THEME_COLOR.dark : THEME_COLOR.light;
      m.setAttribute("content", f ? THEME_COLOR[f] : own);
    });
    if (!themeBtn) return;
    var dark = isDark();
    var label = tr(dark ? "theme_to_light" : "theme_to_dark", dark ? "Light" : "Dark");
    themeBtn.setAttribute("data-mode", forced() || "system");
    themeBtn.setAttribute("aria-label", label);
    themeBtn.setAttribute("title", label);
  }
  function setTheme(dark) {
    var store = null;
    if (dark === !!sysDark.matches) root.removeAttribute("data-theme");
    else { store = dark ? "dark" : "light"; root.setAttribute("data-theme", store); }
    try {
      if (store) window.localStorage.setItem(THEME_KEY, store);
      else window.localStorage.removeItem(THEME_KEY);
    } catch (e) { /* saqlab bo'lmadi — faqat shu sahifada amal qiladi */ }
    paintTheme();
  }
  if (themeBtn) {
    var pendingDark = null;
    themeBtn.addEventListener("click", function () {
      var next = pendingDark === null ? !isDark() : !pendingDark;
      pendingDark = next;
      if (motion && typeof doc.startViewTransition === "function") {
        var r = themeBtn.getBoundingClientRect();
        var x = r.left + r.width / 2, y = r.top + r.height / 2;
        var w = window.innerWidth, h = window.innerHeight;
        root.style.setProperty("--vt-x", x + "px");
        root.style.setProperty("--vt-y", y + "px");
        root.style.setProperty("--vt-r", Math.ceil(Math.sqrt(Math.pow(Math.max(x, w - x), 2) + Math.pow(Math.max(y, h - y), 2))) + "px");
        root.classList.add("vt-theme");
        var done = function () { root.classList.remove("vt-theme"); if (pendingDark === next) pendingDark = null; };
        try {
          var vt = doc.startViewTransition(function () { setTheme(next); });
          vt.finished.then(done, done);
        } catch (e) { setTheme(next); done(); }
      } else {
        setTheme(next);
        pendingDark = null;
      }
    });
    if (sysDark.addEventListener) sysDark.addEventListener("change", paintTheme);
    paintTheme();
  }

  // ---- til va menyu oynalari (<details>): bittasi ochiq; tashqariga bosilsa, Esc yoki havola — yopiladi
  var pops = $$("details.lang, details.menu");
  pops.forEach(function (d) {
    d.addEventListener("toggle", function () {
      if (d.open) pops.forEach(function (o) { if (o !== d) o.open = false; });
    });
    $$("a", d).forEach(function (a) { a.addEventListener("click", function () { d.open = false; }); });
  });
  doc.addEventListener("click", function (e) {
    pops.forEach(function (d) { if (d.open && !d.contains(e.target)) d.open = false; });
  });
  doc.addEventListener("keydown", function (e) {
    if (e.key !== "Escape") return;
    pops.forEach(function (d) {
      if (d.open) { d.open = false; var s = $("summary", d); if (s) s.focus(); }
    });
  });

  // ---- sarlavha: aylantirilganda shisha fon; menyuda joriy bo'lim belgilanadi
  var top = $("#top");
  var ticking = false;
  function onScroll() {
    if (ticking || !top) return;
    ticking = true;
    window.requestAnimationFrame(function () {
      ticking = false;
      top.classList.toggle("scrolled", (window.scrollY || window.pageYOffset) > 8);
    });
  }
  window.addEventListener("scroll", onScroll, { passive: true });
  onScroll();
  var navLinks = $$(".nav a");
  if (hasIO && navLinks.length) {
    var spy = new IntersectionObserver(function (ents) {
      ents.forEach(function (e) {
        if (!e.isIntersecting) return;
        var id = e.target.id || "";
        navLinks.forEach(function (a) { a.classList.toggle("on", !!id && a.getAttribute("href") === "#" + id); });
      });
    }, { rootMargin: "-45% 0px -50% 0px" });
    $$("main > section").forEach(function (s) { spy.observe(s); });
  }

  // ---- aloqa ma'lumotlari (.env: CLOUD_SITE_PHONE, CLOUD_SITE_TELEGRAM, CLOUD_SITE_EMAIL, CLOUD_SITE_ADDRESS)
  var list = $("#contacts");
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
  var price = $("#price");
  var priceText = lang === "uz" ? cfg.price : cfg["price_" + lang];
  if (price && priceText) price.textContent = priceText;
  var demo = $("#demo-link");
  if (demo && cfg.demo) demo.classList.remove("hidden");

  // ---- bir martalik ko'rinish: bo'lim sarlavhalari (.in), ishga tushirish qadamlari (.seen)
  function once(nodes, opts, fn) {
    nodes = nodes.filter(Boolean);
    if (!hasIO) { nodes.forEach(fn); return; }
    var io = new IntersectionObserver(function (ents) {
      ents.forEach(function (e) {
        if (e.isIntersecting) { io.unobserve(e.target); fn(e.target); }
      });
    }, opts);
    nodes.forEach(function (n) { io.observe(n); });
  }
  once($$(".sec-head, .contact-text"), { threshold: 0.2, rootMargin: "0px 0px -6% 0px" },
       function (n) { n.classList.add("in"); });
  once([$("#steps")], { threshold: 0.35 }, function (n) { n.classList.add("seen"); });

  // ---- ekranda ko'rinish: .live — animatsiyalar faqat shunda o'ynaydi (batareya va protsessor tejaladi); kuzatuv
  // devori kataklari birinchi ko'ringanda "yoqiladi" (.on). «Qanday ishlaydi»da faqat sahna kuzatiladi
  function watchLive(pairs) {
    if (!hasIO) { pairs.forEach(function (p) { p[1].classList.add("live", "on", "seen"); }); return; }
    var io = new IntersectionObserver(function (ents) {
      ents.forEach(function (e) {
        pairs.forEach(function (p) {
          if (p[0] !== e.target) return;
          p[1].classList.toggle("live", e.isIntersecting);
          if (e.isIntersecting) p[1].classList.add("on", "seen");
        });
      });
    }, { threshold: 0.15 });
    pairs.forEach(function (p) { io.observe(p[0]); });
  }
  var livePairs = $$(".fv").map(function (n) { return [n, n]; });
  var vas = $("#vas");
  var vasFig = vas && $(".vas-fig", vas);
  if (vasFig) livePairs.push([vasFig, vas]);
  watchLive(livePairs);

  // ---- bosh sahna. Animatsiya CSS da (skriptsiz ham o'ynaydi). Skript bo'lsa: sahna ekranga chiqqanda boshidan
  // boshlanadi (telefonda u birinchi ekrandan pastda) va "Yana ko'rish" bilan qaytariladi (Web Animations API)
  var stage = $("#stage");
  var replay = $("#replay");
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
      var sio = new IntersectionObserver(function (ents) {
        var vis = ents.some(function (e) { return e.isIntersecting; });
        if (first) {
          first = false;
          if (vis) sio.disconnect(); else restart(false);
          return;
        }
        if (vis) { sio.disconnect(); restart(true); }
      }, { threshold: 0.35 });
      sio.observe($(".monitor", stage) || stage);
    }
  }

  // ---- "Qanday ishlaydi": qadam bosilsa — sahnada o'sha qadamning ko'rinishi (aria-pressed) va o'sha qadam qoladi.
  // Bosilmaguncha (va harakat kamaytirilmagan bo'lsa) qadamlar navbat bilan o'zi almashadi — bu faqat ko'rinish
  // (.tour klassi, aria-pressed emas). Navbat vaqti — qadam yonidagi chiziq (CSS animatsiya, 6–7,5 s): u tugaganda
  // keyingisi. Sichqoncha yoki fokus ro'yxatda bo'lsa, pauza bosilgan yoki sahna ekranda bo'lmasa — chiziq to'xtab
  // turadi. Sahna: .vas[data-step] va .vas-sc.cur (Telegram sahnasi ikki qadamga xizmat qiladi: tg va back);
  // data-zone — video qayerda (do'kon ichida / tashqariga faqat hodisa). Telefonda qadamlar tugmachalar qatori,
  // faol qadam izohi ostida (.vas-desc)
  if (vas) {
    var items = $$(".vas-it", vas);
    var scenes = $$(".vas-sc", vas);
    var scTg = $(".sc-tg", vas);
    var vlist = $(".vas-list", vas);
    var descs = $$("#vas-desc p");
    var pauseBtn = $("#vas-pause");
    var cur = -1, touched = false, rowTouched = false;
    var held = function () {
      return vas.classList.contains("hold") || (vlist && (vlist.matches(":hover") || vlist.contains(doc.activeElement)));
    };
    // tugma: avto-ko'rsatuv ketayotgan bo'lsa — "to'xtatish", to'xtagan yoki qadam tanlangan bo'lsa — "davom ettirish"
    // (holat yozuv bilan aytiladi — aria-pressed emas)
    var paintPause = function () {
      if (!pauseBtn) return;
      var stopped = touched || vas.classList.contains("hold");
      var label = pauseBtn.getAttribute(stopped ? "data-l-play" : "data-l-pause") || "";
      pauseBtn.setAttribute("data-state", stopped ? "play" : "pause");
      pauseBtn.setAttribute("aria-label", label);
      pauseBtn.title = label;
    };
    var select = function (i, user) {
      cur = i;
      var key = items[i].getAttribute("data-k");
      items.forEach(function (b, j) {
        b.classList.toggle("tour", !user && j === i);
        b.setAttribute("aria-pressed", user && j === i ? "true" : "false");
      });
      vas.setAttribute("data-step", key);
      vas.setAttribute("data-zone", items[i].getAttribute("data-zone") || "in");
      // Telegram sahnasining ichki holati sahnaning o'zida: boshqa qadamga o'tib xiralashayotganda ham o'zgarmaydi
      if (scTg && (key === "tg" || key === "back")) scTg.classList.toggle("is-back", key === "back");
      scenes.forEach(function (sc) {
        sc.classList.toggle("cur", (" " + sc.getAttribute("data-k") + " ").indexOf(" " + key + " ") >= 0);
      });
      descs.forEach(function (p) { p.classList.toggle("on", p.getAttribute("data-k") === key); });
      // telefonda tugmachalar qatori surilib, faol tugmacha ko'rinadi (sahifa emas — faqat qator)
      if (vlist && !rowTouched && vlist.scrollWidth > vlist.clientWidth + 4 && vas.classList.contains("live")) {
        var li = items[i].parentNode;
        var left = Math.max(0, li.offsetLeft - 16);
        if (typeof vlist.scrollTo === "function") vlist.scrollTo({ left: left, behavior: motion ? "smooth" : "auto" });
        else vlist.scrollLeft = left;
      }
      paintPause();
    };
    if (vlist) {
      ["pointerdown", "touchstart", "wheel"].forEach(function (ev) {
        vlist.addEventListener(ev, function () { rowTouched = true; }, { passive: true });
      });
    }
    items.forEach(function (b, i) {
      b.addEventListener("click", function () { touched = true; vas.classList.remove("hold"); select(i, true); });
      b.addEventListener("animationend", function (e) {
        if (e.animationName.indexOf("vas-rail") !== 0 || touched || i !== cur) return;
        if (!vas.classList.contains("live") || held()) {
          // (chiziq to'xtatilgan bo'lishi kerak edi — ehtiyot uchun: shu qadamda qolib, keyinroq qayta urinadi)
          b.classList.remove("tour"); void b.offsetWidth; b.classList.add("tour");
          return;
        }
        select((i + 1) % items.length, false);
      });
    });
    // Avto-ko'rsatuv tugmasi (faqat harakat kamaytirilmagan bo'lsa): to'xtatadi / davom ettiradi; qadam bosilgandan
    // keyin — avto-ko'rsatuvni shu qadamdan qayta yoqadi
    if (pauseBtn && motion) {
      pauseBtn.hidden = false;
      pauseBtn.addEventListener("click", function () {
        if (touched) {
          touched = false;
          rowTouched = false;
          vas.classList.remove("hold");
          var b = items[cur];
          select(cur, false);
          b.classList.remove("tour"); void b.offsetWidth; b.classList.add("tour");
        } else {
          vas.classList.toggle("hold");
        }
        paintPause();
      });
    }
    select(0, false);
  }

  // ---- ekranlar: tablar (klaviatura: ← → Home End)
  var tabs = $$('.tabs [role="tab"]');
  function show(i, focus, animate) {
    tabs.forEach(function (t, j) {
      var on = i === j;
      t.setAttribute("aria-selected", on ? "true" : "false");
      t.tabIndex = on ? 0 : -1;
      var p = doc.getElementById(t.getAttribute("aria-controls"));
      if (!p) return;
      p.hidden = !on;
      p.classList.remove("in");
      if (on && animate) {
        void p.offsetWidth;            // animatsiya har bosishda qaytadan boshlansin
        p.classList.add("in");
      }
    });
    if (focus) tabs[i].focus();
    var bar = tabs[i] && tabs[i].parentNode;
    if (bar && bar.scrollWidth > bar.clientWidth + 4) {
      var left = Math.max(0, tabs[i].offsetLeft - 24);
      if (typeof bar.scrollTo === "function") bar.scrollTo({ left: left, behavior: motion ? "smooth" : "auto" });
    }
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
  var form = $("#lead-form");
  if (!form) return;
  var msg = $("#form-msg");
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
  form.addEventListener("input", function (e) {
    form.classList.remove("sent");
    var f = e.target && e.target.parentNode;
    if (f && f.classList && f.classList.contains("bad")) f.classList.remove("bad");
  });
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
    var payload = {
      name: name, phone: phone, store: f.store.value.trim(), city: f.city.value.trim(),
      branches: f.branches.value, message: f.message.value.trim(), hp_extra: f.hp_extra.value,
      elapsed: (Date.now() - opened) / 1000, source: source()
    };
    btn.disabled = true;
    say(tr("sending", "…"), true);
    fetch(cfg.api || "/api/v1/leads", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload), credentials: "omit"
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
