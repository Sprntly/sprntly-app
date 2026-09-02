"use client"

// ── ProjectMainThread — the private-chat mount host ──
//
// AD-P14 (flat routes): mounts in place on the one `/projects?id=<id>` route.
// No route change, no `[id]` segment, ever.
//
// `useProjectConversation` composes the shared unit
// (`useComposer`/`useThreadScroll`/`useMainConversation`) over a
// single-conversation store bound to a project-scoped `conversations` row, and
// hands the exact host-bag to the same `ConversationView` main renders per tab.
import type { OpenArtifactCandidate } from "../../../../lib/api"
import { ConversationView } from "../ConversationView"
import { AttachmentViewer } from "../../../shared/AttachmentViewer"
import { useProjectConversation } from "./useProjectConversation"
import styles from "./ProjectMainThread.module.css"

/** The project's ONE chat surface = main's chat, configured for this
 *  project's single conversation. A distinct component so its hook mounts/
 *  unmounts cleanly on a project switch (keyed by the host below). */
function ProjectChatSurface({
  projectId,
  currentUserId,
  onOpenArtifact,
}: {
  projectId: number | string
  currentUserId?: string | null
  onOpenArtifact?: (candidate: OpenArtifactCandidate) => void
}) {
  // The adapter owns the attachment-viewer state (main keeps it on ChatScreen);
  // pull it off the host-bag and render the SHARED AttachmentViewer here, at the
  // surface root — mirroring how ChatScreen mounts the same component beside its
  // own conversation view. Everything else is the exact `ConversationViewProps`.
  const { viewerAttachment, setViewerAttachment, ...viewProps } =
    useProjectConversation(projectId, currentUserId, onOpenArtifact)
  return (
    <>
      <ConversationView {...viewProps} />
      {viewerAttachment ? (
        <AttachmentViewer attachment={viewerAttachment} onClose={() => setViewerAttachment(null)} />
      ) : null}
    </>
  )
}

export type ProjectMainThreadProps = {
  projectId: number | string
  /** The caller's own uid — threaded into `useProjectConversation` so this
   *  chat can subscribe to its own realtime topic (`project:{id}:user:{uid}`).
   *  `null`/omitted (unresolved auth) leaves it realtime-blind, not crashed. */
  currentUserId?: string | null
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

/** Mounts the private project chat — in place, no route change (AD-P14).
 *  Renders exactly one surface: main's chat, mounted on this project's
 *  bound conversation. Keyed on `projectId` so a flat-route A→B project
 *  switch (`?id=` change with no unmount) resets the engine + store
 *  together rather than reconciling in place — latent/defensive, no
 *  current nav path does a direct A→B without an unmount, but it makes the
 *  asserted flat-route premise hold rather than patching a live bug. */
export function ProjectMainThread({ projectId, currentUserId, onOpenArtifact }: ProjectMainThreadProps) {
  return (
    <div className={styles.host} data-testid="main-thread-individual" data-project-id={String(projectId)}>
      <ProjectChatSurface
        key={String(projectId)}
        projectId={projectId}
        currentUserId={currentUserId}
        onOpenArtifact={onOpenArtifact}
      />
    </div>
  )
}

export type { OpenArtifactCandidate }
