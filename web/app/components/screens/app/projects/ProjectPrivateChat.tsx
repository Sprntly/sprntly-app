"use client"

// ── ProjectPrivateChat — "My chat with Sprntly" (private, per project) ──
//
// The private project chat now runs the SHARED chat interface: the single-
// conversation engine (`useConversation`) drives the SAME presentation main uses
// (`ConversationView` → `ChatShell surface:"main"`). This host only builds the
// private `SurfaceAdapter` (the non-visual per-surface seam — identity, server-
// only persistence, history, ask grounding, suggestions) and its composer node.
//
// Deleted with the old `useProjectPrivateThread` engine (all confirmed dropped):
// the delegation footer, the cross-chat insight banner, markdown-in-user-bubble,
// the empty-state placeholder, and private's realtime lane. Command actions
// (PRD/ticket generation, edit-PRD, clarify-to-generate) are DEFERRED — the
// adapter's `dispatchIntent` is a no-op for now, so every send is a grounded ask.
import { useCallback, useMemo, useRef, useState } from "react"
import { ChatComposer } from "../../../shared/ChatComposer"
import { ConversationView } from "../ConversationView"
import { useConversation } from "../../../shared/chat-shell/conversation/useConversation"
import type { SurfaceAdapter } from "../../../shared/chat-shell/conversation/types"
import { useChatComposerController } from "../../../shared/chatComposerController"
import shellCss from "../../../shared/chat-shell/ChatShell.module.css"
import { artifactItemAsCandidate } from "./artifactCandidates"
import type { ThreadTurn } from "../ChatScreen"
import {
  chatIntentApi,
  chatSuggestionsApi,
  projectsApi,
  type AskResponse,
  type ChatArtifactItem,
  type IndividualTurn,
  type OpenArtifactCandidate,
} from "../../../../lib/api"
import {
  runEditPrdAction,
  runListArtifactsAction,
  type ActionConfig,
} from "../../../shared/chat-shell/conversation/actions"
import type { ConversationActionContext } from "../../../shared/chat-shell/conversation/types"
import type { ChatPersistence } from "../../../../lib/chatPersistence"
import { useCompany } from "../../../../context/CompanyContext"
import { useWorkspace } from "../../../../context/WorkspaceContext"
import { useAuth } from "../../../../lib/auth"

const COMPOSER_PLACEHOLDER = "Message Sprntly…"

export type ProjectPrivateChatProps = {
  projectId: number | string
  /** Opens the artifacts modal/drawer on a specific candidate. */
  onOpenArtifact?: (candidate: OpenArtifactCandidate) => void
  /** DEFERRED (dropped with the old engine): the cross-chat insight banner. Kept
   *  in the prop type so callers are unchanged; unused until re-added as shared. */
  insightNote?: { by: string; text: string; source_kind?: "group" | "individual" | null } | null
  /** DEFERRED: fired after a client-driven generate settles. Unused while command
   *  actions are stubbed. */
  onArtifactsChanged?: () => void
  /** DEFERRED: the open-PRD edit target. Unused until edit-PRD is wired. */
  openPrdId?: number | null
}

/** Pair the flat server history (alternating user / assistant rows) into the
 *  canonical `ThreadTurn` model the shared transcript renders: a user row opens a
 *  turn, the next assistant row fills its `reply`. A lone assistant row (a
 *  delivered brief with no user question) renders as its own agent turn. */
function pairHistory(rows: IndividualTurn[]): ThreadTurn[] {
  const out: ThreadTurn[] = []
  for (const h of rows) {
    if (h.role === "user") {
      out.push({ id: `history-${h.id}`, query: h.content })
    } else {
      const last = out[out.length - 1]
      if (last && last.reply == null && last.query) {
        last.reply = { answer: h.content } as AskResponse
      } else {
        out.push({ id: `history-${h.id}`, query: "", reply: { answer: h.content } as AskResponse })
      }
    }
  }
  return out
}

export function ProjectPrivateChat({ projectId, onOpenArtifact, openPrdId, onArtifactsChanged }: ProjectPrivateChatProps) {
  const { activeCompany } = useCompany()
  const { profile } = useWorkspace()
  const auth = useAuth()
  const callerEmail = auth.kind === "authed" ? (auth.user.email ?? null) : null

  const callerName = useMemo(() => {
    const full = [profile?.first_name, profile?.last_name].map((s) => s?.trim()).filter(Boolean).join(" ")
    if (full) return full
    if (callerEmail) {
      const local = callerEmail.split("@")[0]
      if (local) return local
    }
    return null
  }, [profile?.first_name, profile?.last_name, callerEmail])
  const callerFirstName = callerName?.split(/\s+/)[0] ?? ""
  const callerInitials = callerName
    ? callerName.split(/\s+/).slice(0, 2).map((w: string) => w[0]?.toUpperCase() ?? "").join("")
    : ""

  // Cache the resolved conversation id — bound lazily on first send (opening the
  // chat must not create a row).
  const convIdRef = useRef<number | null>(null)
  const resolveConvId = useCallback(async (): Promise<number> => {
    if (convIdRef.current != null) return convIdRef.current
    const c = await projectsApi.individualChat(projectId)
    convIdRef.current = c.id
    return c.id
  }, [projectId])

  // Server-only persistence: the `/v1/ask` route persists the user+assistant
  // pair server-side (keyed by client_message_id), so the client turn writers are
  // no-ops; only conversation binding is real.
  const persistence: ChatPersistence = useMemo(
    () => ({
      pushUserTurn: async () => {},
      pushAssistantTurn: async () => {},
      resolveConvId: () => resolveConvId(),
      ensureConversation: () => resolveConvId().catch(() => null),
    }),
    [resolveConvId],
  )

  // Private's ActionConfig for the shared action layer: render into the engine's
  // turns (via the engine-provided ctx) + persist server-only, plus the open-PRD
  // context and the drawer refresh. The action bodies read these; they never
  // learn the surface.
  const buildPrivateActionConfig = useCallback(
    (ctx: ConversationActionContext, prdId: number | null): ActionConfig => {
      const persist = (id: string, question: string, answer: string) =>
        void projectsApi
          .persistIndividualTurns(projectId, { clientMessageId: id, question, answer })
          .catch(() => {})
      return {
        emitTurn: (turn) => {
          ctx.emitTurn(turn)
          if (turn.reply) persist(turn.id, turn.query, (turn.reply as AskResponse).answer ?? "")
        },
        runActionTurn: async (q, w) => {
          const { turnId, reply } = await ctx.runActionTurn(q, w)
          persist(turnId, q, reply.answer ?? "")
        },
        contextIds: { prdId },
        // Refresh the artifacts list/count after an edit. (The OPEN drawer's own
        // content refresh is a follow-up — there is no prop to push the fresh
        // record into the drawer yet; the turn honestly reports what changed.)
        onArtifactUpdated: () => onArtifactsChanged?.(),
      }
    },
    [projectId, onArtifactsChanged],
  )

  const adapter: SurfaceAdapter = useMemo(
    () => ({
      identity: {
        surface: "project_private",
        projectId: Number(projectId),
        userName: callerFirstName,
        userInitials: callerInitials,
        company: activeCompany,
        conversationKey: `individual-${projectId}`,
      },
      persistence,
      loadHistory: () => projectsApi.individualTurns(projectId).then(pairHistory),
      askParams: { project_id: Number(projectId) },
      suggestions: {
        fetchSuggestions: (conversationId, opts) =>
          chatSuggestionsApi.next(conversationId, opts).then((r) => r.suggestions),
      },
      // Command-intent dispatch: resolve the intent and run the SHARED action
      // layer config'd for this surface. list_artifacts + edit_prd are migrated
      // (the same shared actions main runs); every other command is still
      // DEFERRED and falls through to a grounded ask.
      dispatchIntent: async (draft, ctx) => {
        const envelope = await chatIntentApi
          .resolve(draft, { conversationId: convIdRef.current })
          .catch(() => null)
        if (!envelope) return false

        if (envelope.intent === "list_artifacts" && Array.isArray(envelope.artifact_list)) {
          runListArtifactsAction(draft, envelope, buildPrivateActionConfig(ctx, null))
          return true
        }

        if (envelope.intent === "edit_prd") {
          // Edit the PRD open in this chat's drawer (parity with main's open-tab
          // PRD). No open PRD → nothing to edit; fall through to a grounded ask.
          const prdId = envelope.prd_id ?? openPrdId ?? null
          if (prdId != null && envelope.instruction) {
            await runEditPrdAction(envelope.instruction, buildPrivateActionConfig(ctx, prdId))
            return true
          }
          return false
        }

        return false
      },
    }),
    [projectId, callerFirstName, callerInitials, activeCompany, persistence, openPrdId, onArtifactsChanged, buildPrivateActionConfig],
  )

  const engine = useConversation(adapter)

  // The shared composer controller (attachments + skills live, private rides
  // `/v1/ask`). Its normalized `SendCommand` hands text + resolved attachments +
  // idempotency key to the engine.
  const composerCtl = useChatComposerController({
    scope: { surface: "project_private", projectId: Number(projectId) },
    onCommand: (cmd) =>
      engine.submit(cmd.text, { attachments: cmd.attachments, clientMessageId: cmd.clientMessageId }),
    attachmentsEnabled: true,
    skillsEnabled: true,
  })

  const composerNode = (
    <PrivateComposer
      composerCtl={composerCtl}
      busy={engine.busy}
      onStop={engine.stop}
      placeholder={COMPOSER_PLACEHOLDER}
    />
  )

  return (
    <ConversationView
      engine={engine}
      adapter={adapter}
      composerNode={composerNode}
      onSubmit={(draft) => engine.submit(draft)}
      onOpenArtifact={onOpenArtifact}
      onOpenArtifactItem={(item: ChatArtifactItem) => onOpenArtifact?.(artifactItemAsCandidate(item))}
      viewportClassName={shellCss.standaloneViewport}
      testIdPrefix="ic"
    />
  )
}

/** Private's composer node for the shared `surface:"main"` frame — it owns its
 *  own draft (the shell owns the draft only on the legacy `!isMain` path) and
 *  wires the shared `ChatComposer` to the controller's feature bag, slash palette,
 *  and normalized send. */
function PrivateComposer({
  composerCtl,
  busy,
  onStop,
  placeholder,
}: {
  composerCtl: ReturnType<typeof useChatComposerController>
  busy: boolean
  onStop: () => void
  placeholder: string
}) {
  const [draft, setDraft] = useState("")
  const composerRef = useRef<HTMLTextAreaElement>(null)
  const localFileRef = useRef<HTMLInputElement>(null)
  const noop = () => {}

  const doSubmit = () => {
    const hasAttachments = (composerCtl.features?.attachments.length ?? 0) > 0
    if (!draft.trim() && !hasAttachments) return
    composerCtl.submit(draft)
    setDraft("")
  }

  return (
    <ChatComposer
      busy={busy}
      draft={draft}
      pinnedSkill={composerCtl.features?.pinnedSkill ?? null}
      attachments={composerCtl.features?.attachments ?? []}
      hint={null}
      menuOpen={composerCtl.features?.menuOpen ?? false}
      menuActiveIndex={composerCtl.features?.menuActiveIndex ?? 0}
      slashMenu={composerCtl.slashMenu}
      composerRef={composerRef}
      fileInputRef={composerCtl.features?.fileInputRef ?? localFileRef}
      onInput={(e) => {
        setDraft(e.target.value)
        composerCtl.onInput(e.target.value)
      }}
      onKeyDown={(e) => {
        if (composerCtl.onKeyDownCapture(e.nativeEvent)) return
        if (e.key === "Enter" && !e.shiftKey) {
          e.preventDefault()
          doSubmit()
        }
      }}
      onSend={doSubmit}
      onStop={onStop}
      onToggleMenu={composerCtl.features?.onToggleMenu ?? noop}
      onMenuActive={composerCtl.features?.onMenuActive ?? noop}
      onMenuSelect={composerCtl.features?.onMenuSelect ?? noop}
      onCloseMenu={composerCtl.features?.onCloseMenu ?? noop}
      onRemoveAttachment={composerCtl.features?.onRemoveAttachment ?? noop}
      onRemoveSkill={composerCtl.features?.onRemoveSkill ?? noop}
      onFileSelect={composerCtl.features?.onFileSelect ?? noop}
      placeholder={placeholder}
    />
  )
}
