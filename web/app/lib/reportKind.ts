/**
 * Human labels for a report's KIND — the skill id that produced it
 * ("voice-of-customer-report" → "Voice of Customer").
 *
 * Shared by the artifacts row (badge sub-label) and the report viewer's header
 * so one report never reads as two different things on two surfaces.
 */

/** Ids whose humanised form reads badly, or that have an established name. */
const KIND_LABELS: Record<string, string> = {
  "voice-of-customer-report": "Voice of Customer",
  "public-feedback-report": "Public Feedback",
  "competitive-intelligence-review": "Competitive Intelligence",
}

/** Words to upper-case when humanising an unmapped id (mirrors the backend's
 *  catalog._ACRONYMS for the ones that can plausibly title a report). */
const ACRONYMS = new Set([
  "prd", "okr", "nct", "gtm", "saas", "cir", "jtbd", "pmf", "nps", "kpi",
  "ui", "ux", "ab", "rice", "ice",
])

/**
 * "voice-of-customer-report" → "Voice of Customer";
 * "saas-metrics-diagnosis"   → "SaaS metrics diagnosis" → via ACRONYMS: "SAAS metrics diagnosis";
 * an unknown id humanises rather than rendering a raw slug.
 *
 * A trailing "-report"/"-review" is dropped: the surface already labels the item
 * REPORT, so "Retention churn report report" never happens.
 */
export function reportKindLabel(skill: string | null | undefined): string {
  const id = (skill ?? "").trim()
  if (!id) return "Report"
  const mapped = KIND_LABELS[id]
  if (mapped) return mapped
  const words = id.replace(/-(report|review)$/, "").split("-").filter(Boolean)
  if (!words.length) return "Report"
  return words
    .map((w, i) => {
      if (ACRONYMS.has(w)) return w.toUpperCase()
      return i === 0 ? w.charAt(0).toUpperCase() + w.slice(1) : w
    })
    .join(" ")
}
