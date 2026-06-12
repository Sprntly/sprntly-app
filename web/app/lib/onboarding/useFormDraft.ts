/**
 * Auto-persist onboarding form drafts to localStorage so switching browser
 * tabs or accidental navigation doesn't lose unsaved input.
 *
 * Usage:
 *   const { get, set, clear } = useFormDraft("ob-business-info")
 *   // On mount: restore fields from draft
 *   // On change: call set({ companyName, productName, ... })
 *   // On successful save: call clear()
 */

const PREFIX = "sprntly_ob_draft_"

export function saveDraft(step: string, data: Record<string, unknown>) {
  try {
    localStorage.setItem(PREFIX + step, JSON.stringify(data))
  } catch { /* quota / private mode */ }
}

export function loadDraft(step: string): Record<string, unknown> | null {
  try {
    const raw = localStorage.getItem(PREFIX + step)
    return raw ? JSON.parse(raw) : null
  } catch {
    return null
  }
}

export function clearDraft(step: string) {
  try {
    localStorage.removeItem(PREFIX + step)
  } catch { /* ignore */ }
}
