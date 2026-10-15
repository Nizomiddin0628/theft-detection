// Hushyor AI — brauzer push bildirishnomalari (panel yopiq bo'lsa ham)
self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (e) => e.waitUntil(self.clients.claim()));

self.addEventListener("push", (e) => {
  let d = {};
  try {
    d = e.data ? e.data.json() : {};
  } catch (_) {
    d = { title: "Hushyor AI", body: e.data ? e.data.text() : "" };
  }
  const title = d.title || "Hushyor AI";
  e.waitUntil(
    self.registration.showNotification(title, {
      body: d.body || "",
      tag: d.tag || undefined,
      renotify: Boolean(d.tag),
      icon: "/icon.svg",
      badge: "/icon.svg",
      data: { url: d.url || "/" },
    }),
  );
});

self.addEventListener("notificationclick", (e) => {
  e.notification.close();
  const raw = (e.notification.data && e.notification.data.url) || "/";
  const url = new URL(raw, self.location.origin);
  if (url.origin !== self.location.origin) return; // faqat o'z panelimiz
  e.waitUntil(
    self.clients.matchAll({ type: "window", includeUncontrolled: true }).then((list) => {
      for (const c of list) {
        if (new URL(c.url).origin === url.origin && "focus" in c) {
          c.navigate(url.href);
          return c.focus();
        }
      }
      return self.clients.openWindow(url.href);
    }),
  );
});
