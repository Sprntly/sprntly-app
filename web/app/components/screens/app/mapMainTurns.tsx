"use client"

/**
 * `mapMainTurns` — the verbatim extraction of main chat's turn-state → transcript
 * mapping (formerly inline in ChatScreen's render). It is NOT pure JSX: it
 * MUTATES `deps.animatedTurnIds.current` during the render pass (the typing-
 * animation dedup) and reads render-unstable refs. Extracting it as a function
 * with an explicit dependency bag keeps that behaviour intact ONLY because the
 * function stays called synchronously from ChatScreen's own render — it is never
 * memoized, deferred, or moved into the shell. The shell receives the finished
 * `ChatTranscriptTurn[]`.
 *
 * Every free identifier the original block read arrives through `deps`
 * (destructured below) except module-level values it imports directly
 * (`AGENT_NAME`) and the two module-level presentational components it renders
 * (`ChatArtifactActions` / `ChatTicketSetActions`), imported from their shared
 * `chat-shell/` home so the main mapper and any future consumer share one copy.
 */

import { AGENT_NAME } from "../../../lib/agent"
import { QUOTE_VIEWER_NAME, splitQuotedSuffix } from "../../../lib/chatQuote"
import type { ChatTranscriptTurn } from "../../shared/ChatTranscript"
import type { MapMainTurnsDeps } from "../../shared/chat-shell/types"
import { SlackShareMessage } from "../../shared/SlackSharePreviewCard"
import { ChatArtifactActions, ChatTicketSetActions } from "../../shared/chat-shell/ChatArtifactActions"
import { turnAfterNode } from "../../shared/chat-shell/turnAfterNode"
import type { PlanDecision } from "../../shared/GoalAnalysisPlan"
import { type ThreadTurn } from "./ChatScreen"

export function mapMainTurns(thread: ThreadTurn[], deps: MapMainTurnsDeps): ChatTranscriptTurn[] {
  const {
    animatedTurnIds,
    askStartRef,
    resumedTurnsRef,
    lastLiveTurnIdx,
    busy,
    activeTab,
    name,
    userInitials,
    skillForQuery,
    ticketSetActionState,
    showInsightMsg,
    chatEvidenceExists,
    chatPrdExists,
    chatPrdCtaWaiting,
    chatProtoPrdId,
    chatPrototypeReady,
    inlinePrdCards,
    inlinePrdAnchorIdx,
    insightCardNode,
    prdQuestionsNode,
    clarifyPopupOpen,
    pendingClarifyTurn,
    handleAskAgain,
    handleStopAsk,
    submitClarifyAnswers,
    goalGateBusyTurnId,
    confirmGoalDefinition,
    approveGoalPlan,
    setViewerAttachment,
    editingTurnId,
    copiedTurnId,
    onCopyTurn,
    onRetryTurn,
    onEditTurn,
    onSubmitTurnEdit,
    onCancelTurnEdit,
    openReportByTitle,
    openArtifactInPanel,
    openChatArtifactItem,
    handleTicketSetAction,
    handleOpenEvidence,
    handleOpenPrd,
    handleViewPrototype,
    onSendSlackShare,
    onCancelSlackShare,
    onPickSlackShareTarget,
    handlePrototypeSettled,
    renderUserBody,
    renderAgentBody,
  } = deps

  return thread.map((turn, idx): ChatTranscriptTurn => {
    // "Last" for the purposes of in-flight state and the artifact-action row
    // means the last turn a REPLY could still land on — a pending artifact-
    // summary placeholder is transparent to both.
    const isLast = idx === lastLiveTurnIdx
    // A turn shows the "thinking" skeleton ONLY while its ask is genuinely in
    // flight — the active tab is busy AND this is the last (in-flight) turn.
    const isGenerating =
      isLast &&
      (busy || !!activeTab?.prdGenerating || !!activeTab?.prdCommandThinking)
    const hasFreshReply = !!turn.reply && !animatedTurnIds.current.has(turn.id)
    if (hasFreshReply) animatedTurnIds.current.add(turn.id)
    // Wait-state signals for this turn. Every one is an observable fact, not an
    // inference.
    const waitSkill = skillForQuery(turn.query)
    const waitStartedAt = askStartRef.current.get(turn.id)
    const waitResumed = resumedTurnsRef.current.has(turn.id)

    // Artifact-action row (Generate/View PRD + prototype) — ONLY on a PRD-bound
    // tab whose insight card isn't showing yet, OR the STANDALONE-set row on a
    // chat with no PRD — never both, and only on the last turn once it has a
    // reply.
    const footer =
      isLast && turn.reply && activeTab?.prdId == null && ticketSetActionState ? (
        <ChatTicketSetActions
          state={ticketSetActionState}
          onClick={() => { void handleTicketSetAction(activeTab!.id) }}
        />
      ) : isLast && turn.reply && !showInsightMsg && activeTab?.prdId != null ? (
        <ChatArtifactActions
          evidenceExists={chatEvidenceExists}
          prdExists={chatPrdExists}
          prdWaiting={chatPrdCtaWaiting}
          prdGenerating={!!activeTab?.prdGenerating}
          prdLoading={!!activeTab?.prdLoading}
          onViewEvidence={handleOpenEvidence}
          onOpenPrd={handleOpenPrd}
          prototypePrdId={chatProtoPrdId}
          prototypeReady={chatPrototypeReady}
          onViewPrototype={handleViewPrototype}
          onPrototypeSettled={handlePrototypeSettled}
        />
      ) : null

    // IN-CHAT COMMAND open: the insight/PRD card + clarifying questions render
    // as the reply BELOW the command turn — `inlinePrdAnchorIdx` resolves which
    // turn that is — instead of being pinned above the whole conversation.
    // The share preview card rides its own turn, under the reply. It stays
    // mounted after it settles — as the record of what was (or wasn't) posted —
    // because a card that vanished would leave the thread's prose ("here's what
    // I'll post") as the last word on a message that may already be in a team
    // channel. `questionInPopup` keeps the picker out of it: which channel and
    // which document are asked in the dock's QuestionPopup, like every other
    // choice this product makes.
    const shareNode = turn.slackShare && onSendSlackShare && onCancelSlackShare ? (
      <SlackShareMessage
        key={`share-${turn.id}`}
        preview={turn.slackShare.preview}
        busy={turn.slackShare.busy}
        resolved={turn.slackShare.resolved}
        onSend={(channelId, note) => onSendSlackShare(turn.id, channelId, note)}
        onCancel={() => onCancelSlackShare(turn.id)}
        onPickTarget={
          onPickSlackShareTarget
            ? (target) => onPickSlackShareTarget(turn.id, target)
            : undefined
        }
        questionInPopup
      />
    ) : null

    // The inline insight/PRD-card placement is the shared `turnAfterNode`
    // service; main injects its host-local card nodes + the per-turn share node
    // as the adapter, so the composed after-node is byte-identical.
    const afterNode = turnAfterNode(turn, idx, {
      insightCardNode,
      prdQuestionsNode,
      inlinePrdCards,
      inlinePrdAnchorIdx,
      extra: shareNode,
    })

    // The passage this message was a reply to, lifted out of the stored query
    // so it renders as a quote block above the bubble instead of as literal
    // "> " text inside it. A turn that carries none is unaffected — every user
    // turn ever written before quoting existed goes down this path.
    const { body: queryBody, quote } = splitQuotedSuffix(turn.query)

    // ── What can be done to this past prompt ──────────────────────────────
    // Copy is free: it changes nothing, so any turn the user actually spoke
    // offers it, answered or not.
    const canCopyTurn = !!onCopyTurn && !!queryBody

    // Edit and retry both RE-ASK, which rewinds the thread to this point —
    // everything below is replaced by the new answer. That is the Claude
    // behaviour and it is what makes editing a past prompt coherent rather than
    // orphaning the reply underneath it. Four exclusions, each for its own
    // reason:
    //  * still generating — the question is live; Stop is the affordance there.
    //  * a summary still being written — same, one rung down.
    //  * attachments — re-sending drops them (their bytes left component state
    //    on the original send), and quietly re-asking WITHOUT the files is a
    //    different question. Those turns keep "Ask again", which hands the text
    //    back to the composer instead. Same rule `handleAskAgain` already uses.
    //  * an open clarify batch — the turn is mid-conversation with the gate,
    //    and rewriting the question under it would strand the answers.
    //
    // Note what is NOT excluded any more: an ANSWERED turn. It was, while there
    // was no way to take its answer back out of the record; the rewind
    // (`rewindToUserTurn` → `DELETE …/turns/{id}`) is what made past prompts
    // editable at all.
    //  * a PEER'S message (project GROUP surface): a turn that carries `author`
    //    belongs to someone else in the shared thread. Copy is fine on it, but
    //    editing or re-asking a message the viewer didn't write is never theirs
    //    to do. `author` is unset on main, private, and the viewer's OWN group
    //    turns, so this is byte-identical for every single-author surface.
    const canReAskTurn =
      !!queryBody &&
      !turn.author &&
      !isGenerating &&
      !turn.summaryPending &&
      !turn.attachments?.length &&
      !turn.clarify?.length
    const canEditTurn = !!onEditTurn && canReAskTurn
    const canRetryTurn = !!onRetryTurn && canReAskTurn
    const isEditing = canEditTurn && editingTurnId === turn.id

    // Per-surface AGENT-body override (the project chats' on-join greeting
    // `MORE_MARKER` lead/Show-more split). Returns a node ONLY for the turns it
    // owns (a greeting carrying the marker); every other turn — and every main
    // turn, which never passes `renderAgentBody` — returns null and stays on the
    // default reply ladder, so the mapped output is byte-identical there.
    const surfaceAgentBody = renderAgentBody ? renderAgentBody(turn) : null

    return {
      turnId: turn.id,
      onCopyUserTurn: canCopyTurn ? () => onCopyTurn!(turn) : undefined,
      copied: copiedTurnId === turn.id,
      onRetryUserTurn: canRetryTurn ? () => onRetryTurn!(turn) : undefined,
      onEditUserTurn: canEditTurn ? () => onEditTurn!(turn.id) : undefined,
      editing: isEditing,
      onSubmitEdit: isEditing ? (text: string) => onSubmitTurnEdit?.(turn, text) : undefined,
      onCancelEdit: isEditing ? () => onCancelTurnEdit?.() : undefined,
      // Only when the user actually said something. A turn can be AGENT-ONLY.
      // A turn that CARRIES an `author` is a project-group PEER's message: its
      // head shows the peer's own name/initials/tint (precomputed by the group
      // adapter), not the current viewer's. Absent (main, private, and the
      // viewer's OWN group turns) → the viewer's name/initials, exactly as
      // before. Data-driven: no author ⇒ byte-identical to the pre-change map.
      user: {
        // A turn that CARRIES an `author` is a project-group PEER's message: its
        // head shows the peer's own name/initials/tint; absent (main, private,
        // own group turns) → the viewer's, exactly as before.
        name: turn.author ? turn.author.name : name,
        initials: turn.author ? (turn.author.initials ?? turn.author.name.slice(0, 2).toUpperCase()) : userInitials,
        ...(turn.author?.avatarStyle ? { avatarStyle: turn.author.avatarStyle } : {}),
        query: queryBody,
        quote,
        // The quote block is clamped, so the tail of a long highlight would be
        // unreachable without this. Reuses the SAME overlay a file card opens
        // (`AttachmentViewer` with text and no storage key) rather than
        // inventing a second read-this-passage surface.
        onOpenQuote: quote
          ? () => setViewerAttachment({ name: QUOTE_VIEWER_NAME, content: quote, plain: true })
          : undefined,
        // Per-surface user-body override (project GROUP → mention chips). Only
        // for a turn that HAS query text, so an agent-only turn (query === "")
        // never grows an empty user body. Unset on main/private → plain query.
        ...(renderUserBody && turn.query ? { bodyNode: renderUserBody(turn) } : {}),
        attachments: turn.attachments?.map((a) => ({
          name: a.name, content: a.content, downloadable: !!a.key,
          key: a.key, mime: a.mime,
        })),
        onOpenAttachment: (a) =>
          setViewerAttachment({ name: a.name, content: a.content ?? "", key: a.key, mime: a.mime }),
      },
      // Multi-party attribution (project-group peers only): a peer turn renders
      // start-aligned with a `${name} (${role})` head + tinted avatar, via
      // ChatBubble's EXISTING multi-party arm, and with NO agent block — the
      // peer's message is its own bubble; Sprntly's reply (if any) is a separate
      // author-less turn. Unset for every single-author turn, so main/private
      // and the viewer's own turns keep the default right-aligned rendering.
      ...(turn.author ? {
        speaker: turn.author.name,
        role: turn.author.role ?? null,
        humanAlign: "start" as const,
        showAgent: false,
      } : {}),
      // A GROUP post the agent was never addressed on (2-mode gate's post-only
      // branch — multi-member, untagged — or its hydrated form). The viewer's
      // OWN such message keeps its default right-aligned head, but drops the
      // agent block entirely so it never renders the "No response was generated"
      // placeholder for a turn that was intentionally silent. Peer posts already
      // suppress the agent block via `author` above.
      ...(turn.postedOnly && !turn.author ? { showAgent: false } : {}),
      agentName: AGENT_NAME,
      isLast,
      isGenerating,
      isAnimated: hasFreshReply,
      waitSkill: waitSkill ? { label: waitSkill.label, id: waitSkill.id } : null,
      waitStartedAt,
      waitResumed,
      partial: turn.partial,
      streamDropped: turn.streamDropped,
      livePhase: turn.livePhase,
      error: turn.error,
      onAskAgain: () => handleAskAgain(turn),
      stopped: turn.stopped,
      timedOut: turn.timedOut,
      onReload: () => window.location.reload(),
      interrupted: turn.interrupted,
      summaryPending: turn.summaryPending,
      onStop: handleStopAsk,
      prdCommandThinking: !!activeTab?.prdCommandThinking,
      goalGate: turn.goalGate,
      goalGateResolved: turn.goalGateResolved,
      goalGateError: turn.goalGateError,
      // Busy is per-TURN, not per-thread: two gates can sit in one thread (the
      // definition above, the plan below) and a thread-wide flag would grey out
      // the settled one as well as the live one.
      goalGateBusy: goalGateBusyTurnId === turn.id,
      // `runId` off the GATE, and the tab off the active tab: both survive a
      // reload, which a ref-held Map does not.
      onConfirmGoalDefinition: (d: string) => {
        if (activeTab && turn.goalGate?.kind === "definition") {
          confirmGoalDefinition?.(activeTab.id, turn.id, turn.goalGate.runId, d)
        }
      },
      onApproveGoalPlan: (decision: PlanDecision) => {
        if (activeTab && turn.goalGate?.kind === "plan") {
          approveGoalPlan?.(activeTab.id, turn.id, turn.goalGate.runId, decision)
        }
      },
      clarify: turn.clarify,
      clarifyResolved: turn.clarifyResolved,
      clarifyPopupNote: clarifyPopupOpen && pendingClarifyTurn?.id === turn.id && !turn.clarifyResolved,
      clarifyGateOpen: !!activeTab?.pendingClarify,
      clarifyBusy: busy || !!activeTab?.prdGenerating,
      onSubmitClarify: (answers) => submitClarifyAnswers(answers),
      onSkipClarify: () => submitClarifyAnswers([]),
      reply: turn.reply,
      // The greeting's lead/Show-more body REPLACES the reply ladder (via
      // ChatBubble's `agentBodyNode` escape hatch). Spread only when the
      // surface owns this turn, so a normal turn never grows the field.
      ...(surfaceAgentBody ? { agentBodyNode: surfaceAgentBody } : {}),
      // A report answer is an ARTIFACT: it reads in the panel's Reports tab.
      onOpenReport: openReportByTitle,
      openCandidates: turn.openCandidates,
      onOpenCandidate: (candidate) => { openArtifactInPanel(candidate) },
      artifactList: turn.artifactList,
      onOpenArtifactItem: openChatArtifactItem,
      artifactsDisabled: busy,
      footer,
      afterNode,
    }
  })
}
