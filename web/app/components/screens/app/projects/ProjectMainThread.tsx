"use client"

// ── ProjectMainThread — the group⇆individual swap host ──
//
// AD-P14 (flat routes): the swap is REACT STATE ONLY — `activeChat` selects
// which of the two chats renders, in place, on the one `/projects?id=<id>`
// route. No route change, no `[id]` segment, ever.
//
// AD-P13 (never fork the monolith, one chat presentation layer): NEITHER
// side of this swap touches the app's existing multi-tab chat container.
// The group side composes the shared primitives (`ProjectGroupChat`); the
// private side is a thin host (`ProjectPrivateChat`) that renders the private
// thread through the shared `ChatShell` — the engine (`useProjectPrivateThread`)
// owns the project-genuine machinery, the shell owns the presentation. Neither
// side imports the chat monolith; this file doesn't either.
//
// The private/group toggle is surface-keyed (each host keys its `<ChatShell>`
// by surface) so React does not reuse the subtree across the toggle and leak
// scroll/draft/focus between the two chats (spec §2.5/§6.2).
import { ProjectGroupChat, type ProjectGroupChatProps } from "./ProjectGroupChat"
import { ProjectPrivateChat } from "./ProjectPrivateChat"
import type { OpenArtifactCandidate } from "../../../../lib/api"
import styles from "./ProjectMainThread.module.css"

export type ActiveChat = "group" | "individual"

export type ProjectMainThreadProps = {
  projectId: number | string
  activeChat: ActiveChat
  onOpenArtifact?: ProjectGroupChatProps["onOpenArtifact"]
  /** The cross-chat INSIGHT turn (design-spec AC7/AC11) — a note surfaced
   *  from the group chat, rendered by `ProjectIndividualChat` with the SAME
   *  `bc-turn--insight`/`bc-insight-msg-kind` CSS the app's existing
   *  insight-opening card wears. No real data source feeds this yet —
   *  omitted (the default), it renders nothing. */
  insightNote?: { by: string; text: string } | null
}

/** Swaps the main pane between the group chat and the individual chat per
 *  `activeChat` — in place, no route change (AD-P14). Renders exactly one. */
export function ProjectMainThread({ projectId, activeChat, onOpenArtifact, insightNote }: ProjectMainThreadProps) {
  if (activeChat === "group") {
    return (
      <div className={styles.host} data-testid="main-thread-group">
        <ProjectGroupChat projectId={projectId} onOpenArtifact={onOpenArtifact} />
      </div>
    )
  }
  return (
    <div className={styles.host} data-testid="main-thread-individual" data-project-id={String(projectId)}>
      <ProjectPrivateChat projectId={projectId} onOpenArtifact={onOpenArtifact} insightNote={insightNote} />
    </div>
  )
}

export type { OpenArtifactCandidate }
