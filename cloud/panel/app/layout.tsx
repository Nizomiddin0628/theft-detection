import "@fontsource-variable/ibm-plex-sans/wght.css";
import "./globals.css";

import type { Metadata, Viewport } from "next";
import type { ReactNode } from "react";

export const metadata: Metadata = {
  title: "Hushyor AI",
  description: "Do'kon kameralaridagi shubhali harakatlarni tekshirish paneli",
  manifest: "/manifest.webmanifest",
  icons: { icon: "/icon.svg" },
};

export const viewport: Viewport = {
  themeColor: "#0f1b2a",
  width: "device-width",
  initialScale: 1,
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    // suppressHydrationWarning: theme.js data-theme ni React'dan oldin qo'yadi (3.0.0, mavzu tugmasi)
    <html lang="uz" suppressHydrationWarning>
      <head>
        {/* eslint-disable-next-line @next/next/no-sync-scripts -- ataylab sinxron: mavzu birinchi chizishdan oldin */}
        <script src="/theme.js" />
      </head>
      <body>{children}</body>
    </html>
  );
}
