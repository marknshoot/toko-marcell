import { getProductReviews } from "@/lib/api";
import ProductReviews from "./ProductReviews";

// Fetched inside a Suspense boundary so the product itself is painted first.
export default async function ProductReviewsSection({ productId }) {
  const reviewsData = await getProductReviews(productId, 10);
  return <ProductReviews reviewsData={reviewsData} />;
}
