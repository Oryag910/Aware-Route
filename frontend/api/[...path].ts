import type { VercelRequest, VercelResponse } from "@vercel/node";

// Same-origin API proxy: the browser only ever talks to this Vercel
// deployment. BACKEND_URL (server-side env var, never VITE_-prefixed) picks
// the upstream Render service -- production Render for the Production
// target, the matching Render Service Preview for a PR's Preview target.
const HOP_BY_HOP_HEADERS = new Set([
  "connection",
  "keep-alive",
  "proxy-authenticate",
  "proxy-authorization",
  "te",
  "trailer",
  "transfer-encoding",
  "upgrade",
  "host",
  "content-length",
]);

export default async function handler(
  req: VercelRequest,
  res: VercelResponse,
): Promise<void> {
  const backendUrl = process.env.BACKEND_URL;
  if (!backendUrl) {
    res.status(502).json({ error: "BACKEND_URL is not configured for this deployment." });
    return;
  }

  // `path` is the catch-all route param injected by the `[...path]` filename,
  // not a real query parameter, so it's excluded from the forwarded query string.
  const pathParam = req.query.path;
  const path = Array.isArray(pathParam) ? pathParam.join("/") : (pathParam ?? "");
  const base = backendUrl.endsWith("/") ? backendUrl : `${backendUrl}/`;
  const upstream = new URL(path, base);

  if (upstream.hostname === req.headers.host) {
    res.status(502).json({ error: "BACKEND_URL must not point back at this deployment." });
    return;
  }

  for (const [key, value] of Object.entries(req.query)) {
    if (key === "path") continue;
    for (const v of Array.isArray(value) ? value : [value]) {
      upstream.searchParams.append(key, v);
    }
  }

  const headers = new Headers();
  for (const [key, value] of Object.entries(req.headers)) {
    if (value === undefined || HOP_BY_HOP_HEADERS.has(key.toLowerCase())) continue;
    headers.set(key, Array.isArray(value) ? value.join(", ") : value);
  }
  const forwardedFor =
    (req.headers["x-forwarded-for"] as string | undefined)?.split(",")[0]?.trim() ??
    req.socket.remoteAddress ??
    "";
  if (forwardedFor) headers.set("x-forwarded-for", forwardedFor);
  if (req.headers.host) headers.set("x-forwarded-host", req.headers.host);
  headers.set("x-forwarded-proto", "https");

  const method = req.method ?? "GET";
  const hasBody = method !== "GET" && method !== "HEAD";
  let body: string | undefined;
  if (hasBody && req.body !== undefined && req.body !== null && req.body !== "") {
    body = typeof req.body === "string" ? req.body : JSON.stringify(req.body);
    if (!headers.has("content-type")) headers.set("content-type", "application/json");
  }

  let upstreamResponse: Response;
  try {
    upstreamResponse = await fetch(upstream, { method, headers, body, redirect: "manual" });
  } catch {
    res.status(502).json({ error: "Upstream backend request failed." });
    return;
  }

  res.status(upstreamResponse.status);
  res.setHeader("cache-control", "no-store");
  upstreamResponse.headers.forEach((value, key) => {
    const lower = key.toLowerCase();
    if (HOP_BY_HOP_HEADERS.has(lower) || lower === "cache-control") return;
    res.setHeader(key, value);
  });

  const buffer = Buffer.from(await upstreamResponse.arrayBuffer());
  res.send(buffer);
}
