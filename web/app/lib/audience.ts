/** Host-based audience: production app vs demo/marketing bundle. */
export type Audience = "app" | "demo"

export function inferAudience(): Audience {
  if (typeof window === "undefined") return "demo"
  return window.location.hostname.startsWith("app.") ? "app" : "demo"
}

export function isAppAudience(): boolean {
  return inferAudience() === "app"
}
