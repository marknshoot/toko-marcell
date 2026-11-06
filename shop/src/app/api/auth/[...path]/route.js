// Same-origin proxy for the admin auth endpoints.
//
// The browser only ever calls this origin, so the HttpOnly refresh cookie is
// first-party: a cross-site *.vercel.app -> *.onrender.com request never sends a
// SameSite=Lax cookie, which silently ended every admin session as soon as the
// 15-minute access token expired — no matter that the refresh token was good for
// another 7 days.
//
// Only /api/auth/* is proxied. Chats, search and catalog calls keep talking to the
// API directly, so streaming (SSE) and long copilot turns never depend on this
// function's timeout.
//
// The upstream is resolved per request, not at build time: a Next.js rewrite would
// bake its destination into .next/required-server-files.json and break the Docker
// image, where the API is reachable as http://api:8000 rather than on localhost.

const API_ORIGIN = (
  process.env.INTERNAL_API_URL ||
  process.env.NEXT_PUBLIC_API_URL ||
  "http://localhost:8001"
).replace(/\/+$/, "");

// Hop-by-hop headers (RFC 9110 §7.6.1) plus the ones undici manages itself.
const REQUEST_HEADERS_SKIP = new Set([
  "host",
  "connection",
  "content-length",
  "accept-encoding",
  "transfer-encoding",
]);

// The body is re-streamed, so its framing and encoding no longer apply.
const RESPONSE_HEADERS_SKIP = new Set([
  "connection",
  "content-encoding",
  "content-length",
  "keep-alive",
  "transfer-encoding",
]);

async function forward(request, { params }) {
  const { path } = await params;
  const incoming = new URL(request.url);
  const target = `${API_ORIGIN}/auth/${path.join("/")}${incoming.search}`;

  const headers = new Headers();
  for (const [key, value] of request.headers) {
    if (!REQUEST_HEADERS_SKIP.has(key.toLowerCase())) {
      headers.set(key, value);
    }
  }
  // The API marks the cookie Secure when it sees https; the browser's view of the
  // scheme is forwarded verbatim so localhost (http) keeps working.
  if (!headers.has("x-forwarded-proto")) {
    headers.set("x-forwarded-proto", incoming.protocol.replace(":", ""));
  }
  if (!headers.has("x-forwarded-host")) {
    headers.set("x-forwarded-host", incoming.host);
  }

  const hasBody = request.method !== "GET" && request.method !== "HEAD";
  const upstream = await fetch(target, {
    method: request.method,
    headers,
    body: hasBody ? await request.arrayBuffer() : undefined,
    redirect: "manual",
    cache: "no-store",
  });

  const responseHeaders = new Headers();
  upstream.headers.forEach((value, key) => {
    const lower = key.toLowerCase();
    if (RESPONSE_HEADERS_SKIP.has(lower) || lower === "set-cookie") {
      return;
    }
    responseHeaders.set(key, value);
  });
  // forEach would join several cookies with ", " and corrupt Expires; getSetCookie
  // keeps one Set-Cookie header per cookie.
  for (const cookie of upstream.headers.getSetCookie?.() ?? []) {
    responseHeaders.append("set-cookie", cookie);
  }

  return new Response(upstream.body, {
    status: upstream.status,
    statusText: upstream.statusText,
    headers: responseHeaders,
  });
}

export const dynamic = "force-dynamic";
export const runtime = "nodejs";
// Render's free tier sleeps; waking it on the login/refresh path can take ~50 s.
export const maxDuration = 60;

export const GET = forward;
export const POST = forward;
export const PUT = forward;
export const PATCH = forward;
export const DELETE = forward;
export const HEAD = forward;
export const OPTIONS = forward;
