const API_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8001";

export async function getProducts({ category, limit = 24, offset = 0 } = {}) {
  const params = new URLSearchParams();
  params.set("limit", String(limit));
  params.set("offset", String(offset));
  if (category && category !== "All") {
    params.set("category", category);
  }

  const response = await fetch(`${API_URL}/products?${params.toString()}`);
  if (!response.ok) {
    throw new Error("Could not load products");
  }
  return response.json();
}
