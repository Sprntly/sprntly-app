"use client"

// ── ProjectGroupChat — the thin multi-party group-chat host ──
//
// The final fold of the chat-shell refactor. This file is now a THIN host that
// renders the group thread through the shared `ChatShell` (surface:
// "project_group"). It owns NO chat machinery of its own:
//   • `useProjectGroupThread` owns the data (transport: realtime + since-reconcile
//     + focus-gated poll, dedup, the optimistic negative-id send with rollback +
//     the same-content never-block guard, presence/typing, the invoked-by
//     precompute, the styled error/typing/posting-wait nodes);
//   • `useMentionPicker` owns the @-mention people picker (detection, debounced
//     candidate search, keyboard nav, tag/invite, chip insertion);
//   • `ChatShell` owns what the user sees and touches (the 868px `bc-thread`
//     column, the composer, scroll).
// This host only supplies the descriptor's host closures (the multi-party body
// renderers, the honest run-status node, the open-artifact footer, the roster)
// and WIRES the picker's `onInputCapture` into the shell's draft API. The
// invoke/gate trigger state is not surfaced in the UI (it's debug-y internal
// gate state) — it stays durably recorded server-side via `trigger_kind` for
// debugging. The 897-line pre-fold implementation and its module CSS are gone;
// every visible chat surface — main, project-private, project-group — is now
// defined once (spec §2.5, AD-P13).
import { useRef, useState } from "react"
import { ChatShell } from "../../../shared/chat-shell/ChatShell"
import { useChatComposerController, renderRunStatus } from "../../../shared/chatComposerController"
import type { ChatSurfaceDescriptor, ComposerDraftApi, ShellTurn } from "../../../shared/chat-shell/types"
import shellCss from "../../../shared/chat-shell/ChatShell.module.css"
import { AssistantThinkingSkeleton } from "../../../shared/AssistantThinkingSkeleton"
import { AGENT_BADGE, AGENT_NAME } from "../../../../lib/agent"
import { type ChatArtifactItem, type OpenArtifactCandidate } from "../../../../lib/api"
import { artifactItemAsCandidate } from "./artifactCandidates"
import { personAvatarStyle } from "./avatarColor"
import { useProjectGroupThread } from "./useProjectGroupThread"
import { useMentionPicker, MentionBubble } from "./useMentionPicker"
import extras from "./GroupChatExtras.module.css"

const COMPOSER_PLACEHOLDER = "Message the team, or @Sprntly to hand it a task…"

function initials(name: string | null | undefined): string {
  if (!name) return "?"
  return name.split(" ").filter(Boolean).map((w) => w[0]).join("").toUpperCase().slice(0, 2)
}

export type ProjectGroupChatProps = {
  projectId: number | string
  /** Opens the artifacts modal on a specific candidate (the shell's open-
   *  artifact callback; a no-op here is a legitimate caller until that modal
   *  lands). */
  onOpenArtifact?: (candidate: OpenArtifactCandidate) => void
}

export function ProjectGroupChat({ projectId, onOpenArtifact }: ProjectGroupChatProps) {
  // Declared FIRST — both engines require it up front (they read `.current`
  // lazily, so the shell can populate it on mount without a circular pass).
  const draftApiRef = useRef<ComposerDraftApi | null>(null)
  const engine = useProjectGroupThread({ projectId, draftApiRef })
  const mentions = useMentionPicker({ projectId, draftApiRef })
  // The shared composer controller unifies the send producer: group's send
  // builds a `SendCommand` and hands it to `engine.post`. Attachments and
  // skills are LIVE (matching the private surface): the `+` menu attaches
  // files and browses the skill palette; the engine splices the pinned
  // skill's trigger onto the posted content and forwards attachments +
  // client_message_id on the wire.
  const composerCtl = useChatComposerController({
    scope: { surface: "project_group", projectId: Number(projectId) },
    onCommand: engine.post,
    attachmentsEnabled: true,
    skillsEnabled: true,
  })
  // A one-shot flag flipped when the shell hands the draft API back — it forces
  // exactly one post-mount re-render so the per-render `onInputCapture`
  // assignment below runs against the now-populated ref (the ref assignment in
  // `onDraftApiReady` alone would not re-render the host).
  const [, setApiReady] = useState(false)

  // WIRE THE PICKER (Fable #3). The shell only INVOKES `onInputCapture`
  // (ChatShell.tsx) — nothing assigns it, so the picker is DEAD at HEAD.
  // Reassign it PER RENDER with FRESH closures (never once in `onDraftApiReady`):
  // `engine.sendTyping` is stale-captured there because `myUserId` is null until
  // auth resolves, so a once-on-mount closure would never fire the typing
  // broadcast. Reassigning each render always closes over the current one.
  if (draftApiRef.current) {
    draftApiRef.current.onInputCapture = (value, caret) => {
      mentions.handleComposerInput(value, caret) // opens/updates the @-picker
      engine.sendTyping() // the peer's typing indicator
    }
  }

  const rosterNode =
    engine.presenceMembers.length > 0 ? (
      <div className={extras.roster} data-testid="gc-presence">
        {engine.presenceMembers.map((member) => (
          <span
            key={member.userId}
            className={extras.rosterMember}
            data-testid="gc-presence-member"
            title={member.name}
            style={personAvatarStyle(member.userId, member.name)}
          >
            <span className={extras.rosterDot} aria-hidden="true" />
            {initials(member.name)}
          </span>
        ))}
      </div>
    ) : null

  const descriptor: ChatSurfaceDescriptor = {
    surface: "project_group",
    projectId: Number(projectId),
    testIdPrefix: "gc",
    frame: {
      mode: "thread",
      viewportClassName: shellCss.standaloneViewport,
      aboveViewport: rosterNode,
      loading: engine.loading,
      loadingNode: <AssistantThinkingSkeleton phase="Loading the group chat…" />,
    },
    transcript: {
      agentName: AGENT_NAME,
      agentBadge: AGENT_BADGE,
      multiParty: true,
      timestamps: "fromTurn",
      renderUserBody: (turn: ShellTurn) => <MentionBubble content={turn.content ?? ""} />,
      // NO `renderAgentBody` override: agent turns render through
      // `ChatBubble`'s native reply ladder (the engine feeds `ShellTurn.reply`
      // — the persisted full reply, or the plain content shaped into one) so
      // the same open-candidate chips / artifact-list cards main chat renders
      // appear here, fed by the same envelope-shaped data.
      // The invoked-by / detected trigger badge was removed from the UI —
      // it's debug-y internal gate state, not user-facing. The decision is
      // still durably recorded server-side via `trigger_kind`, so nothing is
      // lost for debugging.
      onOpenCandidate: (c: OpenArtifactCandidate) => onOpenArtifact?.(c),
      onOpenArtifactItem: (item: ChatArtifactItem) => onOpenArtifact?.(artifactItemAsCandidate(item)),
      // The posting-wait node rides the transcript trailing slot (engine-fed,
      // styled by GroupChatExtras).
      trailing: engine.postingWaitNode,
    },
    composer: {
      placeholder: COMPOSER_PLACEHOLDER,
      // never-block: a send fires while an agent reply generates in the
      // background; no Stop UI to swap in (spec §6.2). The engine's same-content
      // guard is the only in-flight protection (R6).
      busyMode: "never-block",
      // Two poppers share the seam, at most one open at a time: the @-mention
      // people picker (opens on "@") and the controller's skill palette
      // (opens from the + menu's Browse skills).
      slashMenu: (
        <>
          {mentions.pickerNode}
          {composerCtl.slashMenu}
        </>
      ),
      // Picker keys outrank Enter-to-send: a `true` return means a picker
      // consumed the key (arrow nav / Enter-selects / Escape-closes) —
      // mention picker first, then the skill palette.
      onKeyDownCapture: (e) => mentions.handleKeys(e) || composerCtl.onKeyDownCapture(e),
      voice: "default",
      attachments: true,
      features: composerCtl.features,
    },
    reply: {
      mode: "backgrounded",
      // The FE agent run-status consume, replacing the old alarming "Sprntly
      // stayed out" pill. Real `ShellTurn.runStatus` takes precedence once the
      // backend feeds it; until then `engine.showStayedOut` is the interim
      // driver, mapped to the QUIET declined treatment (not the alarming pill).
      // `failed` shows error+Retry when `engine.retryRun` exists (dark until the
      // backend exposes retry); `done`/null render nothing.
      runStatus: (status, turn) =>
        renderRunStatus({
          // Backend run-status wins when it reaches us; otherwise a tail
          // @mention (deterministic reply) shows the "thinking" pending state,
          // and only a settled non-mention tail past the grace window shows the
          // quiet "stayed out" note. This is what keeps a still-generating reply
          // from flashing a false stay-out before it streams in.
          status:
            status ??
            (engine.showThinking ? "running" : engine.showStayedOut ? "declined" : null),
          turn,
          prefix: "gc",
          retryRun: engine.retryRun,
        }),
      // The confirmation gate's confirm/cancel seams — the shell's mapped
      // confirm card calls back here with (turnId, token).
      onConfirmMutation: engine.confirmMutation,
      onCancelMutation: engine.cancelMutation,
    },
    send: { onSubmit: composerCtl.submit, pendingSendBubble: false },
    // Error + typing indicator (engine-fed, styled) + the picker's post-select
    // affordance ride above the composer, OUTSIDE the scroll viewport so they
    // never scroll out of view.
    dock: {
      aboveComposer: (
        <>
          {engine.errorRow}
          {engine.typingIndicator}
          {mentions.affordanceRow}
        </>
      ),
    },
  }

  return (
    <ChatShell
      key="project_group"
      descriptor={descriptor}
      turns={engine.turns}
      onDraftApiReady={(api) => {
        draftApiRef.current = api
        setApiReady(true)
      }}
    />
  )
}
