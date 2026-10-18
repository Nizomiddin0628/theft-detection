/* Hushyor AI — landing sayt (2.8.0): aloqa ma'lumotlari (sozlama.js), bosh sahnani qayta ko'rsatish, ekranlar
   tablari, so'rov formasi. Animatsiyaning o'zi — style.css da (CSS); bu yerda faqat foydalanuvchi bosgandagi qism. */
(function () {
  "use strict";
  var cfg = window.HUSHYOR_SITE || {};
  var opened = Date.now();
  var motion = !(window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches);
  document.body.classList.remove("no-js");

  function el(tag, attrs, text) {
    var e = document.createElement(tag);
    Object.keys(attrs || {}).forEach(function (k) { e.setAttribute(k, attrs[k]); });
    if (text) e.textContent = text;
    return e;
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
    if (cfg.phone) contact("Telefon", cfg.phone, "tel:" + String(cfg.phone).replace(/[^\d+]/g, ""));
    if (cfg.telegram) contact("Telegram", "@" + String(cfg.telegram).replace(/^https:\/\/t\.me\//, ""), cfg.telegram);
    if (cfg.email) contact("Email", cfg.email, "mailto:" + cfg.email);
    if (cfg.address) contact("Manzil", cfg.address, "");
  }
  var price = document.getElementById("price");
  if (price && cfg.price) price.textContent = cfg.price;
  var ver = document.getElementById("foot-ver");
  if (ver && cfg.version) ver.textContent = "versiya " + cfg.version;
  var demo = document.getElementById("demo-link");
  if (demo && cfg.demo) demo.classList.remove("hidden");

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
    if ("IntersectionObserver" in window) {
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
    ["utm_source", "utm_campaign"].forEach(function (k) { if (q.get(k)) parts.push(k.slice(4) + "=" + q.get(k)); });
    return parts.join(" · ").slice(0, 200);
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
    if (name.length < 2) { say("Ismingizni yozing", false); f.name.focus(); return; }
    if (digits.length < 9) { say("Telefon raqamini to'liq yozing: +998 90 123 45 67", false); f.phone.focus(); return; }
    var body = {
      name: name, phone: phone, store: f.store.value.trim(), city: f.city.value.trim(),
      branches: f.branches.value, message: f.message.value.trim(), hp_extra: f.hp_extra.value,
      elapsed: (Date.now() - opened) / 1000, source: source()
    };
    btn.disabled = true;
    say("Yuborilmoqda…", true);
    fetch(cfg.api || "/api/v1/leads", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body), credentials: "omit"
    })
      .then(function (r) {
        return r.json().catch(function () { return {}; }).then(function (d) { return { ok: r.ok, d: d }; });
      })
      .then(function (x) {
        if (x.ok) {
          form.reset();
          say("Rahmat! So'rovingiz qabul qilindi — tez orada bog'lanamiz.", true);
          form.classList.add("sent");
        } else {
          say((x.d && x.d.detail) || "Yuborib bo'lmadi — birozdan keyin qayta urinib ko'ring.", false);
        }
      })
      .catch(function () {
        say("Internet bilan muammo — qayta urinib ko'ring" + (cfg.phone ? " yoki qo'ng'iroq qiling: " + cfg.phone : "."), false);
      })
      .then(function () { btn.disabled = false; });
  });
})();
