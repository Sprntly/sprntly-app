import type { NextConfig } from "next"
import { withSentryConfig } from "@sentry/nextjs"

// This app is a static export (`output: "export"` below) — there is NO
// server/edge runtime, so we deliberately ship no server instrumentation file.
// Silence Sentry's build-time warning that looks for one (it's the documented
// escape hatch for client-only setups). Must be set before withSentryConfig runs.
process.env.SENTRY_SUPPRESS_INSTRUMENTATION_FILE_WARNING = "1"

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
  experimental: {
    // Rewrite `import { IconX } from "@tabler/icons-react"` to per-icon deep
    // imports at compile time — keeps the ~5k-icon barrel out of dev graphs
    // and client bundles.
    optimizePackageImports: ["@tabler/icons-react"],
  },
}

// Only wrap with Sentry when a DSN is configured, so default local/CI builds
// stay unchanged (no source-map plugin, no client-config injection). Source-map
// upload additionally requires SENTRY_AUTH_TOKEN + SENTRY_ORG + SENTRY_PROJECT
// at build time — without a token the plugin just injects the client config and
// skips the upload. This app is a static export, so no server/edge
// instrumentation runs; only sentry.client.config.ts takes effect.
const sentryEnabled = Boolean(process.env.NEXT_PUBLIC_SENTRY_DSN)

export default sentryEnabled
  ? withSentryConfig(nextConfig, {
      org: process.env.SENTRY_ORG,
      project: process.env.SENTRY_PROJECT,
      authToken: process.env.SENTRY_AUTH_TOKEN,
      // Quiet unless running in CI.
      silent: !process.env.CI,
      // Broaden source-map association for chunks served from the static export.
      widenClientFileUpload: true,
    })
  : nextConfig
