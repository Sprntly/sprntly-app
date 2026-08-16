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
// renderers, the open-artifact footer, the roster) and WIRES the picker's
// `onInputCapture` into the shell's draft API. (The invoked-by/stayed-out
// badges were removed from the UI — debug-y internal state, not user-facing;
// the underlying state is still logged/recorded, just not rendered.) The
// 897-line pre-fold implementation and its module CSS are gone;
// every visible chat surface — main, project-private, project-group — is now
// defined once (spec §2.5, AD-P13).
import { useRef, useState } from "react"
import { ChatShell } from "../../../shared/chat-shell/ChatShell"
import type { ChatSurfaceDescriptor, ComposerDraftApi, ShellTurn } from "../../../shared/chat-shell/types"
import shellCss from "../../../shared/chat-shell/ChatShell.module.css"
import { AskReplyBody } from "../../../shared/AskReplyBody"
import { AssistantThinkingSkeleton } from "../../../shared/AssistantThinkingSkeleton"
import { OpenArtifactChips } from "../../../shared/OpenArtifactChips"
import { AGENT_NAME } from "../../../../lib/agent"
import { type AskResponse, type OpenArtifactCandidate } from "../../../../lib/api"
import { personAvatarStyle } from "./avatarColor"
import { useProjectGroupThread } from "./useProjectGroupThread"
import { useMentionPicker, MentionBubble } from "./useMentionPicker"
import extras from "./GroupChatExtras.module.css"

const COMPOSER_PLACEHOLDER = "Message the team, or @Sprntly to hand it a task…"

/** A group turn's plain `content` shaped into the minimal `AskResponse`
 *  `AskReplyBody` needs — group turns carry no citations/key-points/skill
 *  metadata (that belongs to `/v1/ask`), so those are the honest empty values. */
function toAskResponse(content: string): AskResponse {
  return { answer: content, key_points: [], citations: [], confidence: 1, unanswered: "" }
}

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
      agentBadge: "AGENT",
      multiParty: true,
      timestamps: "fromTurn",
      renderUserBody: (turn: ShellTurn) => <MentionBubble content={turn.content ?? ""} />,
      renderAgentBody: (turn: ShellTurn) => <AskReplyBody reply={toAskResponse(turn.content ?? "")} />,
      // The invoked-by / detected state badge was removed from the UI
      // (debug-y internal state, not user-facing) — `trigger_kind`
      // ("mention" vs "gate") still durably records which path an agent
      // turn took, so nothing is lost for debugging.
      // Open-artifact chips — a LIVE, backend-tested feature (Gate-1 #2). The
      // engine exposes `footerData.openCandidates` on agent turns for exactly
      // this. Wired through the host-supplied `turnFooter` closure so
      // `ChatShell.tsx` stays untouched.
      turnFooter: (turn: ShellTurn) => {
        if (turn.author.kind !== "agent") return null
        const fd = turn.footerData as { openCandidates?: OpenArtifactCandidate[] } | undefined
        return <OpenArtifactChips candidates={fd?.openCandidates ?? []} onOpen={(c) => onOpenArtifact?.(c)} />
      },
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
      slashMenu: mentions.pickerNode,
      // Picker keys outrank Enter-to-send: a `true` return means the picker
      // consumed the key (arrow nav / Enter-selects / Escape-closes).
      onKeyDownCapture: mentions.handleKeys,
      voice: "default",
      attachments: false,
    },
    reply: {
      mode: "backgrounded",
      // The "Sprntly stayed out" pill was removed from the UI (debug-y
      // internal state, not user-facing) — the stay-out decision is now only
      // logged (routes.py `gate_stayout` branch + the existing
      // `group_gate_decision` log). The seam stays present-but-unwired for a
      // later honest, persisted run-status.
      runStatus: () => null,
    },
    send: { onSubmit: engine.post, pendingSendBubble: false },
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
