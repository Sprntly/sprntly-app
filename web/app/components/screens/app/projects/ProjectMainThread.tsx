"use client"

// ── ProjectMainThread — the group⇆individual swap host ──
//
// AD-P14 (flat routes): the swap is REACT STATE ONLY — `activeChat` selects
// which chat renders, in place, on the one `/projects?id=<id>` route. No route
// change, no `[id]` segment, ever.
//
// The prior per-surface chat implementations (`ProjectGroupChat` /
// `ProjectPrivateChat`) were DELETED — both project chats are being rebuilt as a
// single configurable mount of main's actual chat. Until that lands, each side
// of the swap renders a placeholder; the toggle scaffold + testid hosts stay so
// the surrounding project detail chrome (the group/individual switch) is
// unchanged and the future main-chat mounts drop straight into these slots.
import type { OpenArtifactCandidate } from "../../../../lib/api"
import styles from "./ProjectMainThread.module.css"

export type ActiveChat = "group" | "individual"

export type ProjectMainThreadProps = {
  projectId: number | string
  activeChat: ActiveChat
  onOpenArtifact?: (candidate: OpenArtifactCandidate) => void
  /** DEFERRED (dropped with the old chats): the cross-chat insight banner. Kept
   *  in the prop type so callers are unchanged; unused until the rebuilt chat
   *  re-adds it. */
  insightNote?: { by: string; text: string } | null
  /** DEFERRED: fired after a client-driven generate settles. Unused while the
   *  chat mount is a placeholder. */
  onArtifactsChanged?: () => void
  /** DEFERRED: the open-PRD edit target. Unused until the rebuilt chat wires it. */
  openPrdId: number | null
}

/** Swaps the main pane between the group chat and the individual chat per
 *  `activeChat` — in place, no route change (AD-P14). Renders exactly one.
 *  Both bodies are placeholders pending the configurable main-chat mount. */
export function ProjectMainThread({ projectId, activeChat }: ProjectMainThreadProps) {
  if (activeChat === "group") {
    return (
      <div className={styles.host} data-testid="main-thread-group" data-project-id={String(projectId)} />
    )
  }
  return (
    <div className={styles.host} data-testid="main-thread-individual" data-project-id={String(projectId)} />
  )
}

export type { OpenArtifactCandidate }
