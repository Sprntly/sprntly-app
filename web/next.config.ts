import type { NextConfig } from "next"

// Optional base path for legacy `/demo` hosting (set NEXT_PUBLIC_BASE_PATH=/demo).
// Default: app routes at `/`, `/brief`, `/evidence`, etc.
const basePath = process.env.NEXT_PUBLIC_BASE_PATH?.replace(/\/$/, "") || ""

const nextConfig: NextConfig = {
  reactStrictMode: true,
  ...(basePath ? { basePath } : {}),
  trailingSlash: false,
  images: {
    unoptimized: true,
  },
}

export default nextConfig
