import type { NextConfig } from "next"

// The demo is mounted at /demo on sprntly.ai via a Vercel rewrite from the
// marketing repo (vercel.json: /demo/* -> https://api.sprntly.ai/demo/*).
// We build with `output: 'export'` so we can serve the result as plain static
// files via nginx on EC2 — no Node server, no Vercel project required.
const nextConfig: NextConfig = {
  reactStrictMode: true,
  output: "export",
  basePath: "/demo",
  trailingSlash: false,
  images: {
    unoptimized: true,
  },
}

export default nextConfig
