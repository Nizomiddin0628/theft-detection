/* Hushyor AI sayti (3.0.0): tanlangan mavzu — <head> da, sahifa chizilishidan oldin. "light" / "dark" — majburiy,
   yo'q bo'lsa — tizim sozlamasi (prefers-color-scheme). Tugma — app.js da. Bulut paneli bilan bir xil kalit. */
(function () {
  "use strict";
  try {
    var t = window.localStorage.getItem("hushyor-theme");
    if (t === "light" || t === "dark") document.documentElement.setAttribute("data-theme", t);
  } catch (e) { /* xususiy oyna yoki saqlash taqiqlangan — tizim mavzusi */ }
})();
