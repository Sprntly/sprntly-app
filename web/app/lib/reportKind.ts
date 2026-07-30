/**
 * Human labels for a report's KIND — the skill id that produced it
 * ("voice-of-customer-report" → "Voice of Customer").
 *
 * Shared by the artifacts row (badge sub-label) and the report viewer's header
 * so one report never reads as two different things on two surfaces.
 */

/** Longest title we keep — mirrors report_capture._TITLE_MAX so the chat card
 *  and the stored artifact never disagree about where a title was cut. */
const TITLE_MAX = 200

const TAG_RE = /<[^>]+>/g

/**
 * The report's own title, read out of its HTML document.
 *
 * This MIRRORS the backend's `report_capture.report_title` — `<title>` first,
 * then the first `<h1>`, then the humanised kind — and the order is the whole
 * point, not an incidental detail: a report's stored title is what the artifacts
 * row and the panel's list show, and the chat card resolves WHICH report a turn
 * is by matching against it. Reading the `<h1>` first produced "Voice of Customer
 * Report" where the stored row said "Voice of Customer Report — 30 July 2026 · 1
 * day", so no card ever matched its own report and every click landed on the
 * list. Keep the two in step.
 */
export function reportTitleFromHtml(
  html: string | null | undefined,
  skill?: string | null,
): string {
  const doc = html ?? ""
  for (const re of [/<title[^>]*>([\s\S]*?)<\/title>/i, /<h1[^>]*>([\s\S]*?)<\/h1>/i]) {
    const text = re.exec(doc)?.[1]
    if (!text) continue
    // Entity decoding is the browser's job here (the chat card renders as text,
    // not HTML), so strip tags, collapse whitespace, and take what's left.
    const clean = text.replace(TAG_RE, " ").replace(/\s+/g, " ").trim()
    if (clean) return clean.slice(0, TITLE_MAX)
  }
  return reportKindLabel(skill)
}

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
