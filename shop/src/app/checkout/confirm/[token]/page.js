import Link from "next/link";
import { notFound } from "next/navigation";
import { getOrder } from "@/lib/api";
import { formatRp } from "@/lib/formatRp";
import ClearCart from "@/components/ClearCart";

// The real confirmation page. It shows what the SERVER holds, keyed by the token
// in the URL — which is why it survives a refresh, a new tab or another device,
// and why opening it without a valid token shows a 404 instead of a fake receipt.
export default async function ConfirmPage({ params }) {
  const { token } = await params;
  const order = await getOrder(token);

  if (!order) {
    notFound();
  }

  return (
    <main className="py-12">
      <ClearCart />
      <div className="mx-auto max-w-2xl px-7">
        <p className="text-xs font-medium uppercase tracking-[0.12em] text-muted">
          Order {order.status}
        </p>
        <h1 className="mt-2 text-3xl font-semibold tracking-tight text-foreground">
          Order confirmed (demo)
        </h1>
        <p className="mt-4 text-muted">
          This is a demo checkout. No real money was charged and nothing will ship.
        </p>

        <dl className="mt-8 divide-y divide-border border-y border-border text-sm">
          {order.items.map((item) => (
            <div key={item.asin} className="flex items-baseline justify-between gap-4 py-3">
              <dt className="text-foreground">
                {item.title}
                <span className="text-muted"> × {item.qty}</span>
              </dt>
              <dd className="shrink-0 text-foreground">
                {formatRp(item.unitPriceIdr * item.qty)}
              </dd>
            </div>
          ))}
          <div className="flex items-baseline justify-between gap-4 py-3">
            <dt className="font-medium text-foreground">
              Total ({order.itemCount} item{order.itemCount === 1 ? "" : "s"})
            </dt>
            <dd className="font-medium text-foreground">{formatRp(order.totalIdr)}</dd>
          </div>
        </dl>

        <p className="mt-4 text-xs text-muted">
          Order reference <span className="font-mono">{order.token}</span> ·{" "}
          {new Date(order.createdAt).toLocaleString("en-GB")}
        </p>

        <Link
          href="/#catalog"
          className="mt-8 inline-flex items-center justify-center rounded-full bg-cta px-4 py-3 text-sm font-medium text-white no-underline transition hover:bg-cta-hover"
        >
          Continue shopping
        </Link>
      </div>
    </main>
  );
}
