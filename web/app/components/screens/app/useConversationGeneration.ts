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
import { followTicketSetSwitch, loadTicketSet, runTicketSetGeneration } from "../../../lib/runTicketSetGeneration"
import { customArtifactsApi, type AskResponse, type ChatIntentEnvelope, type OpenArtifactCandidate, type OpenArtifactResult } from "../../../lib/api"
import type { ChatPersistence } from "../../../lib/chatPersistence"
import type { TicketSetFailureKind } from "../../../types/content"
import type { AppContentState } from "../../../types/content"
import type { ContentPanelTab } from "../../../context/NavigationContext"
import type { ConversationHandle } from "./conversationCore"
import type { ThreadTurn } from "./ChatScreen"

type PersistedAttachment = { name: string; content: string; key?: string | null; mime?: string | null; size?: number | null }

// Named artifact kinds that don't render in the shared panel — the open flow
// says where they DO live instead of substituting the wrong document.
const UNSUPPORTED_OPEN_KIND: Record<string, string> = {
  prototype: "A prototype",
  report: "A report",
  tickets: "Tickets",
}

const TICKET_SET_ACK =
  "Writing tickets for that — they'll open in the panel on the right when ready. " +
  "Use the View Tickets button in this chat to reopen them anytime."

// Toast copy per failure KIND — the kind is all the runner returns, so the words
// live here (a toast has one line; the panel has its own SET_ERROR_COPY).
const TICKET_SET_FAILURE_TOAST: Record<TicketSetFailureKind, string> = {
  timeout: "That run is taking longer than expected. It may still finish — reopen this chat in a few minutes.",
  network: "The connection dropped while the tickets were being written. Try again.",
  notfound: "Those tickets are no longer available.",
  failed: "The tickets couldn't be written from this conversation. Try again with more specifics.",
}

export interface UseConversationGenerationDeps {
  /** Place a fully-formed settled command turn into the conversation + persist.
   *  Main: the tab-orchestrator `emitCommandTurn` (active-or-new tab); a project
   *  slot: single-conversation append + server-only persist. Injected seam. */
  emitTurn: (turn: ThreadTurn) => void
  /** Mint the handle onto a conversation by key (main: `makeTabHandle`). */
  makeHandle: (key: string) => ConversationHandle
  /** Seed a settled command-acknowledgement turn and resolve its conversation.
   *  Main: the tab-orchestrator `seedGenerationTurn` (reuse active-or-spawn tab,
   *  rename, clear composer, persist); a project slot: single-conversation
   *  append + server-only persist. Returns the resolved key + its bound DB id. */
  seedGenerationTurn: (seedTurn: ThreadTurn) => { tabId: string; dbConvId: number | null }
  /** The conversation's turn/answer history as grounding context for a document
   *  generation. Main reads the tab's thread; a project slot its own. */
  threadContextFor: (key: string) => string
  /** The create-once persistence spine (shared with the turn store) — used to
   *  ensure the conversation row exists before an artifact is attached to it. */
  persistence: ChatPersistence
  /** Open a resolved artifact in its destination. Main: the shared side-panel
   *  (via openArtifactDestination → tab reuse/spawn); a project slot: the
   *  artifacts MODAL (the sanctioned per-surface destination divergence). Returns
   *  false when the candidate has no usable id. */
  openArtifactInPanel: (candidate: OpenArtifactCandidate, seedQuery?: string) => boolean
  /** Post an assistant turn that opens NOTHING (the ambiguous / not-found /
   *  can't-open halves of the open-artifact contract) — surface-specific tab
   *  seeding, injected like `emitTurn`. */
  postOpenArtifactReply: (seedQuery: string, answer: string, candidates: OpenArtifactCandidate[]) => void
  /** Mark this conversation's ticket-set as auto-opened, so main's thread-resume
   *  probe doesn't double-read the row. Main-tab coordination; a project slot,
   *  which has no such probe, provides a no-op (an ABSENT concept, not a
   *  re-derivation of generation). */
  markTicketSetAutoOpened: (key: string) => void
  /** Post the agent-only artifact-summary turn (main: via its summary poster).
   *  Optional-chained in main, so a no-op is a valid injection. `kind` matches
   *  the host poster's artifact-kind union. */
  postSummary: (key: string, kind: "prd" | "evidence" | "prototype" | "ticket_set", artifactId: number) => void
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
  /** Tenant/dataset scope — passed to `customArtifactsApi.generate` so the
   *  backend can ground a THIN-context document (a fresh "generate a report
   *  on X" with no prior thread to draw on) on a real retrieval-backed answer
   *  instead of writing one that honestly says it has nothing to say. See
   *  custom_artifact_generate.py's `_ground_thin_context`. */
  activeCompany: string
}

export function useConversationGeneration({
  emitTurn,
  makeHandle,
  seedGenerationTurn,
  threadContextFor,
  persistence,
  pushPendingConversation,
  finalizeConversationTurn,
  setContent,
  openContentPanel,
  content,
  showToast,
  openArtifactInPanel,
  postOpenArtifactReply,
  markTicketSetAutoOpened,
  postSummary,
  activeCompany,
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

  // Write a team document from this chat and open it in the right panel. Mirrors
  // the ticket-set flow: seed a settled acknowledgement (via the injected seam),
  // then start the work — so the exchange survives a reload and reads like every
  // other command.
  const documentCommandFlow = useCallback((
    seedQuery: string,
    envelope: ChatIntentEnvelope,
  ) => {
    const turnId =
      typeof crypto !== "undefined" && crypto.randomUUID ? crypto.randomUUID() : `turn-${Date.now()}`
    const kind = envelope.artifact_kind?.trim() || "document"
    const ack: AskResponse = {
      answer: `Writing your ${kind} — it will open in the panel on the right.`,
      sources: [], follow_ups: [], key_points: [], citations: [], confidence: 1, unanswered: "",
    } as AskResponse
    const seedTurn: ThreadTurn = { id: turnId, query: seedQuery, reply: ack }
    // Seed via the surface's seam; `dbConvId` is the tab's bound conversation
    // (null on a fresh tab, mirroring the old inline `convId = inTab.dbConvId`).
    const { tabId, dbConvId } = seedGenerationTurn(seedTurn)

    void (async () => {
      try {
        // THE CONVERSATION HAS TO EXIST BEFORE THE DOCUMENT DOES. On a tab's
        // first message `dbConvId` is null (the create was fired, not awaited),
        // so a document stored then would orphan from its thread. `ensureConversation`
        // shares that very in-flight create (create-once), and uses the SAME
        // title `seedGenerationTurn` persisted (49-char truncation) so a create
        // race can't rename the row.
        const attachTo = dbConvId ?? await persistence.ensureConversation(tabId, {
          turnId,
          title: seedQuery.length > 52 ? `${seedQuery.slice(0, 49)}…` : seedQuery,
          query: seedQuery,
        })
        const created = await customArtifactsApi.generate({
          kind,
          // THE GROUNDING — without it the generator writes a skeleton listing
          // what it doesn't know, for a subject discussed in the thread above.
          task: envelope.task?.trim() || seedQuery,
          context: threadContextFor(tabId),
          conversation_id: attachTo,
          // Lets the backend fall back to a real retrieval-backed answer when
          // the thread context above is thin (a fresh ask with nothing prior
          // to draw on) — see the `dataset` doc on customArtifactsApi.generate.
          dataset: activeCompany,
        })
        // NEVER OPEN THIS CONVERSATION'S DOCUMENT OVER SOMEONE ELSE'S: the
        // round trips mean the user may have moved on, and this pair is
        // unconditional. The document is attached to its conversation, so
        // returning re-opens it through useThreadDocumentSync.
        if (!makeHandle(tabId).isActive()) return
        setContent({ documentId: created.id, documentGenerating: true })
        openContentPanel("document")
      } catch {
        showToast(
          "Couldn't start that document",
          "Please try again, or create one from Artifacts.",
        )
      }
    })()
  }, [seedGenerationTurn, makeHandle, setContent, openContentPanel, showToast, threadContextFor, persistence, activeCompany])

  // A document attached to a "make a PRD" command is the chat entry to the
  // PRD-IMPORT flow: upload the file to POST /v1/prd/import — the same conversion
  // the Artifacts "Upload PRD" button uses (parse to text, faithful re-layout
  // into our PRD format) — then stream the imported PRD into the shared panel.
  // This is the PANEL counterpart of main's tab-based `importPrdCommandFlow`:
  // same shared primitives (`prdApi.importDoc` + `resumePrdGeneration`), driven
  // through the injected content-panel seam so the artifact lands wherever the
  // surface's panel lives.
  //
  // OPTIMISTIC-FIRST, like every other command: the ack turn + panel spinner
  // render on THIS commit (via the seam); the import POST + poll run AFTER, in
  // the async block — never awaited before the first render, so a big deck can't
  // clear the composer and leave the chat blank for the multi-second call.
  // `openTickets` ("convert this doc into tickets") kicks the user-stories job
  // the moment the PRD is ready and lands the panel on the Tickets tab.
  const importDocCommandFlow = useCallback((
    file: File,
    opts: {
      /** The company slug the PRD belongs to (importDoc's `dataset`). */
      company: string
      openTickets: boolean
      seedQuery: string
      /** The uploaded format the user named. Easy to miss on THIS path and the
       *  most important to get right: attaching a file to "create a PRD using our
       *  Acme format" dispatches an IMPORT, so a format dropped here is a document
       *  silently written in a different one. */
      artifactTemplateId?: string | null
    },
  ) => {
    const turnId =
      typeof crypto !== "undefined" && crypto.randomUUID ? crypto.randomUUID() : `turn-${Date.now()}`
    // The exact acks main's `commandAckReply` produces for the import kind, so the
    // thread reads identically on both surfaces.
    const ack: AskResponse = {
      answer: opts.openTickets
        ? "Importing your document as a PRD — it'll open in the panel on the right, and I'll break it into tickets as soon as it's ready."
        : "Importing your document as a PRD — it'll open in the panel on the right when ready.",
      sources: [], follow_ups: [], key_points: [], citations: [], confidence: 1, unanswered: "",
    } as AskResponse
    const seedTurn: ThreadTurn = { id: turnId, query: opts.seedQuery, reply: ack }
    // Seed via the surface's seam; `dbConvId` is the conversation's bound row
    // (null on a fresh one, mirroring the inline path's `convId = inTab.dbConvId`).
    const { tabId, dbConvId } = seedGenerationTurn(seedTurn)
    const conv = makeHandle(tabId)
    conv.patchMeta({ prdGenerating: true })
    if (conv.isActive()) {
      setContent({ prd: null, prdGenerating: true, prdPartialHtml: null })
      openContentPanel("prd")
    }

    void (async () => {
      // Stream the re-laid-out draft as it renders — only while this conversation
      // still owns the panel.
      const onPartial = (html: string) => {
        if (makeHandle(tabId).isActive()) setContent({ prdPartialHtml: html })
      }
      try {
        const { prdApi, storiesApi } = await import("../../../lib/api")
        const { resumePrdGeneration } = await import("../../../lib/runPrdGeneration")
        // THE CONVERSATION HAS TO EXIST BEFORE THE PRD DOES. On a fresh
        // conversation `dbConvId` is null (the create was fired, not awaited);
        // passing the id to importDoc binds the chat to the PRD server-side, so
        // leaving the page mid-import can't orphan it (the same bind
        // generateFromTask does). `ensureConversation` shares that in-flight
        // create (create-once) and reuses the SAME title `seedGenerationTurn`
        // persisted (49-char truncation) so a create race can't rename the row.
        const attachTo = dbConvId ?? await persistence.ensureConversation(tabId, {
          turnId,
          title: opts.seedQuery.length > 52 ? `${opts.seedQuery.slice(0, 49)}…` : opts.seedQuery,
          query: opts.seedQuery,
        })
        const start = await prdApi.importDoc(file, opts.company, attachTo, opts.artifactTemplateId)
        // Stamp the now-known prd_id immediately so a reload past this point can
        // resume the run and the View PRD affordance has something to open.
        conv.patchMeta({ prdId: start.prd_id })
        const result = await resumePrdGeneration(start.prd_id, undefined, onPartial)
        // NEVER OPEN THIS CONVERSATION'S PRD OVER ANOTHER ONE: the round trips
        // mean the user may have moved on.
        if (!makeHandle(tabId).isActive()) return
        if (result.ok) {
          conv.patchMeta({ prd: result.prd, prdId: result.prd.prd_id, prdGenerating: false })
          setContent({ prd: result.prd, prdGenerating: false, prdPartialHtml: null })
          // "convert this doc into tickets": the user asked for TICKETS — kick the
          // user-stories generation NOW (fire-and-forget; the backend dedups
          // in-flight jobs) so work starts before the Tickets tab even mounts,
          // then land the panel on Tickets.
          if (opts.openTickets) {
            void storiesApi.generate(result.prd.prd_id).catch(() => {})
            openContentPanel("tickets")
          }
          // The thread's record of what got built — the agent-only summary turn
          // (a no-op on a surface with no poster).
          postSummary(tabId, "prd", result.prd.prd_id)
        } else {
          conv.patchMeta({ prdGenerating: false })
          setContent({ prdGenerating: false, prdPartialHtml: null })
          showToast("PRD unavailable", result.message.slice(0, 200))
        }
      } catch {
        conv.patchMeta({ prdGenerating: false })
        if (makeHandle(tabId).isActive()) setContent({ prdGenerating: false, prdPartialHtml: null })
        showToast("Couldn't import that document", "Please try again, or import one from Artifacts.")
      }
    })()
  }, [seedGenerationTurn, makeHandle, persistence, setContent, openContentPanel, showToast, postSummary])

  // The whole open_artifact dispatch: 1 match opens (in the surface's
  // destination), 2+ ask, 0 says so — and a kind this panel can't show says
  // where it DOES live. The two destinations (`openArtifactInPanel` /
  // `postOpenArtifactReply`) are surface-divergent by design and injected.
  const openArtifactFlow = useCallback(
    (seedQuery: string, open: OpenArtifactResult) => {
      const noun = open.artifact_type === "evidence" ? "evidence" : "PRD"
      if (open.status === "unsupported_type") {
        postOpenArtifactReply(
          seedQuery,
          `${UNSUPPORTED_OPEN_KIND[open.artifact_type] ?? "That kind of artifact"} doesn't open in this panel — you'll find it in the Artifacts tab. I can open a PRD or its evidence here.`,
          [],
        )
        return
      }
      if (open.status === "resolved" && open.artifact) {
        if (openArtifactInPanel(open.artifact, seedQuery)) return
        // A match we cannot actually open (no usable id) is a NOT-FOUND from
        // the user's side; saying so beats opening an empty panel.
        postOpenArtifactReply(
          seedQuery,
          `I found "${open.artifact.title}" but couldn't open it — try it from the Artifacts tab.`,
          [],
        )
        return
      }
      if (open.status === "ambiguous") {
        postOpenArtifactReply(
          seedQuery,
          `There's more than one ${noun} matching "${open.query}". Which one did you mean?`,
          open.candidates,
        )
        return
      }
      // not_found. Deliberately does NOT offer to generate one: the user asked
      // to open something, and turning that into a generation is the exact
      // failure this action exists to prevent.
      postOpenArtifactReply(
        seedQuery,
        `I couldn't find a ${noun} for "${open.query}". Nothing was opened — check the Artifacts tab, or tell me to generate one if you'd like it written.`,
        [],
      )
    },
    [openArtifactInPanel, postOpenArtifactReply],
  )

  // ── Standalone ticket sets ──────────────────────────────────────────────────
  // The runner: mark generating, ensure the conversation exists (create-once —
  // a ticket_sets row has no back-patch route), open the panel on the same
  // commit, generate, then stamp the result + post the summary. Never yanks the
  // panel from another conversation.
  const startTicketSetRun = useCallback((
    tabId: string,
    task: string,
    seed: { turnId: string; title: string; query: string } | null,
    artifactTemplateId?: string | null,
  ) => {
    const conv = makeHandle(tabId)
    conv.patchMeta({ ticketSetRunning: true, ticketSetStatus: "generating", ticketSetTask: task })
    // This IS the conversation's ticket-set open, so main's thread-resume probe
    // must not also fire and put a second reader on the same row.
    markTicketSetAutoOpened(tabId)
    void (async () => {
      const convId = conv.dbConvId() ?? (seed ? await persistence.ensureConversation(tabId, seed) : null)
      if (conv.isActive()) openContentPanel("tickets")
      const result = await runTicketSetGeneration(task, convId ?? null, setContent, artifactTemplateId)
      if (result.ok) {
        conv.patchMeta({ ticketSetRunning: false, ticketSetId: result.set.id, ticketSetStatus: "ready" })
        // The thread's record of what got built — the agent-only summary turn.
        postSummary(tabId, "ticket_set", result.set.id)
        return
      }
      conv.patchMeta({ ticketSetRunning: false, ticketSetStatus: "failed" })
      showToast("Tickets unavailable", TICKET_SET_FAILURE_TOAST[result.kind])
    })()
  }, [makeHandle, markTicketSetAutoOpened, persistence, openContentPanel, setContent, postSummary, showToast])

  // The chat's "generate tickets" command on a conversation with no PRD.
  // Optimistic-first: the ack turn (seeded via the seam) renders on this commit,
  // every network call happens after it.
  const ticketSetCommandFlow = useCallback((
    seedQuery: string,
    task: string,
    /** The uploaded TICKET format the user named, off the intent envelope. */
    artifactTemplateId?: string | null,
  ) => {
    const turnId =
      typeof crypto !== "undefined" && crypto.randomUUID ? crypto.randomUUID() : `turn-${Date.now()}`
    const ack: AskResponse = {
      answer: TICKET_SET_ACK,
      sources: [], follow_ups: [], key_points: [], citations: [], confidence: 1, unanswered: "",
    } as AskResponse
    const seedTurn: ThreadTurn = { id: turnId, query: seedQuery, reply: ack }
    const { tabId } = seedGenerationTurn(seedTurn)
    startTicketSetRun(tabId, task, {
      turnId,
      title: seedQuery.length > 52 ? `${seedQuery.slice(0, 49)}…` : seedQuery,
      query: seedQuery,
    }, artifactTemplateId)
  }, [seedGenerationTurn, startTicketSetRun])

  // The reply-footer button: reopen a finished set, or re-run a failed one.
  const handleTicketSetAction = useCallback(async (tabId: string) => {
    const conv = makeHandle(tabId)
    const tab = conv.getMeta()
    if (!tab) return
    if (tab.ticketSetStatus === "failed") {
      // Re-run from the ORIGINAL request. In-session that is on the tab; after
      // a reload it is read back off the row (`source_text`).
      let task = tab.ticketSetTask?.trim() || content.ticketSet?.sourceText?.trim() || ""
      if (!task && tab.ticketSetId != null) {
        const { ticketSetsApi } = await import("../../../lib/api")
        task = await ticketSetsApi.get(tab.ticketSetId)
          .then((r) => r.source_text?.trim() ?? "")
          .catch(() => "")
      }
      if (!task) {
        showToast(
          "Ask again in the chat",
          "The original request isn't available any more — say what to break into tickets and I'll re-run it.",
        )
        return
      }
      startTicketSetRun(tabId, task, null)
      return
    }
    if (tab.ticketSetId == null) return
    // Always re-read the set rather than trusting shared content: the panel is
    // global, and opening a PRD in the meantime clears the slice.
    setContent({ ticketSetStandalone: false })
    openContentPanel("tickets")
    void loadTicketSet(tab.ticketSetId, setContent)
  }, [makeHandle, content.ticketSet, setContent, openContentPanel, showToast, startTicketSetRun])

  return {
    listArtifactsFlow,
    prdChangeTemplateFlow,
    ticketsChangeTemplateFlow,
    documentCommandFlow,
    importDocCommandFlow,
    openArtifactFlow,
    ticketSetCommandFlow,
    handleTicketSetAction,
  }
}
