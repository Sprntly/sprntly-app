import type { NextConfig } from "next"

// Optional base path for legacy `/demo` hosting (set NEXT_PUBLIC_BASE_PATH=/demo).
// Default: app routes at `/`, `/brief`, `/evidence`, etc.
const basePath = process.env.NEXT_PUBLIC_BASE_PATH?.replace(/\/$/, "") || ""

const nextConfig: NextConfig = {
  reactStrictMode: true,
  // Default: "export" (Apurva's static-export/nginx deploy). Local dev:
  // set DISABLE_STATIC_EXPORT=1 to enable SSR so dynamic routes work in dev.
  output: process.env.DISABLE_STATIC_EXPORT === "1" ? undefined : "export",
  ...(basePath ? { basePath } : {}),
  trailingSlash: false,
  images: {
    unoptimized: true,
  },
}

export default nextConfig
