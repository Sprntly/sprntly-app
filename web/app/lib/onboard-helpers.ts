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
