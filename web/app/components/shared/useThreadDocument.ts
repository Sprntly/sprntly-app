"use client"

import { useEffect, useRef } from "react"
import { useContent } from "../../context/ContentContext"
import { customArtifactsApi } from "../../lib/api"

/**
 * Keeps the active thread's team document attached to the panel.
 *
 * `content.documentId` is set when the chat WRITES one, and lives only in
 * memory — so without this a document the thread genuinely owns disappears
 * from the panel the moment the page reloads. The row is still there; the tab
 * has simply forgotten it.
 *
 * CALLED ONCE, FROM AppShell, deliberately — the same shape and the same place
 * as `useThreadReportsSync`, which solves the identical problem for a thread's
 * captured reports.
 *
 * It also matters that it is NOT in ChatScreen. Putting it there made every one
 * of that component's 46 test files depend on an api surface their (partial)
 * mocks do not define, so an unrelated suite broke on an import it has no
 * business knowing about. A fetch that belongs to the shell should live in the
 * shell; the fact that the tests said so first is the useful part.
 */
export function useThreadDocumentSync() {
  const { content, setContent } = useContent()
  const conversationId = content.conversationId ?? null

  // `setContent` takes a patch rather than an updater, so the current value is
  // mirrored here for the async resolution below to read.
  const documentIdRef = useRef<number | null>(null)
  useEffect(() => { documentIdRef.current = content.documentId ?? null }, [content.documentId])

  useEffect(() => {
    if (conversationId == null) return
    let cancelled = false
    void customArtifactsApi
      .listForConversation(conversationId)
      .then((rows) => {
        // A generation started while this was in flight MUST win: it is the
        // document the user just asked for, and this is a stale read of the
        // same thread.
        if (cancelled || rows.length === 0 || documentIdRef.current != null) return
        setContent({ documentId: rows[0].id })
      })
      // Silent by design: a thread with no document is the normal case, and a
      // failed lookup must not put an error in front of someone who never
      // asked for one.
      .catch(() => {})
    return () => { cancelled = true }
  }, [conversationId, setContent])
}
