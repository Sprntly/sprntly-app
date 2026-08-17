"use client"

// ── ProjectPrivateChat — "My chat with Sprntly" (private, per project) ──
//
// The thin host that renders the private project thread through the shared
// `ChatShell`. It owns NO chat machinery of its own: `useProjectPrivateThread`
// owns where turns come from and go (conversation binding, history, realtime,
// classify → dispatch → ask, clarify-pick), and `ChatShell` owns what the user
// sees and touches (the frame, the transcript, the composer, scroll,
// esc-to-stop). This host only supplies the descriptor — the per-turn render
// closures (markdown user body, the show-more agent body, the delegation
// footer, the insight banner) and the composer/frame config — and forwards the
// engine's turns + pick callback into the shell (spec §2.5).
//
// AD-P13a (never fork the monolith): this host imports no chat-monolith
// container; the project-genuine dispatch primitive (`dispatchChatIntent`)
// lives in the engine hook.
import ReactMarkdown from "react-markdown"
import remarkGfm from "remark-gfm"
import { ChatShell } from "../../../shared/chat-shell/ChatShell"
import { useChatComposerController, renderRunStatus } from "../../../shared/chatComposerController"
import type { ChatSurfaceDescriptor, ShellTurn } from "../../../shared/chat-shell/types"
import shellCss from "../../../shared/chat-shell/ChatShell.module.css"
import { AssistantThinkingSkeleton } from "../../../shared/AssistantThinkingSkeleton"
import { artifactItemAsCandidate } from "./artifactCandidates"
import { AGENT_BADGE, AGENT_NAME } from "../../../../lib/agent"
import type { ChatArtifactItem, DelegationLedgerRow, OpenArtifactCandidate } from "../../../../lib/api"
import { DelegationActions } from "./DelegationActions"
import { useProjectPrivateThread } from "./useProjectPrivateThread"
import extras from "./project-chat-extras.module.css"

const COMPOSER_PLACEHOLDER = "Message Sprntly…"

export type ProjectPrivateChatProps = {
  projectId: number | string
  /** Opens the artifacts modal on a specific candidate. `OpenArtifactChips`
   *  renders zero chips today (no candidate source on a plain `AskResponse`),
   *  composed anyway so wiring real candidates later is additive. */
  onOpenArtifact?: (candidate: OpenArtifactCandidate) => void
  /** The cross-chat INSIGHT turn — a note surfaced from the group chat or
   *  another member's individual chat. `source_kind` picks the copy;
   *  omitted/`null` renders a kind-neutral note. */
  insightNote?: { by: string; text: string; source_kind?: "group" | "individual" | null } | null
  /** #9-count artifact invalidation: called after a client-driven generate
   *  (`runGeneratePrd`/`runGenerateTickets`) settles its own `addArtifact` —
   *  refreshes the host's artifacts list + count immediately, without
   *  waiting on the realtime `artifact.added` echo. */
  onArtifactsChanged?: () => void
}

/** The insight banner's location phrase — derived from the ACTUAL source
 *  conversation kind (never assumed "group chat"). Neutral when unresolved. */
function insightSourcePhrase(sourceKind: "group" | "individual" | null | undefined): string {
  if (sourceKind === "group") return "noted this in the group chat"
  if (sourceKind === "individual") return "noted this in a chat with Sprntly"
  return "noted this"
}

/** True for a turn that came from persisted history (vs. the current session)
 *  — the testid suffix (`-history-*` vs `-msg-*`) and body treatment split on
 *  it. The engine mints history ids as `history-<id>`. */
function isHistoryTurn(turn: ShellTurn): boolean {
  return turn.id.startsWith("history-")
}

export function ProjectPrivateChat({ projectId, onOpenArtifact, insightNote, onArtifactsChanged }: ProjectPrivateChatProps) {
  const engine = useProjectPrivateThread(projectId, { onArtifactsChanged })
  // The shared composer controller (un-stubs the project composer). Private
  // rides `/v1/ask`, so BOTH attachments and skills go live: the built
  // `SendCommand` (splice + extracted attachment context) hands to `engine.send`.
  const composerCtl = useChatComposerController({
    scope: { surface: "project_private", projectId: Number(projectId) },
    onCommand: engine.send,
    attachmentsEnabled: true,
    skillsEnabled: true,
  })

  const markdownUserBody = (turn: ShellTurn) => (
    <div data-testid={isHistoryTurn(turn) ? "ic-history-you" : "ic-msg-you"}>
      <ReactMarkdown remarkPlugins={[remarkGfm]}>{turn.content ?? ""}</ReactMarkdown>
    </div>
  )

  const delegationActionsFor = (turn: ShellTurn) => {
    const delegation = turn.footerData as DelegationLedgerRow | null | undefined
    if (!delegation) return null
    return (
      <div className={extras.delegationActions} data-testid="ic-brief-delegation-actions">
        <DelegationActions
          delegationId={delegation.delegation_id}
          status={delegation.status}
          viewerParty="assignee"
          onEmit={(event, note) => engine.emitDelegation(delegation.delegation_id, event, note)}
          compact
        />
      </div>
    )
  }

  const leadingNode = (
    <>
      {insightNote ? (
        <div className="bc-turn bc-turn--insight" data-testid="cross-chat-insight">
          <span className="bc-insight-msg-kind">INSIGHT</span>
          <span>
            <b>{insightNote.by}</b> {insightSourcePhrase(insightNote.source_kind)}: {insightNote.text}
          </span>
        </div>
      ) : null}
      {engine.resuming ? (
        <div data-testid="ic-resuming">
          <AssistantThinkingSkeleton phase="Picking up where you left off…" />
        </div>
      ) : null}
    </>
  )

  const trailingNode =
    !engine.resuming && engine.turns.length === 0 ? (
      <div data-testid="individual-chat-empty">
        Ask Sprntly anything about this project — it already knows what the team has covered.
      </div>
    ) : null

  const descriptor: ChatSurfaceDescriptor = {
    surface: "project_private",
    projectId: Number(projectId),
    testIdPrefix: "ic",
    frame: { mode: "thread", viewportClassName: shellCss.standaloneViewport },
    transcript: {
      agentName: AGENT_NAME,
      agentBadge: AGENT_BADGE,
      timestamps: "fromTurn",
      userHead: "named",
      renderUserBody: markdownUserBody,
      // NO `renderAgentBody` override: private agent turns now render through
      // `ChatBubble`'s native reply ladder (the shared consume-not-reimplement
      // path group already uses), fed by the engine's `ShellTurn` state +
      // `reply`/`openCandidates`/`artifactList`. Open-destinations route to
      // this project's artifacts modal, same contract as group.
      onOpenCandidate: (c: OpenArtifactCandidate) => onOpenArtifact?.(c),
      onOpenArtifactItem: (item: ChatArtifactItem) => onOpenArtifact?.(artifactItemAsCandidate(item)),
      turnFooter: delegationActionsFor,
      leading: leadingNode,
      trailing: trailingNode,
    },
    composer: {
      placeholder: COMPOSER_PLACEHOLDER,
      busyMode: "block-while-asking",
      stop: { enabled: true, onStop: engine.stop },
      escToStop: true,
      voice: "default",
      attachments: true,
      features: composerCtl.features,
      slashMenu: composerCtl.slashMenu,
      onKeyDownCapture: composerCtl.onKeyDownCapture,
    },
    reply: {
      mode: "streamed",
      // The FE agent run-status consume. Dark until the backend feeds real
      // `ShellTurn.runStatus` (undefined → nothing); private has no retry seam
      // (it re-asks via the turn), so no Retry is offered.
      runStatus: (status, turn) => renderRunStatus({ status, turn, prefix: "ic" }),
      // The confirmation gate's confirm/cancel seams — the shell's mapped
      // confirm card calls back here with (turnId, token).
      onConfirmMutation: engine.confirmMutation,
      onCancelMutation: engine.cancelMutation,
    },
    send: { onSubmit: composerCtl.submit, pendingSendBubble: true },
  }

  return (
    <ChatShell
      key="project_private"
      descriptor={descriptor}
      turns={engine.turns}
      onPickOption={engine.pickOption}
      onClarifySubmit={engine.submitClarify}
      onClarifySkip={engine.skipClarify}
    />
  )
}
