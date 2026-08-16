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
 * (`ChatArtifactActions` / `ChatTicketSetActions`), imported from ChatScreen —
 * a type-and-lazy-value cycle that resolves safely because both are hoisted
 * function declarations used only inside this function, called at render time.
 */

import { AGENT_NAME } from "../../../lib/agent"
import type { ChatTranscriptTurn } from "../../shared/ChatTranscript"
import type { MapMainTurnsDeps } from "../../shared/chat-shell/types"
import { SlackShareMessage } from "../../shared/SlackSharePreviewCard"
import { ChatArtifactActions, ChatTicketSetActions, type ThreadTurn } from "./ChatScreen"

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
    setViewerAttachment,
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

    const afterNode =
      inlinePrdCards && idx === inlinePrdAnchorIdx ? (
        <>
          {insightCardNode}
          {prdQuestionsNode}
          {shareNode}
        </>
      ) : shareNode

    return {
      turnId: turn.id,
      // Only when the user actually said something. A turn can be AGENT-ONLY.
      user: {
        name,
        initials: userInitials,
        query: turn.query,
        attachments: turn.attachments?.map((a) => ({
          name: a.name, content: a.content, downloadable: !!a.key,
          key: a.key, mime: a.mime,
        })),
        onOpenAttachment: (a) =>
          setViewerAttachment({ name: a.name, content: a.content ?? "", key: a.key, mime: a.mime }),
      },
      agentName: AGENT_NAME,
      agentBadge: "Product Coworker",
      isLast,
      isGenerating,
      isAnimated: hasFreshReply,
      waitSkill: waitSkill ? { label: waitSkill.label, id: waitSkill.id } : null,
      waitStartedAt,
      waitResumed,
      partial: turn.partial,
      streamDropped: turn.streamDropped,
      error: turn.error,
      onAskAgain: () => handleAskAgain(turn),
      stopped: turn.stopped,
      timedOut: turn.timedOut,
      onReload: () => window.location.reload(),
      interrupted: turn.interrupted,
      summaryPending: turn.summaryPending,
      onStop: handleStopAsk,
      prdCommandThinking: !!activeTab?.prdCommandThinking,
      clarify: turn.clarify,
      clarifyResolved: turn.clarifyResolved,
      clarifyPopupNote: clarifyPopupOpen && pendingClarifyTurn?.id === turn.id && !turn.clarifyResolved,
      clarifyGateOpen: !!activeTab?.pendingClarify,
      clarifyBusy: busy || !!activeTab?.prdGenerating,
      onSubmitClarify: (answers) => submitClarifyAnswers(answers),
      onSkipClarify: () => submitClarifyAnswers([]),
      reply: turn.reply,
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
