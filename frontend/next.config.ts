import type { NextConfig } from "next";

// Proxy /api/* to the FastAPI backend so the browser hits one origin (no CORS in dev).
// Override the target with BACKEND_URL when deploying.
const BACKEND_URL = process.env.BACKEND_URL ?? "http://127.0.0.1:8000";

const nextConfig: NextConfig = {
  // The /api proxy defaults to a 30s timeout, but a PDF's analyze+translate pipeline
  // (concurrent Claude calls) can run 1-2 min. Raise it so the proxy doesn't 500 the
  // request out from under a still-working backend. Applies to `next start` too.
  experimental: { proxyTimeout: 600_000 },
  async rewrites() {
    return [{ source: "/api/:path*", destination: `${BACKEND_URL}/api/:path*` }];
  },
};

export default nextConfig;
