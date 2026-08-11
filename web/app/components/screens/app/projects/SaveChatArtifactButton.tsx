"use client"

// ── SaveChatArtifactButton — the item-14 substrate's UI half ──
//
// Backend + API client (`projectsApi.saveChatArtifact`,
// `POST /v1/projects/{id}/artifacts/from-chat`) is this ticket's real
// scope; wiring a "Save as artifact" affordance into the actual group-chat
// thread (message-level placement, styling to match `ProjectGroupChat`) is
// a SEPARATE ticket. This is a small, STANDALONE, unwired affordance —
// composable from wherever that follow-up ticket decides to mount it — so
// the client call has a real caller and a real component test, without
// this ticket reaching into `ProjectGroupChat.tsx`.
//
// Deliberately minimal: no toast system dependency, no design-token guess
// — a disabled-while-saving button + inline status text, mirroring the
// `error`-state pattern `CreateProjectModal.tsx` uses for its own
// best-effort calls.
import { useState } from "react"
import { ApiError, projectsApi } from "../../../../lib/api"

export type SaveChatArtifactButtonProps = {
  projectId: number | string
  /** The chat output to save — the caller supplies this (e.g. a group-turn's
   *  `content`, or an individual-chat answer's markdown body). */
  content: string
  /** Optional explicit title; omitted lets the server derive one (the
   *  content's first non-empty line, else "Saved from chat"). */
  title?: string
  /** Called once the save succeeds, with the new artifact's id. */
  onSaved?: (artifactId: number) => void
}

type SaveState = "idle" | "saving" | "error"

/** A "Save as artifact" button for a chat output. Calls
 *  `projectsApi.saveChatArtifact` and reports success/failure inline —
 *  no navigation, no toast. */
export function SaveChatArtifactButton({
  projectId,
  content,
  title,
  onSaved,
}: SaveChatArtifactButtonProps) {
  const [state, setState] = useState<SaveState>("idle")
  const [error, setError] = useState<string | null>(null)

  const handleSave = () => {
    if (!content.trim() || state === "saving") return
    setState("saving")
    setError(null)
    projectsApi
      .saveChatArtifact(projectId, { content, title })
      .then((saved) => {
        setState("idle")
        onSaved?.(saved.artifact_id)
      })
      .catch((err) => {
        setState("error")
        setError(
          err instanceof ApiError && err.status === 400
            ? "Nothing to save"
            : "Couldn't save this as an artifact. Try again.",
        )
      })
  }

  return (
    <div>
      <button type="button" onClick={handleSave} disabled={state === "saving" || !content.trim()}>
        {state === "saving" ? "Saving…" : "Save as artifact"}
      </button>
      {error ? <span role="alert">{error}</span> : null}
    </div>
  )
}
