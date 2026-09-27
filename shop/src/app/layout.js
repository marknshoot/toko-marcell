import { Geist } from "next/font/google";
import Link from "next/link";
import "./globals.css";
import { CartProvider } from "@/components/CartProvider";
import CartLink from "@/components/CartLink";
import FloatingCopilot from "@/components/FloatingCopilot";

const geist = Geist({
  subsets: ["latin"],
  variable: "--font-geist-sans",
  display: "swap",
});

export const metadata = {
  title: "Toko Marcell",
  description:
    "6,000-product fashion catalog with hybrid search, session recommendations and a QRIS demo checkout.",
};

export default function RootLayout({ children }) {
  return (
    <html lang="en" className={geist.variable}>
      <body
        className={`${geist.className} flex min-h-screen flex-col bg-background text-foreground antialiased`}
      >
        <CartProvider>
          <header className="sticky top-0 z-50 flex flex-wrap items-center justify-between gap-4 border-b border-border bg-surface px-5 py-3.5 sm:px-8">
            <div className="flex items-center gap-6">
              <Link
                href="/"
                className="text-lg font-bold tracking-tight text-foreground no-underline"
              >
                Toko Marcell
              </Link>
            </div>

            <div className="flex items-center gap-4">
              <CartLink />
            </div>
          </header>

          <div className="flex-1">{children}</div>

          <FloatingCopilot />

          <footer className="border-t border-border bg-surface px-5 py-6 sm:px-8 text-xs text-muted">
            <div className="mx-auto flex max-w-6xl flex-col items-start gap-3">
              <p>
                © 2026 Toko Marcell — Karya Marcell Hermawan Kristianto (Binus DS).
              </p>
              <div>
                <a
                  href="https://github.com/marknshoot/toko-marcell"
                  target="_blank"
                  rel="noopener noreferrer"
                  className="hover:text-foreground hover:underline"
                >
                  GitHub
                </a>
              </div>
            </div>
          </footer>
        </CartProvider>
      </body>
    </html>
  );
}
