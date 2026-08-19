"use client"

import { useEffect, type DependencyList } from "react"
import { customArtifactsApi } from "../../../../lib/api"
import type { ContentPanelTab } from "../../../../context/NavigationContext"
import type { AppContentState } from "../../../../types/content"

/**
 * The shared "a thread that produced a DOCUMENT reopens on it" probe.
 *
 * A chat-written document was reachable ONLY while the panel stayed open:
 * `useThreadDocumentSync` (AppShell) re-attaches the pointer after a reload,
 * but nothing opens the panel and a document turn has no reply-footer button
 * the way a ticket set does. So the ack said "it will open in the panel on the
 * right", you reloaded, and the only route back was the Artifacts library. This
 * probe closes that: on load, once per target, surface the newest non-failed
 * document into the shared panel.
 *
 * The effect and its async core live here ONCE; every surface differs only in
 * the refs it reads and the precedence it keeps, which it hands in via `probe`:
 *
 *  - `begin()` runs the surface's pre-fetch guards against fresh refs and, if
 *    eligible, claims its once-per-target marker and returns the conversation id
 *    to list documents for (null bails). Main keys off the active tab; the
 *    project surface off its one conversation.
 *  - `stillActive()` / `panelOpen()` / `documentClaimed()` are the post-fetch
 *    guards read AFTER the list resolves — the target is still in front of the
 *    user, the panel is not already open, and a live generate has not already
 *    claimed the document pointer (the stale-read guard).
 *  - `ticketsWin?()` is main's OPTIONAL late-precedence arm: a ticket set
 *    `loadTicketSet` is still filling keeps the panel, because its ack promised
 *    Tickets and the document is one click away. The project surface has no such
 *    arm and omits it.
 *
 * It refuses, on every surface, the same things: claim the target BEFORE the
 * fetch (this effect re-runs on unchanged-but-new deps, and an unclaimed probe
 * would re-issue the request forever), never open over a target the user has
 * moved off, never fight an open panel, and never auto-open a FAILED document —
 * reopening a chat should not greet you with an error you already dismissed.
 * `generating` DOES open: that is the live state the panel exists to show.
 */
export type DocumentReopenProbe = {
  begin: () => number | null
  stillActive: () => boolean
  panelOpen: () => boolean
  documentClaimed: () => boolean
  ticketsWin?: () => boolean
  setContent: (patch: Partial<AppContentState>) => void
  openContentPanel: (tab: ContentPanelTab) => void
}

export function useDocumentReopenProbe(
  probe: DocumentReopenProbe,
  deps: DependencyList,
): void {
  useEffect(() => {
    const convId = probe.begin()
    if (convId == null) return
    void (async () => {
      try {
        const docs = await customArtifactsApi.listForConversation(convId).catch(() => [])
        if (!docs.length) return
        const newest = docs[0]
        // The user may have moved off this target during the round trip.
        if (!probe.stillActive()) return
        if (newest.status === "failed") return
        // Never fight a panel that is already open, and never overwrite the
        // document a live generate just wrote (the stale-read guard).
        if (probe.panelOpen()) return
        if (probe.documentClaimed()) return
        // A late-precedence arm may still claim the panel (main: tickets win).
        if (probe.ticketsWin?.()) return
        probe.setContent({ documentId: newest.id, documentGenerating: newest.status === "generating" })
        probe.openContentPanel("document")
      } catch {
        // A resume PROBE must never throw — it runs on every chat open and its
        // only job is to surface an artifact that may not exist.
      }
    })()
    // The deps array is the surface's own — its exact triggers and ref-read
    // order live at the call site, not here.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps)
}
