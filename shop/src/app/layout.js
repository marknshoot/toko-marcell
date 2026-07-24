import { Geist } from "next/font/google";
import Link from "next/link";
import "./globals.css";
import { CartProvider } from "@/components/CartProvider";
import CartLink from "@/components/CartLink";


const geist = Geist({
  subsets: ["latin"],
  variable: "--font-geist-sans",
  display: "swap",
});

export const metadata = {
  title: "Toko Marcell",
  description: "Single brand shop demo",
};

export default function RootLayout({ children }) {
  return (
    <html lang="en" className={geist.variable}>
      <body
        className={`${geist.className} flex min-h-screen flex-col bg-background text-foreground antialiased`}
      >
        <CartProvider>
          <header className="sticky top-0 z-50 flex flex-wrap items-center justify-between gap-4 border-b border-border bg-surface px-5 py-4">

            <Link
              href="/"
              className="text-xl font-semibold tracking-tight text-foreground no-underline"
            >
              Toko Marcell
            </Link>

            <CartLink />

          </header>

          <div className="flex-1">{children}</div>

          <footer className="border-t border-border px-5 py-4 text-sm text-muted">
            Made by Marcell Hermawan Kristianto
          </footer>
        </CartProvider>
      </body>
    </html>
  );
}
