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
 * `artifactTemplateId` is the uploaded TICKET format the user named, resolved
 * by the backend planner and carried on the intent envelope. Undefined — the
 * normal case — means the company's active ticket format. Forwarding it is what
 * makes "break this into tickets using our Acme format" honour the ask instead
 * of rendering in whichever format happens to be active.
 *
 * Resolves with the terminal outcome; it never throws. Callers that want to
 * toast a failure read `kind` — the raw backend message is deliberately not
 * returned, because nothing that came off the wire should reach the screen.
 */
export async function runTicketSetGeneration(
  task: string,
  conversationId: number | null,
  setContent: SetContent,
  artifactTemplateId?: string | null,
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
    start = await storiesApi.generateFromInsight(
      task, conversationId, artifactTemplateId,
    )
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
    let templateId: string | null = null
    let templateName: string | null = null
    try {
      const rec = await ticketSetsApi.get(setId)
      title = rec.title
      stories = rec.stories ?? fallback
      status = rec.status || "ready"
      convo = rec.conversation_id
      sourceText = rec.source_text || task
      templateId = rec.artifact_template_id ?? null
      templateName = rec.artifact_template_name ?? null
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
      artifactTemplateId: templateId,
      artifactTemplateName: templateName,
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

/**
 * Open an EXISTING set by id — the Artifacts library's row, and the thread
 * resume. The other half of the same ownership rule: this module is still the
 * only thing that writes `content.ticketSet`, so a screen opening a set never
 * has to know the slice's shape.
 *
 * A set that is still `generating` is picked up mid-run and polled to a
 * terminal state on the durable ROW (there is no job id on this path — the job
 * belongs to whoever kicked it off, possibly in another browser tab). That
 * polling is not optional: writing `ticketSetGenerating: true` and stopping
 * there is exactly the stale "Writing tickets…" over a run that finished an
 * hour ago that this feature must never show.
 *
 * A set mid-FORMAT-SWITCH (`relaying`) is followed the same way, and this is
 * the path that makes leaving the page safe: the switch runs server-side, so
 * coming back — in this tab, another tab, or tomorrow — has to either show the
 * new format or say the switch is still going. The difference from a
 * generation is that the tickets stay on screen throughout: they are the
 * previous format's, and every one of them is still readable.
 *
 * Never throws. A 404 — an unknown id, or another tenant's, deliberately
 * indistinguishable — lands the classified `notfound` kind, the same way a run
 * does, so the panel prints its own words and nothing off the wire.
 */
export async function loadTicketSet(
  setId: number,
  setContent: SetContent,
): Promise<TicketSetGenResult> {
  // Clear the previous artifact's slots on the SAME patch as the working
  // state, for the reason `ticketSetOpenScopePatch` exists: a PRD left in the
  // shared slot would put a PRD and an Evidence tab beside a set that has
  // neither, each opening another thread's document.
  setContent({
    ...ticketSetOpenScopePatch(),
    ticketSet: null,
    ticketSetGenerating: true,
  })

  const publish = (rec: {
    id: number
    title: string
    status: string
    stories: GeneratedStory[]
    conversation_id: number | null
    source_text: string
    artifact_template_id?: string | null
    artifact_template_name?: string | null
    relaying?: boolean
    relaying_into_name?: string | null
  }): TicketSetSlice => ({
    id: rec.id,
    title: rec.title,
    stories: rec.stories ?? [],
    conversationId: rec.conversation_id,
    status: rec.status || "ready",
    sourceText: rec.source_text,
    stubs: [],
    progress: null,
    error: null,
    artifactTemplateId: rec.artifact_template_id ?? null,
    artifactTemplateName: rec.artifact_template_name ?? null,
    relaying: !!rec.relaying,
    relayingIntoName: rec.relaying_into_name ?? null,
  })

  const fail = (kind: TicketSetFailureKind, known: TicketSetSlice | null): TicketSetGenResult => {
    setContent({
      ticketSet: known == null ? null : { ...known, status: "failed", error: kind },
      ticketSetGenerating: false,
    })
    return { ok: false, kind }
  }

  let rec: Awaited<ReturnType<typeof ticketSetsApi.get>>
  try {
    rec = await ticketSetsApi.get(setId)
  } catch (e) {
    if (e instanceof ApiError && e.status === 404) return fail("notfound", null)
    return fail(isTransportFailure(e) ? "network" : "failed", null)
  }

  let slice = publish(rec)
  const settled = (r: { status: string; relaying?: boolean }) =>
    r.status !== "generating" && !r.relaying
  if (settled(rec)) {
    if (rec.status === "failed") return fail("failed", slice)
    setContent({ ticketSet: slice, ticketSetGenerating: false })
    return { ok: true, set: slice }
  }

  // Still being written, or mid-format-switch. Show what already exists (a
  // resumed run may be half-way through its fan-out; a switch has the whole
  // previous set) and follow the row to its terminal state.
  //
  // `ticketSetGenerating` stays FALSE for a switch: it is the panel's "there
  // is nothing to look at yet" flag, and during a re-lay there very much is —
  // the tickets are on screen and usable. The slice's own `relaying` drives
  // the working strip instead.
  setContent({ ticketSet: slice, ticketSetGenerating: rec.status === "generating" })

  const startedAt = Date.now()
  let transportFailures = 0
  while (Date.now() - startedAt < MAX_MS) {
    await sleepUntilNextPoll(POLL_MS)
    try {
      const next = await ticketSetsApi.get(setId)
      transportFailures = 0
      slice = publish(next)
      if (next.status === "failed") return fail("failed", slice)
      if (settled(next)) {
        setContent({ ticketSet: slice, ticketSetGenerating: false })
        return { ok: true, set: slice }
      }
      // Partial fan-out, or a switch still re-laying: republish so tickets
      // appear as they are written and the strip tracks the live state.
      setContent({
        ticketSet: slice,
        ticketSetGenerating: next.status === "generating",
      })
    } catch (e) {
      // The row is durable — a 404 here means it is genuinely gone, not that an
      // in-memory job was dropped, so there is nothing left to fall back to.
      if (e instanceof ApiError && e.status === 404) return fail("notfound", slice)
      if (!isTransportFailure(e)) return fail("failed", slice)
      transportFailures += 1
      if (transportFailures >= MAX_TRANSPORT_FAILURES) return fail("network", slice)
    }
  }
  return fail("timeout", slice)
}

/**
 * Follow a standalone set's in-flight FORMAT SWITCH to completion, without
 * blanking what is on screen.
 *
 * `loadTicketSet` clears the slice first, which is right when opening a set
 * (whatever was there belongs to another artifact) and wrong here: the tickets
 * being re-laid are already rendered, they stay valid the whole time, and
 * flashing them away would make a background job look like a reload. So this
 * marks the slice `relaying` in place, polls the durable row, and republishes
 * when it lands.
 *
 * Resolves true when the switch finished, false when it was lost (transport,
 * timeout, a deleted set). The caller owns the words either way — nothing off
 * the wire is returned.
 */
export async function followTicketSetSwitch(
  setId: number,
  setContent: SetContent,
  current: TicketSetSlice,
  intoName: string | null,
): Promise<boolean> {
  // Optimistic, and deliberately not conditional on a read: the POST has
  // already returned, so the switch IS running, and waiting a poll interval to
  // say so would leave the click looking ignored.
  setContent({
    ticketSet: { ...current, relaying: true, relayingIntoName: intoName },
  })

  const startedAt = Date.now()
  let transportFailures = 0
  while (Date.now() - startedAt < MAX_MS) {
    await sleepUntilNextPoll(POLL_MS)
    try {
      const next = await ticketSetsApi.get(setId)
      transportFailures = 0
      if (next.relaying) continue
      setContent({
        ticketSet: {
          ...current,
          stories: next.stories ?? current.stories,
          status: next.status || current.status,
          artifactTemplateId: next.artifact_template_id ?? null,
          artifactTemplateName: next.artifact_template_name ?? null,
          relaying: false,
          relayingIntoName: null,
        },
      })
      return true
    } catch (e) {
      if (e instanceof ApiError && e.status === 404) break
      if (!isTransportFailure(e)) break
      transportFailures += 1
      if (transportFailures >= MAX_TRANSPORT_FAILURES) break
    }
  }
  // Give up watching, but never leave the strip spinning over a switch nobody
  // is following any more — the row is the truth and the next open re-reads it.
  setContent({ ticketSet: { ...current, relaying: false, relayingIntoName: null } })
  return false
}
