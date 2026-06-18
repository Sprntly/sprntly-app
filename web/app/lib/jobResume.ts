// Persist in-flight server-side AI job ids so a remount can RESUME polling
// instead of orphaning a running job.
//
// Problem: PRD / evidence / multi-agent generation kicks off a fire-and-forget
// server job and then polls in an `await` closure, with the only client trace
// being an in-memory `*Generating` flag. If the screen/tab remounts (e.g. the
// tab is backgrounded long enough that the route unmounts, or the user
// navigates away and back), the closure is gone and the UI never resumes —
// even though the server finishes the job. The backend dedupes a re-kick
// (`force=false` returns the existing row, see routes/prd.py / evidence.py), so
// persisting the active job id and re-entering the (now visibility-aware) poll
// is a low-risk UX resume.
//
// Keyed per (kind + company + scope) so the id is unambiguous: PRD/evidence are
// scoped per brief insight (briefId:insightIndex); multi-agent likewise. The id
// is cleared when the job completes or errors.

export type JobKind = "prd" | "evidence" | "multi-agent" | "ask"

const PREFIX = "sprntly_pending_job"

function keyFor(kind: JobKind, company: string, scope: string): string {
  return `${PREFIX}_${kind}_${company}_${scope}`
}

/** Stable scope for an insight-bound job (PRD / evidence / multi-agent). */
export function insightScope(briefId: number, insightIndex: number): string {
  return `b${briefId}:i${insightIndex}`
}

export type PendingJob = { id: string }

/** Persist the active job id. `id` is the prd_id / evidence_id / run_id. */
export function setPendingJob(
  kind: JobKind,
  company: string,
  scope: string,
  id: number | string,
): void {
  try {
    localStorage.setItem(keyFor(kind, company, scope), String(id))
  } catch {
    /* localStorage unavailable (SSR / private mode) — resume is best-effort */
  }
}

/** Read a persisted pending job id, or null if none is in flight. */
export function getPendingJob(
  kind: JobKind,
  company: string,
  scope: string,
): PendingJob | null {
  try {
    const id = localStorage.getItem(keyFor(kind, company, scope))
    return id ? { id } : null
  } catch {
    return null
  }
}

/** Clear the persisted id once the job is terminal (ready / failed / timeout). */
export function clearPendingJob(
  kind: JobKind,
  company: string,
  scope: string,
): void {
  try {
    localStorage.removeItem(keyFor(kind, company, scope))
  } catch {
    /* ignore */
  }
}
