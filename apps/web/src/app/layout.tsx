import type { Metadata } from "next";
import type { ReactNode } from "react";
import { AuthProvider } from "@/lib/auth";
import { SocketProvider } from "@/lib/socket";
import "./globals.css";

export const metadata: Metadata = {
  title: "ExamPrep",
  description: "Study from your own material — answers grounded in your notes.",
};

/**
 * Providers wrap everything because both are app-wide singletons: one session
 * and one WebSocket. The socket sits inside the auth provider since it
 * reconnects as whoever is signed in.
 */
export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en" className="h-full">
      <body className="min-h-full">
        <AuthProvider>
          <SocketProvider>{children}</SocketProvider>
        </AuthProvider>
      </body>
    </html>
  );
}
