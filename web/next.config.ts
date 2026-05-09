import type { NextConfig } from "next"

// The demo is mounted at /demo on sprntly.ai via a Vercel rewrite from the
// marketing site (vercel.json: /demo/* -> sprntly-demo.vercel.app/demo/*).
// We set basePath: '/demo' so Next.js generates URLs like /demo/sign-in
// rather than /sign-in.
const nextConfig: NextConfig = {
  reactStrictMode: true,
  basePath: "/demo",
  trailingSlash: false,
}

export default nextConfig
