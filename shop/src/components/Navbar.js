import Link from "next/link";
import CartLink from "./CartLink";

function ShieldIcon() {
  return (
    <svg
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.8"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      className="h-4 w-4"
    >
      <path d="M12 3l7 3v5c0 4.5-3 7.5-7 10-4-2.5-7-5.5-7-10V6l7-3z" />
      <path d="M9 12l2 2 4-4" />
    </svg>
  );
}

export default function Navbar() {
  return (
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
        <Link
          href="/admin/kpi"
          className="inline-flex items-center gap-1.5 text-muted no-underline transition-colors hover:text-foreground"
        >
          <ShieldIcon />
          <span>Dashboard</span>
        </Link>
        <CartLink />
      </div>
    </header>
  );
}
