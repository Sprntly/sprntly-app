/**
 * Pure helpers shared by the onboarding wizard, extracted for unit testing.
 */

/** Mirror of backend slug rules: lowercase a-z0-9, _ or -, length 2-63. */
export function suggestedSlug(name: string): string {
  return name
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9_-]+/g, "_")
    .replace(/_+/g, "_")
    .replace(/^_|_$/g, "")
    .slice(0, 63)
}

/**
 * Generate a unique, opaque company slug. The slug is decoupled from the
 * company name: it's a random token, not name-derived, so it ALWAYS satisfies
 * the backend `companies_slug_format` CHECK (^[a-z0-9][a-z0-9_-]{1,62}$,
 * 2-63 chars, must start alphanumeric) regardless of what the user types.
 *
 * Shape: a leading letter ("c") + 11 random chars from [a-z0-9] = 12 chars,
 * which is well within 2-63 and starts alphanumeric by construction. The
 * random body uses a crypto-strong RNG for collision resistance, and varies
 * on every call. On a UNIQUE collision the caller simply regenerates.
 */
export function generateSlug(): string {
  const ALPHABET = "abcdefghijklmnopqrstuvwxyz0123456789" // [a-z0-9]
  const BODY_LEN = 11
  const bytes = new Uint8Array(BODY_LEN)
  randomBytesInto(bytes)
  let body = ""
  for (let i = 0; i < BODY_LEN; i++) {
    body += ALPHABET[bytes[i] % ALPHABET.length]
  }
  // Leading letter guarantees the first char is alphanumeric (a-z).
  return "c" + body
}

/**
 * Fill `out` with crypto-strong random bytes via the Web Crypto API
 * (`crypto.getRandomValues`), available in browsers, Edge, and Node 20+.
 */
function randomBytesInto(out: Uint8Array): void {
  globalThis.crypto.getRandomValues(out)
}

/** Strip duplicate uploads keyed by name+size (a user re-dropping the same file). */
export function dedupeFiles(files: File[]): File[] {
  const seen = new Set<string>()
  return files.filter((f) => {
    const k = `${f.name}::${f.size}`
    if (seen.has(k)) return false
    seen.add(k)
    return true
  })
}

/**
 * Onboarding generation has no real per-stage status from the backend yet, so
 * we surface a believable progression driven by elapsed time. Tuned to the
 * observed shape of brief generation (one LLM call ~20-60s with bursty start).
 *
 * - Reading sources    : 0–15s  (corpus load is fast, LLM hasn't returned yet)
 * - Drafting insights  : 15–60s (the bulk of the LLM call)
 * - Polishing          : 60s+   (final tokens + DB write; or just "almost there")
 */
export type GenStage = "reading" | "drafting" | "polishing"

export const GEN_STAGES: { id: GenStage; label: string; description: string }[] = [
  { id: "reading", label: "Reading sources", description: "Parsing your uploads into the LLM corpus." },
  { id: "drafting", label: "Drafting insights", description: "Claude is identifying 3–5 actionable findings." },
  { id: "polishing", label: "Polishing", description: "Saving the brief and warming the evidence pages." },
]

export function stageForElapsed(elapsedMs: number): GenStage {
  if (elapsedMs < 15_000) return "reading"
  if (elapsedMs < 60_000) return "drafting"
  return "polishing"
}

/**
 * Rough progress 0..1 for a fill bar. Asymptotic — never reaches 1.0 until
 * the backend says "ready", so the bar can't "lie" past 95%.
 */
export function progressForElapsed(elapsedMs: number): number {
  // 95% at 90s; geometric falloff after, capped at 0.97.
  const halfLifeMs = 30_000
  const p = 1 - Math.pow(0.5, elapsedMs / halfLifeMs)
  return Math.min(0.97, p)
}
