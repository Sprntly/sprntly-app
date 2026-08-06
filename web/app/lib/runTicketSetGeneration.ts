import { ApiError, storiesApi, ticketSetsApi, type GeneratedStory } from "./api"
import { sleepUntilNextPoll } from "./poll"
import { ticketSetOpenScopePatch } from "./panelPrdScope"
import type { AppContentState, TicketSetFailureKind } from "../types/content"

// ── Standalone ticket sets: the ONE owner of a run ───────────────────────────
//
// Tickets asked for in a chat with no PRD become a `ticket_sets` row (backend
// commit 0edeea35). This module owns the whole arc of one run — kick off,
// poll, publish partials, write the terminal state — and every caller goes
// through it.
//
// That single-owner rule is load-bearing, not tidiness. The Tickets tab's PRD
// path re-kicks generation from an EFFECT whenever it remounts, which is fine
// there (the backend dedupes on prd_id and the result is a content-hashed
// cache) and would be ruinous here: two phrasings of the same ask do not
// dedupe on the insight path, so a panel that polled on its own would produce
// a second `ticket_sets` row and a second multi-minute LLM bill for tickets
// the user asked for once. The panel therefore only ever READS
// `content.ticketSet`. Nothing in `components/` may call
// `storiesApi.generateFromInsight`.

/** Shape of the content slice this module owns. */
type TicketSetSlice = NonNullable<AppContentState["ticketSet"]>

export type TicketSetGenResult =
  | { ok: true; set: TicketSetSlice }
  | { ok: false; kind: TicketSetFailureKind }

/** Minimal writer contract — `setContent` from `useContent()`. Passed in rather
 *  than imported so this stays a plain module (no React) and so a test can
 *  observe every patch the run publishes. */
export type SetContent = (patch: Partial<AppContentState>) => void

/** Wall-clock budget for one run. Generous: the fan-out over a long thread is a
 *  multi-minute job, and giving up early on a run that is still going is the
 *  worse failure — the row lands `ready` server-side and the user is told it
 *  stopped. Matches the PRD/evidence runners. */
const MAX_MS = 6 * 60 * 1000
/** Poll cadence, matching the Tickets tab's own PRD-path poll. */
const POLL_MS = 2000
/** Consecutive transport failures tolerated before the run is called lost. A
 *  single dropped poll on a flaky connection is not a failed generation. */
const MAX_TRANSPORT_FAILURES = 3

function isTransportFailure(e: unknown): boolean {
  // `fetch` rejects (no response at all) for a dropped connection; an ApiError
  // means the backend answered, and a 5xx is likewise the server, not the wire
  // — both are worth retrying, neither is a terminal verdict on the run.
  if (e instanceof ApiError) return e.status >= 500 || e.status === 0
  return true
}

/**
 * Generate a standalone ticket set from `task`, publishing every state it
 * passes through into shared content.
 *
 * `conversationId` stamps the thread the set was born in (null for a chat whose
 * first ask has not persisted yet — a set may legitimately have no thread). It
 * is NOT the "standalone" signal: `ticketSetStandalone` is written false here
 * because a run started from a live chat always has one, whatever the id is.
 *
 * Resolves with the terminal outcome; it never throws. Callers that want to
 * toast a failure read `kind` — the raw backend message is deliberately not
 * returned, because nothing that came off the wire should reach the screen.
 */
export async function runTicketSetGeneration(
  task: string,
  conversationId: number | null,
  setContent: SetContent,
): Promise<TicketSetGenResult> {
  // The panel must show a working state from the first frame, before the
  // create call has even answered — and the scope patch has to land WITH it,
  // or the Tickets tab spends the whole run advertising the previous
  // conversation's PRD and Evidence tabs beside the set being written.
  setContent({
    ...ticketSetOpenScopePatch(),
    ticketSet: null,
    ticketSetGenerating: true,
    ticketSetStandalone: false,
  })

  const fail = (kind: TicketSetFailureKind, setId: number | null): TicketSetGenResult => {
    // Keep the set id when there is one: the panel's error state is rendered
    // over the set, and Part 2's artifact row needs to stay resolvable.
    setContent({
      ticketSet: setId == null ? null : {
        id: setId,
        title: "",
        stories: [],
        conversationId,
        status: "failed",
        sourceText: task,
        stubs: [],
        progress: null,
        error: kind,
      },
      ticketSetGenerating: false,
    })
    return { ok: false, kind }
  }

  let start: { job_id: number; ticket_set_id?: number }
  try {
    start = await storiesApi.generateFromInsight(task, conversationId)
  } catch (e) {
    // Nothing was created, so there is no row to point the error at. A 404 here
    // is the backend refusing an unknown/foreign conversation id, which is a
    // failed run rather than a missing set.
    return fail(isTransportFailure(e) ? "network" : "failed", null)
  }

  const setId = start.ticket_set_id ?? null
  if (setId == null) {
    // The insight path always creates the row before scheduling, so this only
    // happens if the request was somehow routed as a PRD run. Without a row
    // there is no durable artifact and nothing was saved — say so, rather than
    // rendering tickets that vanish on the next open.
    return fail("failed", null)
  }

  // Publish the id immediately: it is the tracker-sync scope, and the panel
  // needs it to render a header for the set that is being written.
  setContent({
    ticketSet: {
      id: setId,
      title: "",
      stories: [],
      conversationId,
      status: "generating",
      sourceText: task,
      stubs: [],
      progress: null,
      error: null,
    },
    ticketSetGenerating: true,
  })

  const startedAt = Date.now()
  let transportFailures = 0
  // Once the in-memory job store loses the job (a backend restart drops it),
  // the durable row is the only truth left — switch to it rather than treating
  // a 404 as a lost run. The row is guaranteed to reach a terminal state
  // (routes/stories.py::_settle_ticket_set), which is what makes this safe.
  let pollTheRow = false

  const publishPartial = (
    stories: GeneratedStory[] | undefined,
    stubs: { title: string; summary?: string; prd_section?: string }[] | undefined,
    progress: { done: number; total: number } | undefined,
  ) => {
    setContent({
      ticketSet: {
        id: setId,
        title: "",
        stories: stories ?? [],
        conversationId,
        status: "generating",
        sourceText: task,
        stubs: stubs ?? [],
        progress: progress ?? null,
        error: null,
      },
      ticketSetGenerating: true,
    })
  }

  /** The finished set, read back from the row so the panel gets the
   *  LLM-derived title and the lifecycle-filtered tickets. `fallback` is what
   *  the job handed us — used when the read-back fails, because losing a
   *  completed generation to a failed GET is the one outcome worth avoiding
   *  more than an unnamed set. */
  const settle = async (fallback: GeneratedStory[]): Promise<TicketSetGenResult> => {
    let title = ""
    let stories = fallback
    let status = "ready"
    let convo = conversationId
    let sourceText = task
    try {
      const rec = await ticketSetsApi.get(setId)
      title = rec.title
      stories = rec.stories ?? fallback
      status = rec.status || "ready"
      convo = rec.conversation_id
      sourceText = rec.source_text || task
    } catch {
      /* keep what the job produced — an unnamed set beats a lost one */
    }
    if (status === "failed") return fail("failed", setId)
    const set: TicketSetSlice = {
      id: setId,
      title,
      stories,
      conversationId: convo,
      status,
      sourceText,
      stubs: [],
      progress: null,
      error: null,
    }
    setContent({ ticketSet: set, ticketSetGenerating: false })
    return { ok: true, set }
  }

  while (Date.now() - startedAt < MAX_MS) {
    try {
      if (pollTheRow) {
        const rec = await ticketSetsApi.get(setId)
        transportFailures = 0
        if (rec.status === "ready") return settle(rec.stories ?? [])
        if (rec.status === "failed") return fail("failed", setId)
      } else {
        const job = await storiesApi.getJob(start.job_id)
        transportFailures = 0
        if (job.status === "ready") return settle(job.stories ?? [])
        if (job.status === "failed") return fail("failed", setId)
        // Still generating: stream what exists. The planned roster lands
        // ~20-35s in, well before the first written ticket, so the panel can
        // show the whole set as skeleton rows long before any of it is real.
        if (job.stories?.length || job.stubs?.length || job.progress) {
          publishPartial(job.stories, job.stubs, job.progress)
        }
      }
    } catch (e) {
      if (e instanceof ApiError && e.status === 404) {
        if (pollTheRow) return fail("notfound", setId)
        // One immediate re-read against the row, then back onto the normal
        // cadence — the switch is not worth a poll interval's delay.
        pollTheRow = true
        continue
      }
      // A 4xx that isn't 404 is a verdict, not a blip: retrying it just delays
      // the same answer by six seconds.
      if (!isTransportFailure(e)) return fail("failed", setId)
      transportFailures += 1
      if (transportFailures >= MAX_TRANSPORT_FAILURES) {
        return fail("network", setId)
      }
    }
    await sleepUntilNextPoll(POLL_MS)
  }

  return fail("timeout", setId)
}
