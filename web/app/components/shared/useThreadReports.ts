"use client"

import { useEffect, useRef } from "react"
import { useContent } from "../../context/ContentContext"
import { useNavigation } from "../../context/NavigationContext"
import { reportsApi, type ReportSummary } from "../../lib/api"

/** Long enough for capture (which runs right after the answer completes) to have
 *  landed, short enough that nobody reads the empty state as the answer. */
const RETRY_MS = 1500

/** How many times an EMPTY list is re-asked before it is believed.
 *
 *  Capture is a post-terminal side effect on the server: the ask job is marked
 *  `ready` and only THEN is the report written, so the poll that delivers the
 *  answer routinely beats the insert — measured at 2.2s on 2026-08-24
 *  (`GET /v1/reports?conversation_id=998` returned `[]` at 09:51:08.524; the row
 *  landed at 09:51:10.685). One retry was not enough for that gap. Three at
 *  `RETRY_MS` covers it, and an empty thread pays them only when something
 *  actually said a report was coming. */
const MAX_EMPTY_RETRIES = 3

/**
 * Last known reports per conversation, for the session.
 *
 * Switching back to a thread used to blank the list and re-fetch, so the panel
 * only slid in once the network answered — by which time the user had often
 * moved on, and the slide arrived over the tab they moved TO. Seeding from here
 * makes a revisit instant: the reports are already known, the panel opens on the
 * same commit as the tab switch, and the refetch below just confirms them.
 *
 * Module-level and unbounded on purpose: an entry is a handful of small rows per
 * conversation the user actually opened, and it dies with the page.
 */
const cache = new Map<number, ReportSummary[]>()

/**
 * Keeps the active thread's captured reports in ContentContext.
 *
 * Called ONCE, from AppShell — the same "one owner, mirrored into content"
 * shape as useBriefHydration. Two consumers need this list and neither should
 * fetch it: the panel (which tab to show, and the list itself) and ChatScreen
 * (whether a PRD-less thread should open its Reports tab). A second fetcher
 * would mean two requests and two answers that can disagree.
 *
 * It runs on the THREAD, not on the panel: opening a chat pulls its reports
 * whether or not the panel is open, so the Reports tab is already there when the
 * user looks for it instead of appearing a beat after they open the panel.
 *
 * Refetches when the reader lands on the Reports tab, because that is the one
 * moment staleness shows — a report asked for in this chat is captured
 * server-side after the answer, so the list held from thread-open time can be
 * one report behind. Same reason for the single delayed retry on an empty list:
 * "No reports in this chat", said about the report you just watched arrive, is
 * simply wrong.
 */
export function useThreadReportsSync() {
  const { content, setContent } = useContent()
  const { contentPanelTab } = useNavigation()
  const conversationId = content.conversationId
  const onReportsTab = contentPanelTab === "reports"
  // Bumped when a report answer lands in the open thread — capture is a
  // server-side step AFTER the answer, so nothing else here changes to say the
  // list just went stale. See `reportsRefreshKey` in types/content.ts.
  const refreshKey = content.reportsRefreshKey
  // The key value this hook has already chased. A run where it CHANGED is a run
  // that follows a report answer, and only that run may retry an empty list —
  // otherwise every ordinary thread-open on a report-less chat would pay three
  // pointless requests for the rest of the session.
  const chasedKeyRef = useRef<number | undefined>(undefined)

  useEffect(() => {
    if (conversationId == null) {
      // No thread → nothing to list. Clear, so a previous thread's reports can
      // never show against this one.
      setContent({
        threadReports: [],
        threadReportsStatus: "idle",
        threadReportsConversationId: null,
      })
      return
    }
    let cancelled = false
    let retry: ReturnType<typeof setTimeout> | undefined
    // A report answer just landed, and the row it produced may not be committed
    // yet (see MAX_EMPTY_RETRIES).
    const chasingAReport = refreshKey !== undefined && refreshKey !== chasedKeyRef.current
    chasedKeyRef.current = refreshKey
    // Seed from the last known answer for THIS thread so a revisit renders (and
    // the panel opens) immediately, with the refetch confirming underneath.
    // Status stays "ready" in that case: the list is real, just possibly a beat
    // stale — flipping to "loading" would hide the tab we already know belongs.
    const cached = cache.get(conversationId)
    // Every write stamps the conversation the rows describe. This effect lives in
    // AppShell (the PARENT), and React flushes child effects first, so on the
    // commit where ChatScreen switches tabs the content still holds the PREVIOUS
    // thread's rows. Readers compare the stamp against the active conversation
    // and treat a mismatch as "not landed yet" rather than as this thread's
    // answer — without it, a new chat auto-opened the panel on the old thread's
    // report.
    setContent(
      cached
        ? {
            threadReports: cached,
            threadReportsStatus: "ready",
            threadReportsConversationId: conversationId,
          }
        : {
            // Nothing known for this thread yet: blank the rows as well as the
            // status, so `threadReports` and its stamp NEVER disagree. Leaving
            // the previous thread's rows behind a "loading" flag meant any reader
            // that didn't check the status (the tab strip's "View report" button)
            // was still reading them.
            threadReports: [],
            threadReportsStatus: "loading",
            threadReportsConversationId: conversationId,
          },
    )

    const load = (attempt: number): Promise<void> =>
      reportsApi.listForConversation(conversationId)
        .then((rows) => {
          if (cancelled) return
          cache.set(conversationId, rows)
          setContent({
            threadReports: rows,
            threadReportsStatus: "ready",
            threadReportsConversationId: conversationId,
          })
          // "No reports in this chat", said about the report the user just
          // watched arrive, is simply wrong — so an empty list is re-asked while
          // there is reason to think one is on its way.
          if (rows.length === 0 && (onReportsTab || chasingAReport) && attempt < MAX_EMPTY_RETRIES) {
            retry = setTimeout(() => { void load(attempt + 1) }, RETRY_MS)
          }
        })
        .catch(() => {
          // An error is NOT an empty thread: the tab keeps whatever it holds and
          // says the load failed, rather than claiming this chat has no reports.
          if (!cancelled) setContent({ threadReportsStatus: "error" })
        })

    void load(0)
    return () => { cancelled = true; if (retry) clearTimeout(retry) }
  }, [conversationId, onReportsTab, refreshKey, setContent])
}
