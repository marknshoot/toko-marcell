export default function ProductReviews({ reviewsData }) {
  if (!reviewsData || !reviewsData.reviews || reviewsData.reviews.length === 0) {
    return (
      <section className="mt-12 border-t border-border pt-8">
        <h2 className="text-sm font-semibold uppercase tracking-[0.12em] text-foreground">
          Customer Reviews
        </h2>
        <p className="mt-4 text-sm text-muted">
          No customer reviews yet for this product. Be the first to review it!
        </p>
      </section>
    );
  }

  const { total, averageRating, breakdown, reviews } = reviewsData;

  const renderStars = (rating) => {
    const full = Math.floor(rating);
    const half = rating - full >= 0.5 ? 1 : 0;
    const empty = 5 - full - half;

    return (
      <div className="flex items-center text-amber-500" aria-label={`${rating} out of 5 stars`}>
        {Array.from({ length: full }).map((_, i) => (
          <span key={`f-${i}`} className="text-base">★</span>
        ))}
        {half > 0 && <span className="text-base">★</span>}
        {Array.from({ length: empty }).map((_, i) => (
          <span key={`e-${i}`} className="text-base text-zinc-300 dark:text-zinc-600">★</span>
        ))}
      </div>
    );
  };

  return (
    <section className="mt-12 border-t border-border pt-8">
      <div className="flex flex-col gap-8 md:flex-row md:items-start md:justify-between">
        <div className="w-full md:w-1/3">
          <h2 className="text-sm font-semibold uppercase tracking-[0.12em] text-foreground">
            Customer Reviews
          </h2>

          <div className="mt-4 flex items-baseline gap-3">
            <span className="text-4xl font-bold tracking-tight text-foreground">
              {averageRating !== null ? averageRating.toFixed(1) : "—"}
            </span>
            <div>
              {averageRating !== null ? renderStars(averageRating) : null}
              <p className="mt-0.5 text-xs text-muted">
                Based on {total.toLocaleString()} {total === 1 ? "review" : "reviews"}
              </p>
            </div>
          </div>

          <div className="mt-6 space-y-2">
            {[5, 4, 3, 2, 1].map((stars) => {
              const count = breakdown[String(stars)] || 0;
              const pct = total > 0 ? Math.round((count / total) * 100) : 0;
              return (
                <div key={stars} className="flex items-center gap-2 text-xs">
                  <span className="w-7 text-muted">{stars} ★</span>
                  <div className="h-2 flex-1 overflow-hidden rounded-full bg-zinc-200 dark:bg-zinc-800">
                    <div
                      className="h-full bg-amber-400 transition-all duration-300"
                      style={{ width: `${pct}%` }}
                    />
                  </div>
                  <span className="w-8 text-right font-medium text-muted">{pct}%</span>
                </div>
              );
            })}
          </div>
        </div>

        <div className="w-full md:w-2/3">
          <div className="space-y-6 divide-y divide-border">
            {reviews.map((rev) => (
              <article key={rev.id} className="pt-6 first:pt-0">
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-2.5">
                    <div className="flex h-7 w-7 items-center justify-center rounded-full bg-surface-muted text-xs font-semibold uppercase text-foreground">
                      {rev.author ? rev.author.charAt(0) : "C"}
                    </div>
                    <span className="text-sm font-medium text-foreground">
                      {rev.author || "Amazon Customer"}
                    </span>
                  </div>

                  {rev.reviewDate ? (
                    <time className="text-xs text-muted">{rev.reviewDate}</time>
                  ) : null}
                </div>

                <div className="mt-2 flex items-center gap-2">
                  {renderStars(rev.rating)}
                  {rev.summary ? (
                    <h3 className="text-sm font-semibold text-foreground">
                      {rev.summary}
                    </h3>
                  ) : null}
                </div>

                {rev.verified ? (
                  <p className="mt-1 inline-flex items-center gap-1 text-[11px] font-medium text-emerald-600 dark:text-emerald-400">
                    <svg className="h-3 w-3" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2.5} d="M5 13l4 4L19 7" />
                    </svg>
                    Verified Purchase
                  </p>
                ) : null}

                <p className="mt-2 text-sm leading-relaxed text-muted">
                  {rev.comment}
                </p>
              </article>
            ))}
          </div>
        </div>
      </div>
    </section>
  );
}
