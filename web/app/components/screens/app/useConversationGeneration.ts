"use client"

/**
 * The per-conversation ARTIFACT-GENERATION flows, extracted verbatim from
 * `ChatScreen`: the chat-driven commands that produce or re-shape a PRD /
 * evidence / ticket set / document and drive the shared content panel.
 *
 * These are surface-agnostic by construction: each flow writes its turns through
 * the `ConversationHandle`, its per-conversation artifact metadata through the
 * handle's `patchMeta`, and the (single, app-global) content panel through the
 * INJECTED content-panel seam — so the same flow drives main and, later, a
 * project slot. The seam is injected, NOT re-derived: main passes its real
 * `ContentContext` `setContent`/`openContentPanel` + its `emitTurn`
 * (tab-orchestrator) exactly as before; a project slot passes the SAME global
 * content panel + its own single-conversation `emitTurn` at wiring time.
 *
 * Genuinely tab-orchestrator concerns (`openTab`, tab-switch artifact sync) stay
 * in the host and are injected where a flow needs them.
 */

import { useCallback } from "react"
import { runListArtifactsAction } from "../../shared/chat-shell/conversation/actions"
import type { ChatIntentEnvelope } from "../../../lib/api"
import type { ThreadTurn } from "./ChatScreen"

export interface UseConversationGenerationDeps {
  /** Place a fully-formed settled command turn into the conversation + persist.
   *  Main: the tab-orchestrator `emitCommandTurn` (active-or-new tab); a project
   *  slot: single-conversation append + server-only persist. Injected seam. */
  emitTurn: (turn: ThreadTurn) => void
}

export function useConversationGeneration({ emitTurn }: UseConversationGenerationDeps) {
  // "What are my PRDs?" — the rows rode the envelope; render them as clickable
  // cards on a turn (empty included: "none yet" is the listing's own honest
  // answer, not a fall-through). Runs the SHARED list-artifacts action, config'd
  // with the surface's emitTurn.
  const listArtifactsFlow = useCallback((seedQuery: string, envelope: ChatIntentEnvelope) => {
    runListArtifactsAction(seedQuery, envelope, { emitTurn })
  }, [emitTurn])

  return { listArtifactsFlow }
}
