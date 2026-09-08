import type { Metadata, Viewport } from "next";
import type { ReactNode } from "react";
import { Instrument_Serif, JetBrains_Mono, Outfit } from "next/font/google";
import { AuthProvider } from "@/lib/auth";
import { SocketProvider } from "@/lib/socket";
import "./globals.css";

/**
 * The three families the design system names, self-hosted by Next rather than
 * pulled from fonts.googleapis.com by the `@import` in its `tokens/fonts.css`.
 *
 * Same faces and weights; the difference is that these are served from our own
 * origin, so there is no render-blocking third-party request and no flash of
 * fallback type. The CSS variables are the ones every token file expects.
 */
const display = Instrument_Serif({
  variable: "--font-display",
  subsets: ["latin"],
  weight: "400",
  // The serif is only ever set italic in this system.
  style: ["normal", "italic"],
  display: "swap",
  fallback: ["Georgia", "serif"],
});

const sans = Outfit({
  variable: "--font-sans",
  subsets: ["latin"],
  weight: ["300", "400", "500", "600"],
  display: "swap",
  fallback: ["Helvetica Neue", "Arial", "sans-serif"],
});

const mono = JetBrains_Mono({
  variable: "--font-mono",
  subsets: ["latin"],
  weight: ["400", "500"],
  display: "swap",
  fallback: ["ui-monospace", "monospace"],
});

export const metadata: Metadata = {
  title: "Examprep",
  description:
    "Upload your notes once. Ask them anything, then let Examprep quiz you on the parts you keep missing.",
};

export const viewport: Viewport = {
  // Examprep is dark-only, so the browser chrome should match rather than
  // framing a near-black page in white.
  themeColor: "#0A0A0B",
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html
      lang="en"
      className={`${display.variable} ${sans.variable} ${mono.variable}`}
      style={{ height: "100%" }}
    >
      <body style={{ minHeight: "100%" }}>
        <AuthProvider>
          <SocketProvider>{children}</SocketProvider>
        </AuthProvider>
      </body>
    </html>
  );
}
