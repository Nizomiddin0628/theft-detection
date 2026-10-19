/* Hushyor AI bulut paneli (3.0.0): tanlangan mavzu — <head> da, sahifa chizilishidan oldin (oq "chaqnash" bo'lmasin).
   "light" / "dark" — majburiy, yo'q bo'lsa — tizim sozlamasi. Tugma — components/ThemeToggle.tsx. Sayt bilan bir xil kalit. */
(function () {
  "use strict";
  try {
    var t = window.localStorage.getItem("hushyor-theme");
    if (t === "light" || t === "dark") document.documentElement.setAttribute("data-theme", t);
  } catch (e) {
    /* xususiy oyna yoki saqlash taqiqlangan — tizim mavzusi */
  }
})();
