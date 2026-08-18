"use client"

/**
 * The per-conversation ARTIFACT-GENERATION flows, extracted verbatim from
 * `ChatScreen`: the chat-driven commands that produce or re-shape a PRD /
 * evidence / ticket set / document and drive the shared content panel.
 *
 * These are surface-agnostic by construction: each flow writes its turns through
 * the `ConversationHandle` (`patchTurns` / `setBusy`), its per-conversation
 * artifact metadata through `patchMeta`, gates its panel writes on `isActive()`,
 * and drives the (single, app-global) content panel through the INJECTED
 * content-panel seam — so the same flow drives main and, later, a project slot.
 * The seams are injected, NOT re-derived: main passes its real `ContentContext`
 * `setContent`/`openContentPanel` + its tab-orchestrator `emitTurn`
 * (`emitCommandTurn`) exactly as before; a project slot passes the SAME global
 * content panel + its own single-conversation `emitTurn` at wiring time.
 *
 * Genuinely tab-orchestrator concerns (`openTab`, tab-switch artifact sync) stay
 * in the host and are injected where a flow needs them.
 */

import { useCallback } from "react"
import { runListArtifactsAction } from "../../shared/chat-shell/conversation/actions"
import { clearPrdDrafts } from "../../shared/PrdInputQuestions"
import { followTicketSetSwitch, loadTicketSet } from "../../../lib/runTicketSetGeneration"
import type { AskResponse, ChatIntentEnvelope } from "../../../lib/api"
import type { AppContentState } from "../../../context/ContentContext"
import type { ContentPanelTab } from "../../../context/NavigationContext"
import type { ConversationHandle } from "./conversationCore"
import type { ThreadTurn } from "./ChatScreen"

type PersistedAttachment = { name: string; content: string; key?: string | null; mime?: string | null; size?: number | null }

export interface UseConversationGenerationDeps {
  /** Place a fully-formed settled command turn into the conversation + persist.
   *  Main: the tab-orchestrator `emitCommandTurn` (active-or-new tab); a project
   *  slot: single-conversation append + server-only persist. Injected seam. */
  emitTurn: (turn: ThreadTurn) => void
  /** Mint the handle onto a conversation by key (main: `makeTabHandle`). */
  makeHandle: (key: string) => ConversationHandle
  /** Seed the optimistic pending-conversation rail entry + fire the create. */
  pushPendingConversation: (
    turnId: string,
    query: string,
    key: string,
    attachments?: PersistedAttachment[],
  ) => void
  /** Settle a turn's reply/error into persistence. */
  finalizeConversationTurn: (
    turnId: string,
    updates: { reply?: AskResponse; error?: string },
    key: string,
  ) => Promise<void>
  // ── The shared content-panel seam (a single app-global panel) ──────────────
  setContent: (patch: Partial<AppContentState>) => void
  openContentPanel: (tab: ContentPanelTab) => void
  /** The live content-panel state (read for the open ticket-set slice). */
  content: AppContentState
  showToast: (title: string, sub: string, link?: string, opts?: { onAction?: () => void; persist?: boolean }) => void
}

export function useConversationGeneration({
  emitTurn,
  makeHandle,
  pushPendingConversation,
  finalizeConversationTurn,
  setContent,
  openContentPanel,
  content,
  showToast,
}: UseConversationGenerationDeps) {
  // "What are my PRDs?" — the rows rode the envelope; render them as clickable
  // cards on a turn (empty included: "none yet" is the listing's own honest
  // answer, not a fall-through). Runs the SHARED list-artifacts action, config'd
  // with the surface's emitTurn.
  const listArtifactsFlow = useCallback((seedQuery: string, envelope: ChatIntentEnvelope) => {
    runListArtifactsAction(seedQuery, envelope, { emitTurn })
  }, [emitTurn])

  // "Change the template to Acme" on a PRD tab: dispatch the in-place format
  // switch (POST /v1/prd/{id}/change-template) and acknowledge in the thread —
  // the ack posts on dispatch, like the ticket-set ack, because the re-write
  // renders live in the panel and the thread's job is to say what started and
  // where to look. The regeneration's OUTCOME lands as a toast (the same pair
  // the panel's own Format control shows), read from the row's stamp: a failed
  // regeneration is restored to `ready` with its content intact and its OLD
  // stamp — unchanged stamp, unchanged document.
  const prdChangeTemplateFlow = useCallback(async (
    query: string, targetTabId: string, prdId: number,
    templateId: string, templateName: string | null,
  ) => {
    const conv = makeHandle(targetTabId)
    const id =
      typeof crypto !== "undefined" && crypto.randomUUID ? crypto.randomUUID() : `turn-${Date.now()}`
    conv.patchTurns((thread) => [...thread, { id, query }])
    conv.setBusy(true)
    pushPendingConversation(id, query, targetTabId)
    const finalize = (reply: AskResponse) => {
      conv.patchTurns((thread) => thread.map((tn) => (tn.id === id ? { ...tn, reply } : tn)))
      finalizeConversationTurn(id, { reply }, targetTabId)
    }
    const label = templateName ? `“${templateName}”` : "that format"
    let res: { status: "ready" | "generating"; unchanged?: boolean; artifact_template_id: string | null }
    try {
      const { prdApi } = await import("../../../lib/api")
      res = await prdApi.changeTemplate(prdId, templateId)
    } catch (e) {
      const msg = e instanceof Error ? e.message : "something went wrong"
      finalize({
        answer: `I couldn't switch the format — ${msg}. The PRD is unchanged, and its version history is intact.`,
        sources: [], follow_ups: [], key_points: [], citations: [], confidence: 1, unanswered: "",
      } as AskResponse)
      conv.setBusy(false)
      return
    }
    if (res.unchanged) {
      finalize({
        answer: `This PRD is already written in ${label} — nothing to change.`,
        sources: [], follow_ups: [], key_points: [], citations: [], confidence: 1, unanswered: "",
      } as AskResponse)
      conv.setBusy(false)
      return
    }
    finalize({
      answer: `Switching this PRD to ${label} — re-writing it into that structure now. It'll re-render in the panel on the right, and the previous version is saved in Version history.`,
      sources: [], follow_ups: [], key_points: [], citations: [], confidence: 1, unanswered: "",
    } as AskResponse)
    // The turn is answered; the thread stays usable while the re-write runs.
    conv.setBusy(false)

    // Drive the panel exactly like a first generation: stale drafts cleared (a
    // local draft must not overwrite the re-laid-out document), the tab and
    // panel flip to generating, and the poll + SSE stream render it live.
    clearPrdDrafts(prdId)
    conv.patchMeta({ prd: null, prdGenerating: true })
    if (conv.isActive()) {
      setContent({ prd: null, prdGenerating: true, prdPartialHtml: null })
      openContentPanel("prd")
    }
    try {
      const { resumePrdGeneration, loadPrdById } = await import("../../../lib/runPrdGeneration")
      const result = await resumePrdGeneration(prdId, undefined, (html) => {
        if (conv.isActive()) setContent({ prdPartialHtml: html })
      })
      const prd = result.ok
        ? result.prd
        // Timeout/hiccup: the backend preserved the document — reload it so the
        // panel shows the honest state, never a blank pane.
        : await loadPrdById(prdId).then((r) => (r.ok ? r.prd : null)).catch(() => null)
      conv.patchMeta({ prd, prdGenerating: false })
      if (conv.isActive()) {
        setContent({ prd, prdGenerating: false, prdPartialHtml: null })
      }
      if (result.ok && (result.prd.artifactTemplateId ?? null) === templateId) {
        showToast("Format switched", `This PRD is now written in ${templateName || "the new format"}.`)
      } else {
        showToast("Couldn't switch the format", "The PRD is unchanged — its content and version history are intact. Try again in a moment.")
      }
    } catch {
      showToast("Couldn't switch the format", "The PRD is unchanged — its content and version history are intact. Try again in a moment.")
    }
  }, [makeHandle, pushPendingConversation, finalizeConversationTurn, setContent, openContentPanel, showToast])

  // ── Change the TICKETS' format from chat ────────────────────────────────────
  // "Change the ticket template to Acme". The tickets counterpart of
  // prdChangeTemplateFlow: the backend re-LAYS the existing tickets (identity,
  // edits and tracker links preserved — never a regeneration) in the
  // BACKGROUND, so the POST returns as soon as the switch is scheduled and the
  // reply says it is under way rather than done. `target` is the thread's
  // standalone set when it has one, else the tab PRD's persisted tickets —
  // resolved by the caller, because the backend cannot see a set from a
  // prd_id-shaped envelope.
  const ticketsChangeTemplateFlow = useCallback(async (
    query: string, targetTabId: string,
    target: { ticketSetId: number } | { prdId: number },
    templateId: string, templateName: string | null,
  ) => {
    const conv = makeHandle(targetTabId)
    const id =
      typeof crypto !== "undefined" && crypto.randomUUID ? crypto.randomUUID() : `turn-${Date.now()}`
    conv.patchTurns((thread) => [...thread, { id, query }])
    conv.setBusy(true)
    pushPendingConversation(id, query, targetTabId)
    const finalize = (reply: AskResponse) => {
      conv.patchTurns((thread) => thread.map((tn) => (tn.id === id ? { ...tn, reply } : tn)))
      finalizeConversationTurn(id, { reply }, targetTabId)
    }
    const asReply = (answer: string) => ({
      answer, sources: [], follow_ups: [], key_points: [], citations: [], confidence: 1, unanswered: "",
    } as AskResponse)
    const label = templateName ? `“${templateName}”` : "that format"
    try {
      const { storiesApi } = await import("../../../lib/api")
      const res = await storiesApi.changeTemplate(target, templateId)
      if (res.unchanged) {
        finalize(asReply(`These tickets are already written in ${label} — nothing to change.`))
        return
      }
      finalize(asReply(
        `Re-laying the tickets into ${label} now — every ticket keeps its content, edits and tracker links; only the description layout changes. It carries on in the background, so you can keep working; they'll update in the panel on the right when it lands.`,
      ))
      // Follow the switch so the panel lands on the new format by itself. A
      // standalone set is followed through its one owner, which marks the
      // slice `relaying` in place — never `loadTicketSet`, which would blank
      // tickets that are still perfectly readable. A PRD's tickets need no
      // call at all: the Tickets tab's own poll is watching the row and owns
      // both the re-read and the completion toast.
      if (conv.isActive()) {
        if ("ticketSetId" in target) {
          const slice = content.ticketSet
          if (slice && slice.id === target.ticketSetId) {
            void followTicketSetSwitch(
              target.ticketSetId, setContent, slice, templateName,
            ).then((landed) => {
              if (landed) {
                showToast("Format switched",
                  `These tickets now use ${templateName || "the new format"}.`)
              }
            })
          } else {
            void loadTicketSet(target.ticketSetId, setContent)
          }
        }
        openContentPanel("tickets")
      }
      showToast(
        "Switching format",
        `Re-laying these tickets into ${templateName || "the new format"}.`,
      )
    } catch (e) {
      const msg = e instanceof Error ? e.message : "something went wrong"
      finalize(asReply(`I couldn't switch the ticket format — ${msg}. The tickets are unchanged.`))
    } finally {
      conv.setBusy(false)
    }
  }, [makeHandle, pushPendingConversation, finalizeConversationTurn, setContent, openContentPanel,
      showToast, content.ticketSet])

  return { listArtifactsFlow, prdChangeTemplateFlow, ticketsChangeTemplateFlow }
}
