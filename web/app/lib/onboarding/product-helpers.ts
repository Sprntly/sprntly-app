/** Normalize optional product website to https URL or null. */
export function normalizeProductWebsite(raw: string): string | null {
  const trimmed = raw.trim()
  if (!trimmed) return null
  try {
    const parsed = new URL(/^https?:\/\//i.test(trimmed) ? trimmed : `https://${trimmed}`)
    if (parsed.protocol !== "http:" && parsed.protocol !== "https:") return null
    return parsed.href.replace(/\/$/, "")
  } catch {
    return null
  }
}

export function validateProductWebsite(raw: string): string | null {
  if (!raw.trim()) return null
  if (!normalizeProductWebsite(raw)) {
    return "Enter a valid website URL (e.g. https://acme.com)."
  }
  return null
}
