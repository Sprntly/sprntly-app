"use client"

/**
 * Goal Analysis — the run panel. (Engine name Crucible; that word never
 * appears on screen.)
 *
 * WHAT THIS SURFACE IS FOR, and why it is shaped this way:
 *
 *  - A run STOPS and asks what the goal means. That is not a loading state and
 *    it must not look like one. It is the product's central claim — that the
 *    analysis is about the thing you actually meant — so the question gets the
 *    panel, prefilled and editable.
 *  - A finished run is a DOCUMENT, not a list of chips — see
 *    `GoalAnalysisReport`, which owns every rule about how a finding, its
 *    provenance, the rejections and the run's own limits are rendered. This
 *    file owns the STATE MACHINE and nothing else; two components rendering a
 *    finding is how the "never render an unsized finding as 0" rule ends up
 *    being true in one of them.
 *
 * FOUR STATES, and only two of them are waiting on a machine:
 *
 *   awaiting_confirmation  what does this goal MEAN? (the I9 gate)
 *   awaiting_approval      here is what I will read and what I cannot answer —
 *                          drop a source, tell me what you already believe,
 *                          then say go
 *   running                the only real loading state
 *   ready                  the REPORT — a document, not a list
 *
 * Both gates are terminal for the poller: nothing but a click moves them, and
 * a click has to RE-ARM the poll (`pollKey`), or the panel sits on the last
 * status it saw while the run finishes on the server. That bug shipped once
 * already for confirm; approve is wired the same way for the same reason.
 */
import { useCallback, useEffect, useRef, useState } from "react"
import {
  goalAnalysisApi,
  type GoalReportDoc,
  type GoalRunDetail,
  apiErrorMessage,
} from "../../lib/api"
import { GoalAnalysisReport } from "./GoalAnalysisReport"
import { GoalRunNarration } from "./GoalRunNarration"
import { GoalReportDocument } from "./GoalReportDocument"

/** How often to poll a live run. A run is minutes long, so a tight poll buys
 *  nothing but load; the row is durable, so a missed tick costs nothing. */
const POLL_MS = 3000

/** Consecutive failed polls before the panel stops trying. Three, because a
 *  deploy restart drops one or two ticks and the run itself survives it. */
const MAX_CONSECUTIVE_FAILURES = 3

/** Statuses that will never change on their own. `awaiting_confirmation` is
 *  here because only a click moves it — but the click has to RE-ARM the poll,
 *  which is what `pollKey` below is for. Getting that wrong meant every user
 *  confirmed and then watched "Reading 0 claims…" forever while the run
 *  finished on the server. */
const TERMINAL = new Set([
  "ready", "failed", "cancelled",
  // BOTH human gates. A run waiting on a person will wait forever, and polling
  // it is load with no possible new information; the click that releases it
  // re-arms the poll itself.
  "awaiting_confirmation", "awaiting_approval",
])

/** Error codes the backend may return, in the user's language. Anything not
 *  listed falls through to a generic line — the raw `error` is never sent. */
const ERROR_COPY: Record<string, string> = {
  no_evidence:
    "There is nothing connected yet for this to read. Connect a source and try again.",
  goal_unresolved: "We could not work out which metric this goal is about.",
  interrupted: "This run stopped before it finished. Nothing was saved from it.",
  cancelled: "This run was cancelled.",
  llm_error: "A model call failed partway through.",
  internal: "Something went wrong on our side partway through this run.",
}

/** The server's own explanation, when it gave one — else ''.

 *  Parsing is delegated to `apiErrorMessage`, which already understands
 *  FastAPI's `detail` in both forms (a plain string, and the validation-error
 *  list). I had hand-rolled only the string case.
 *
 *  Two deliberate choices:
 *
 *  - Duck-typed on `body`, NOT `instanceof ApiError`. The class would couple
 *    this to one module instance, and an error that crosses a boundary — or
 *    arrives from a test double — still carries a perfectly good `detail` that
 *    the user deserves to read.
 *  - `apiErrorMessage` falls back to "Request failed (413)" when there is no
 *    detail at all. That is worse than what each caller already says, so it is
 *    filtered back out to '' and the caller's own sentence wins.
 */
function _detailOf(e: unknown): string {
  const body = (e as { body?: unknown })?.body
  if (body === null || body === undefined) return ""
  const status = (e as { status?: unknown })?.status
  const code = typeof status === "number" ? status : 0
  const msg = apiErrorMessage(code, body)
  if (!msg || msg === `Request failed (${code})`) return ""
  return msg.trim()
}

export function GoalAnalysisTab({ runId }: { runId: number }) {
  const [run, setRun] = useState<GoalRunDetail | null>(null)
  const [error, setError] = useState<string | null>(null)
  // Bumped by confirm to restart the poll. `load` is keyed on `runId`, which
  // has not changed, so without this the effect never re-runs and the panel
  // stays on the last status it saw.
  const [pollKey, setPollKey] = useState(0)
  // How many consecutive polls failed. One 502 on one tick of a multi-minute
  // run must not brick the panel — a transient error is a retry, not a state.
  const failures = useRef(0)
  // The user's edit must survive a poll landing underneath it. Without this
  // the textarea is reset every three seconds and a long definition is
  // impossible to type.

  // ── The report as a document ────────────────────────────────────────────
  //
  // The run is immutable; the report is a document ABOUT the run. `doc` is
  // that document once one exists, `editing` is whether it is what the panel
  // is currently showing.
  //
  // A DETACHED REPORT OPENS ITSELF. If someone has edited it, the read-only
  // view would render the RUN's findings — not their words — which is the one
  // outcome this whole feature exists to prevent: their edit would look like
  // it had been thrown away. So a detached document takes the panel on load,
  // once (`autoOpened`), leaving the user free to go back to the analysis
  // afterwards without the effect dragging them forward again.
  const [doc, setDoc] = useState<GoalReportDoc | null>(null)
  const [editing, setEditing] = useState(false)
  const [docBusy, setDocBusy] = useState(false)
  const [docNote, setDocNote] = useState<string | null>(null)
  const autoOpened = useRef(false)

  const load = useCallback(async () => {
    try {
      const detail = await goalAnalysisApi.get(runId)
      failures.current = 0
      setError(null)              // a recovered poll clears the warning
      setRun(detail)
      return detail.status
    } catch {
      failures.current += 1
      if (failures.current >= MAX_CONSECUTIVE_FAILURES) {
        setError("Lost contact with this analysis. It may still be running.")
        return "failed"           // stop polling; the run row survives
      }
      // SAY SO ON THE FIRST FAILURE, not only on the last. Staying silent
      // until we give up meant there was no state in which the warning was
      // visible AND polling was still going — so "a recovered poll clears the
      // warning" was untestable by construction, and the user watched a stale
      // panel with no hint that anything was wrong.
      setError("Lost contact — retrying…")
      // Keep polling. Returning a non-terminal status is the point: a single
      // blip during a run that takes minutes is not a reason to give up on it.
      return "running"
    }
  }, [runId])

  useEffect(() => {
    let live = true
    let timer: ReturnType<typeof setTimeout> | undefined
    const tick = async () => {
      const status = await load()
      if (!live || TERMINAL.has(String(status))) return
      timer = setTimeout(tick, POLL_MS)
    }
    void tick()
    return () => {
      live = false
      if (timer) clearTimeout(timer)
    }
  }, [load, pollKey])

  // `confirm` and `approve` lived here and are gone with the gates they served:
  // both are answered in the chat thread now (`GoalGateCard`), and the 422/413
  // vs lost-response distinction they carried moved to `confirmGoalDefinition` /
  // `approveGoalPlan` in ChatScreen with its reasoning intact. Leaving them
  // here would have been ~80 lines nothing could reach.

  // Load the report document, if this run already has one. GATED ON
  // `artifact_id` rather than fired unconditionally: most runs never have a
  // document, and a request per ready run to be told 404 is load bought with
  // nothing. The id rides the run row, so it costs no extra read to know.
  const artifactId = run?.artifact_id ?? null
  useEffect(() => {
    if (artifactId == null) return
    let live = true
    void (async () => {
      try {
        const fresh = await goalAnalysisApi.document(runId)
        if (!live) return
        setDoc(fresh)
        if (fresh.detached && !autoOpened.current) {
          autoOpened.current = true
          setEditing(true)
        }
      } catch {
        // A report that will not load is a missing document, not a broken
        // analysis. The findings are still on screen, which is the part that
        // matters, and the Edit button will try again.
      }
    })()
    return () => { live = false }
  }, [runId, artifactId])

  const openDocument = useCallback(async () => {
    if (docBusy) return
    setDocBusy(true)
    setDocNote(null)
    try {
      // IDEMPOTENT ON THE SERVER, which is why this is safe to call whether or
      // not a document already exists: it returns the FIRST one, edits and
      // all, rather than re-rendering over them.
      const fresh = await goalAnalysisApi.createDocument(runId)
      setDoc(fresh)
      setEditing(true)
    } catch (e) {
      // SURFACE THE SERVER'S SENTENCE. A bare `catch` with a fixed string made
      // the 413 invisible: the server explains that the report is too large
      // and that the RUN is unaffected, and the user saw "we could not open
      // this report" — which reads as "your analysis is broken". The whole
      // point of returning a reason is that somebody sees it.
      setDocNote(_detailOf(e) || "We could not open this report for editing.")
    } finally {
      setDocBusy(false)
    }
  }, [runId, docBusy])

  const saveCopy = useCallback(async () => {
    if (docBusy) return
    setDocBusy(true)
    setDocNote(null)
    try {
      await goalAnalysisApi.forkDocument(runId)
      setDocNote(
        "Saved as a separate document in your team library. This report is " +
        "unchanged.",
      )
    } catch (e) {
      // Same reasoning as the edit path above, and the same 413: `_body_or_413`
      // guards BOTH writers, so a report too large to open is also too large to
      // fork. Surfacing the reason in one handler and swallowing it in the
      // other would just move the silence.
      setDocNote(_detailOf(e) || "We could not save a copy of this report.")
    } finally {
      setDocBusy(false)
    }
  }, [runId, docBusy])

  const backToRun = useCallback(() => {
    setEditing(false)
    // RE-READ ON THE WAY OUT. The user may have just typed into the document,
    // which detaches it — and the read-only view behind this button needs to
    // know, or it offers "Edit" on a report that has already been edited and
    // says nothing about the version holding their words.
    //
    // THIS CAN RACE THE AUTOSAVE, and that is survivable rather than fixed.
    // Leaving the editor blurs it, which flushes a pending save, but the flush
    // and this read are two requests with no ordering between them: land the
    // read first and the report still looks untouched for one render. The cost
    // is one missing line, not lost text — the document holds their words
    // either way, and "Edit" reopens THAT document (the create endpoint is
    // idempotent and returns the stored body), so the way back never depends
    // on this read having won.
    void goalAnalysisApi.document(runId).then(setDoc).catch(() => {})
  }, [runId])

  if (error && !run) return <p className="ga-error">{error}</p>
  if (!run) return <p className="ga-loading">Loading…</p>

  // A BANNER, not a replacement. Making `error` short-circuit the whole panel
  // threw away a loaded run to show one line; making it unreachable once a run
  // existed was the opposite mistake and left the user with a frozen panel and
  // no explanation. It sits above whatever the run last showed.
  const banner = error ? (
    <p className="ga-error" role="status" data-testid="goal-error">{error}</p>
  ) : null

  // ── The gates live in the CHAT THREAD now ────────────────────────────────
  //
  // Both are a conversation — what does this goal mean, and here is what I will
  // read — so they are answered in the conversation, as `GoalGateCard` turns.
  // The panel keeps the finished report, which is a document.
  //
  // RENDERING THEM HERE TOO IS NOT A HARMLESS DUPLICATE. The same gate would
  // carry two live Confirm buttons on one screen; answering in the panel leaves
  // the thread card open on a question that has already been answered, and its
  // button then 409s against a run that has moved on.
  if (run.status === "awaiting_confirmation" || run.status === "awaiting_approval") {
    return (
      <div className="ga" data-testid="goal-gate-in-thread">
        {banner}
        <p className="ga-goal">{run.goal_text}</p>
        <p className="ga-ask">
          {run.status === "awaiting_confirmation"
            ? "Confirm what this goal means in the chat to start the analysis."
            : "Approve the plan in the chat to start reading."}
        </p>
      </div>
    )
  }

  if (run.status === "failed") {
    return (
      <div className="ga" data-testid="goal-failed">
        {banner}
        <p className="ga-goal">{run.goal_text}</p>
        <p className="ga-error">
          {ERROR_COPY[run.error_code ?? ""] || "This run did not finish."}
        </p>
      </div>
    )
  }

  if (run.status !== "ready") {
    // THE RUN NARRATES ITSELF WHERE IT CAN. `progress` is written as the run
    // decides, so this fills in over the minutes rather than sitting on one
    // sentence. It is absent on a run that has only just started and on every
    // run that finished before narration shipped, and the old line is the
    // honest fallback for both — never a funnel of zeroes.
    const progress = run.prioritisation?.progress
    return (
      <div className="ga" data-testid="goal-running">
        {banner}
        <p className="ga-goal">{run.goal_text}</p>
        {progress ? (
          <GoalRunNarration progress={progress} />
        ) : (
          <p className="ga-loading">
            Reading {run.claim_count || 0} claims…
          </p>
        )}
      </div>
    )
  }

  // THE FUNNEL SURVIVES THE RUN. The gap between the final progress write and
  // `status="ready"` is about a second against a 3s poll, so a reader who could
  // only see this live would usually see nothing — the drop rows are the whole
  // feature. `progress` is durable in `prioritisation`, so the finished report
  // can say how its ranking was narrowed instead of asking to be taken on
  // faith, which is the post-hoc-disclosure problem this work exists to end.
  //
  // THE TAB ONLY. The edited-document branch and the exported report still
  // carry no funnel, so a reader who is handed the DOCUMENT is back to taking
  // the ranking on authority. Stated rather than implied, because the previous
  // version of this comment claimed the report reprinted the funnel when
  // nothing did, and the next reader would have built on it.
  const readyProgress = run.prioritisation?.progress
  const howItNarrowed =
    readyProgress && readyProgress.step === "done" ? (
      <details className="ga-narration-recap" data-testid="goal-narration-recap">
        <summary>How this was narrowed</summary>
        <GoalRunNarration progress={readyProgress} />
      </details>
    ) : null

  const note = docNote ? (
    <p className="ga-doc-note" role="status" data-testid="goal-doc-note">
      {docNote}
    </p>
  ) : null

  if (editing && doc) {
    return (
      <div className="ga" data-testid="goal-ready">
        {banner}
        {note}
        <GoalReportDocument
          doc={doc}
          onBack={backToRun}
          onSaveCopy={saveCopy}
          busy={docBusy}
        />
      </div>
    )
  }

  return (
    <div className="ga" data-testid="goal-ready">
      {banner}
      {note}
      {/* An edited version exists and the reader is looking at the original.
          Saying so is not optional: without it the panel shows the run's own
          findings with an "Edit" button, and the document holding someone's
          rewrite is unreachable and invisible. */}
      {doc?.detached ? (
        <p className="ga-doc-note" data-testid="goal-report-has-edit">
          An edited version of this report exists.{" "}
          <button
            type="button"
            className="ga-doc-action"
            data-testid="goal-report-open-edited"
            onClick={() => setEditing(true)}
          >
            Open it
          </button>
        </p>
      ) : null}
      <GoalAnalysisReport
        run={run}
        editable
        onEdit={openDocument}
        onSaveCopy={saveCopy}
        busy={docBusy}
      />
      {howItNarrowed}
    </div>
  )
}

export default GoalAnalysisTab
