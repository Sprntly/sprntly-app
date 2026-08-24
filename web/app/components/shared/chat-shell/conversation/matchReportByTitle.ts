import { type ReportSummary } from "../../../../lib/api"

/** Find the report a chat turn is about, from its title.
 *
 *  Title is the join key because the reply the thread holds carries no report
 *  id: capture runs after the ask completes, deliberately (it must never delay
 *  the answer), so the id doesn't exist yet when the reply is stored. Both sides
 *  derive the title from the document's own <h1> — the client via
 *  reportTitleFromDoc, the server via report_capture.report_title — so they
 *  agree by construction.
 *
 *  Matched exactly first, then leniently (case/whitespace, then either side
 *  being a prefix of the other) — a title that drifts by a dash or a truncation
 *  should still open the right document rather than dumping the reader on a
 *  list, which is the failure this whole path exists to avoid. No match means
 *  capture hasn't landed yet (or the row is gone); the caller opens the panel
 *  and lets it show what it has. */
export function matchReportByTitle(
  reports: readonly ReportSummary[],
  title: string,
): ReportSummary | undefined {
  const norm = (s: string) => s.trim().toLowerCase().replace(/\s+/g, " ")
  const want = norm(title)
  return (
    reports.find((r) => r.title === title) ??
    reports.find((r) => norm(r.title) === want) ??
    reports.find((r) => {
      const have = norm(r.title)
      return have.length > 0 && (have.startsWith(want) || want.startsWith(have))
    })
  )
}
