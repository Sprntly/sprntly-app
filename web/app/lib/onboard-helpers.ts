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
