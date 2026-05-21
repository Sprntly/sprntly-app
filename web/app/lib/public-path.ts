/**
 * Paths for links when the app is hosted under a subpath (e.g. /demo).
 * Set NEXT_PUBLIC_BASE_PATH=/demo in production CI — see deploy-frontend.yml.
 */
export function getBasePath(): string {
  const raw = process.env.NEXT_PUBLIC_BASE_PATH ?? ""
  if (!raw) return ""
  return raw.startsWith("/") ? raw.replace(/\/$/, "") : `/${raw.replace(/\/$/, "")}`
}

/** publicPath("/terms") → "/demo/terms" when base path is set. */
export function publicPath(path: string): string {
  const base = getBasePath()
  const normalized = path.startsWith("/") ? path : `/${path}`
  if (!base) return normalized
  return `${base}${normalized}`
}

/** Absolute URL for Google OAuth / legal forms. */
export function publicAbsoluteUrl(path: string): string {
  const site =
    process.env.NEXT_PUBLIC_SITE_URL?.replace(/\/$/, "") ||
    process.env.NEXT_PUBLIC_API_URL?.replace(/\/$/, "") ||
    "https://api.sprntly.ai"
  return `${site}${publicPath(path)}`
}
