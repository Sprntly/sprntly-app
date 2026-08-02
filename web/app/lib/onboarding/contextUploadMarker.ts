// "This workspace has already handed over its .md" — a durable, per-workspace
// marker, written the moment a context upload lands.
//
// Two screens offer the same upload: the dedicated import-context step (02) and
// the second-chance banner on the workspace step (06). Asking again after the
// user has already given us the file reads as the flow forgetting what they
// did, so the later offer is suppressed once this marker is set.
//
// It exists because nothing else answers the question durably. The provider's
// in-memory `contextImport` state resets on reload, and the jobResume record for
// the extraction is CLEARED the moment the job goes terminal (see
// runContextImport) — so someone who uploaded, waited for the extraction, then
// reloaded would look like they had never uploaded at all. This marker is
// written at upload time and never cleared: the upload is a fact about the
// user's setup, not an in-flight job.
//
// Best-effort by design. With localStorage unavailable (private mode, SSR) both
// calls no-op and the banner simply shows again — the safe way to be wrong,
// since that costs a redundant offer rather than a lost chance to upload.

const KEY_PREFIX = "sprntly_llm_context_uploaded"

function keyFor(workspaceId: string): string {
  return `${KEY_PREFIX}_${workspaceId}`
}

/** Record that this workspace uploaded a context .md. Safe to call repeatedly. */
export function markContextFileUploaded(workspaceId: string | null | undefined): void {
  if (!workspaceId) return
  try {
    localStorage.setItem(keyFor(workspaceId), "1")
  } catch {
    /* unavailable — the later offer just shows again, which is harmless */
  }
}

/** True once this workspace has uploaded a context .md at any point. */
export function hasUploadedContextFile(
  workspaceId: string | null | undefined,
): boolean {
  if (!workspaceId) return false
  try {
    return localStorage.getItem(keyFor(workspaceId)) !== null
  } catch {
    return false
  }
}
