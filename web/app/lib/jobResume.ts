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

export type JobKind =
  | "prd"
  | "evidence"
  | "multi-agent"
  | "ask"
  | "website-analysis"
  // The background LLM extraction over an uploaded onboarding context file —
  // kicked on the import-context step, still running while the user is on
  // connectors, so a reload in between must re-attach rather than orphan it.
  | "llm-context-import"

const PREFIX = "sprntly_pending_job"

function keyFor(kind: JobKind, company: string, scope: string): string {
  return `${PREFIX}_${kind}_${company}_${scope}`
}

/** Stable scope for an insight-bound job (PRD / evidence / multi-agent). */
export function insightScope(briefId: number, insightIndex: number): string {
  return `b${briefId}:i${insightIndex}`
}

export type PendingJob = {
  id: string
  /** Set only for a chat Ask (`kind === "ask"`) whose ORIGINATING send minted
   *  a reply dedup key (see `runAskGeneration`'s `replyClientMessageId` opt).
   *  Carries the key across a remount/second-mount so whichever completion
   *  path (a live poll, or a resumed one from a second mount/tab watching the
   *  SAME conversation-scoped ask) persists this ask's answer stamps the
   *  SAME `client_message_id` on its `add_turn` write — the server's
   *  idempotent upsert (`routes/conversations.py::add_turn`) then collapses
   *  a same-key double-submit to one row. Every other job kind, and every
   *  ask that predates this field, simply omits it. */
  clientMessageId?: string
}

/** Persist the active job id. `id` is the prd_id / evidence_id / run_id.
 *  `clientMessageId` (ask jobs only) rides alongside it in the SAME record —
 *  see `PendingJob.clientMessageId`. Omitting it keeps the stored value the
 *  ORIGINAL bare string every existing (non-ask, or pre-this-ticket ask)
 *  caller already writes and reads, byte-identical. */
export function setPendingJob(
  kind: JobKind,
  company: string,
  scope: string,
  id: number | string,
  clientMessageId?: string,
): void {
  try {
    localStorage.setItem(
      keyFor(kind, company, scope),
      clientMessageId
        ? JSON.stringify({ id: String(id), clientMessageId })
        : String(id),
    )
  } catch {
    /* localStorage unavailable (SSR / private mode) — resume is best-effort */
  }
}

/** Read a persisted pending job id, or null if none is in flight. Reads BOTH
 *  shapes `setPendingJob` can have written: the original bare id string
 *  (every non-ask job, and every ask with no reply dedup key), and the
 *  JSON-enveloped `{id, clientMessageId}` shape a chat Ask writes when one
 *  was minted. A bare value never starts with `{`, so the two are
 *  unambiguous without needing a parse-and-catch on the common case. */
export function getPendingJob(
  kind: JobKind,
  company: string,
  scope: string,
): PendingJob | null {
  try {
    const raw = localStorage.getItem(keyFor(kind, company, scope))
    if (!raw) return null
    if (!raw.startsWith("{")) return { id: raw }
    try {
      const parsed = JSON.parse(raw) as { id?: unknown; clientMessageId?: unknown }
      if (typeof parsed.id !== "string") return { id: raw }
      return {
        id: parsed.id,
        ...(typeof parsed.clientMessageId === "string" ? { clientMessageId: parsed.clientMessageId } : {}),
      }
    } catch {
      // A malformed envelope is worse than a lost resume, never a crash.
      return { id: raw }
    }
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
