"use client"

// ── ProjectMainThread — the group⇆individual swap host ──
//
// AD-P14 (flat routes): the swap is REACT STATE ONLY — `activeChat` selects
// which chat renders, in place, on the one `/projects?id=<id>` route. No route
// change, no `[id]` segment, ever.
//
// The prior per-surface chat implementations (`ProjectGroupChat` /
// `ProjectPrivateChat`) were DELETED and rebuilt as a SINGLE configurable mount
// of main's ACTUAL chat: `useProjectConversation` composes the shared unit
// (`useComposer`/`useThreadScroll`/`useMainConversation`) over a
// single-conversation store bound to a project-scoped `conversations` row, and
// hands the exact host-bag to the same `ConversationView` main renders per tab.
// The group/individual switch (react-state-only, AD-P14) selects which surface
// mounts — each is its own conversation.
import type { OpenArtifactCandidate } from "../../../../lib/api"
import { ConversationView } from "../ConversationView"
import { AttachmentViewer } from "../../../shared/AttachmentViewer"
import { useProjectConversation, type ProjectChatSurface } from "./useProjectConversation"
import styles from "./ProjectMainThread.module.css"

/** One project chat surface = main's chat, configured for that surface's single
 *  conversation. A distinct component so its hook mounts/unmounts cleanly on the
 *  group⇆individual swap. */
function ProjectChatSurface({
  projectId,
  surface,
  onOpenArtifact,
  projectName,
}: {
  projectId: number | string
  surface: ProjectChatSurface
  onOpenArtifact?: (candidate: OpenArtifactCandidate) => void
  projectName?: string
}) {
  // The adapter owns the attachment-viewer state (main keeps it on ChatScreen);
  // pull it off the host-bag and render the SHARED AttachmentViewer here, at the
  // surface root — mirroring how ChatScreen mounts the same component beside its
  // own conversation view. Everything else is the exact `ConversationViewProps`.
  const { viewerAttachment, setViewerAttachment, ...viewProps } =
    useProjectConversation(projectId, surface, onOpenArtifact, projectName)
  return (
    <>
      <ConversationView {...viewProps} />
      {viewerAttachment ? (
        <AttachmentViewer attachment={viewerAttachment} onClose={() => setViewerAttachment(null)} />
      ) : null}
    </>
  )
}

export type ActiveChat = "group" | "individual"

export type ProjectMainThreadProps = {
  projectId: number | string
  activeChat: ActiveChat
  /** The project's display name — threaded into the GROUP chat's empty-state
   *  greeting ("Welcome to the {name} team chat"). Optional: absent falls back
   *  to a name-less greeting. The individual chat ignores it (keeps default). */
  projectName?: string
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
 *  `activeChat` — in place, no route change (AD-P14). Renders exactly one: main's
 *  chat, mounted on that surface's project-bound conversation.
 *
 *  The surface component is KEYED on project+surface so toggling group⇆private
 *  forces a fresh unmount+remount rather than a re-render in place. Without the
 *  key React reconciles the two branches to the same `ProjectChatSurface`
 *  position and only changes its `surface` prop; the single-conversation store
 *  (thread/dbConvId) then persists and the previous surface's messages stay on
 *  screen (the hydrate guard only fills a still-empty thread). A fresh mount
 *  resets the whole store and re-resolves the conversation, exactly like a page
 *  load — which was already showing the correct thread. */
export function ProjectMainThread({ projectId, activeChat, onOpenArtifact, projectName }: ProjectMainThreadProps) {
  if (activeChat === "group") {
    return (
      <div className={styles.host} data-testid="main-thread-group" data-project-id={String(projectId)}>
        <ProjectChatSurface key={`${String(projectId)}:group`} projectId={projectId} surface="group" onOpenArtifact={onOpenArtifact} projectName={projectName} />
      </div>
    )
  }
  return (
    <div className={styles.host} data-testid="main-thread-individual" data-project-id={String(projectId)}>
      <ProjectChatSurface key={`${String(projectId)}:individual`} projectId={projectId} surface="individual" onOpenArtifact={onOpenArtifact} />
    </div>
  )
}

export type { OpenArtifactCandidate }
